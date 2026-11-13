"""The MCP server for one repository: `repo-brief serve`.

It offers repo-brief's own tools, one tool per script group this client may use,
`repo_ask`, and the overview and map as resources. What a client may use follows
the fixed rules in `catalog` (SPEC.md, "Tool groups"), narrowed by `--groups`.
Over HTTP, the Host header is checked and a bearer token can be required; off
loopback it must be. Needs `repo-brief[mcp]`.
"""
import hmac
import os
import sys
import time
from pathlib import Path

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .. import catalog, facts as facts_mod, log as log_mod, overview, trust
from . import ask as ask_mod
from . import settings as settings_mod
from . import tools as tools_mod

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
RUNS_PROJECT = ToolAnnotations(read_only_hint=False, open_world_hint=False)
ASK_DESCRIPTION = (
    "Ask a read-only agent a question about this repository. It investigates with "
    "repo-brief's tools and the repo's files, and returns an answer with path:line "
    "references, the gaps it could not close, and a session id: pass it back as "
    "`session_id` to ask a follow-up. `audience` is `developer` or `product`. Takes "
    "seconds to minutes; progress is reported per tool call."
)
# Claude Code keeps the first 4,096 characters of a server's instructions, and
# the rest of ours stays under 1,500.
WRITTEN_LIMIT = 2500


def _client_name(ctx) -> str:
    try:
        return ctx.session.client_params.client_info.name or "mcp"
    except AttributeError:
        return "mcp"


def _client_session(ctx) -> str:
    """The client's own session id, when it sends one.

    OpenCode puts `ai.opencode/sessionID` in every request's `_meta`; a client
    that names no session gets none, which is the honest answer.
    """
    try:
        meta = dict(ctx.meta or {})
    except (TypeError, ValueError):
        return ""
    for key, value in meta.items():
        if isinstance(value, str) and str(key).lower().endswith(("sessionid", "session_id")):
            return value
    return ""


def _result_size(result) -> int:
    if isinstance(result, dict):
        return sum(
            len(str(part.get("text") or ""))
            for part in result.get("content") or []
            if isinstance(part, dict)
        )
    return 0


class LogCalls:
    """Record every tool a client calls, when the log is on.

    The client's calls are the other half of the record: what the orchestrator
    asked for before it asked an agent, and how often. A failure to log never
    reaches the caller.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    async def __call__(self, ctx, call_next):
        if ctx.method != "tools/call" or not log_mod.enabled(self.root):
            return await call_next(ctx)
        params = dict(ctx.params or {})
        tool = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        started = time.monotonic()

        def record(result=None, is_error=False):
            log_mod.record_call(
                self.root,
                tool,
                arguments,
                client=_client_name(ctx),
                client_session=_client_session(ctx),
                result_bytes=_result_size(result),
                duration_ms=int((time.monotonic() - started) * 1000),
                is_error=is_error,
            )

        try:
            result = await call_next(ctx)
        except Exception:
            record(is_error=True)
            raise
        record(result, bool(isinstance(result, dict) and result.get("isError")))
        return result


def _instructions(root: Path, trusted: bool, groups: list, ask: bool = True) -> str:
    lines = [
        "repo-brief server for the repository %r." % root.name,
        overview.short(root),
        "repo_context finds where something lives and returns a read plan; repo_graph "
        "shows how the areas depend on each other; repo_dependents finds the usages of a "
        "class, for what uses it and what a change to it reaches; repo_tools lists the "
        "repo's scripts; "
        "repo_facts searches the facts the adapter extracted and repo_fact opens one.",
    ]
    if ask:
        lines.append("repo_ask hands a question that needs an investigation to a read-only agent.")
    lines.append("Resources: repo://overview and repo://map.")
    if groups:
        lines.append(
            "Script tools: %s." % ", ".join("repo_%s (%s)" % (group, group) for group in groups)
        )
    if not trusted:
        lines.append(
            "This repository is not trusted, so its scripts do not run here; "
            "`repo-brief trust` allows them."
        )
    written = overview.written(root)
    if len(written) > WRITTEN_LIMIT:
        written = written[:WRITTEN_LIMIT].rsplit("\n", 1)[0] + "\n\n(The rest is in repo://overview.)"
    if written:
        lines.append("From the overview's written section:\n\n" + written)
    return "\n\n".join(line for line in lines if line)


def build_server(
    root: Path, adapter, trusted=None, groups=None, toolbox="full", ask=True
) -> MCPServer:
    """The server, without a transport. `groups` narrows the script groups.

    `toolbox="ask"` builds the smaller server an `ask` runner hands its agent:
    repo-brief's read-only tools and, in a trusted repository, the `inspect`
    scripts. It has no `repo_ask` (no agents inside agents), no dev or debug
    scripts, and no client-call logging, since its calls belong to the ask.

    `ask=False` leaves `repo_ask` out of the full server. A client that can
    search for itself does not need a second agent, and the choice is measured
    rather than assumed.
    """
    root = Path(root)
    trusted = trust.is_trusted(root) if trusted is None else trusted
    if toolbox == "ask":
        return _agent_toolbox(root, adapter, trusted)
    narrowed = None if groups is None else set(groups) | {"brief"}
    allowed = catalog.allowed_groups(trusted, narrowed=narrowed)
    settings = settings_mod.load(root)
    script_groups = [
        group for group in tools_mod.SCRIPT_GROUPS
        if group in allowed and tools_mod.scripts_in(root, group)
    ]
    server = MCPServer(
        root.name,
        instructions=_instructions(root, trusted, script_groups, ask),
        middleware=[LogCalls(root)],
    )

    @server.tool(description=tools_mod.CONTEXT_DESCRIPTION, annotations=READ_ONLY)
    def repo_context(query: str) -> str:
        return tools_mod.guard(
            "repo_context", lambda: tools_mod.context_text(root, adapter, query)
        )

    @server.tool(description=tools_mod.GRAPH_DESCRIPTION, annotations=READ_ONLY)
    def repo_graph(area: str = "", task: str = "") -> str:
        return tools_mod.guard(
            "repo_graph", lambda: tools_mod.graph_text(root, adapter, area=area, task=task)
        )

    @server.tool(description=tools_mod.DEPENDENTS_DESCRIPTION, annotations=READ_ONLY)
    def repo_dependents(name: str) -> str:
        return tools_mod.guard(
            "repo_dependents", lambda: tools_mod.dependents_text(root, adapter, name)
        )

    @server.tool(
        description="The repository's scripts by group, and which groups this client may run.",
        annotations=READ_ONLY,
    )
    def repo_tools() -> str:
        return tools_mod.catalog_text(root, trusted, groups=narrowed)

    @server.tool(description=tools_mod.FACTS_DESCRIPTION, annotations=READ_ONLY)
    def repo_facts(query: str = "", kind: str = "", limit: int = 15) -> str:
        return tools_mod.facts_text(root, adapter, query, kind, limit)

    @server.tool(description=tools_mod.FACT_DESCRIPTION, annotations=READ_ONLY)
    def repo_fact(id: str) -> str:
        return tools_mod.fact_text(root, adapter, id)

    for group in script_groups:
        _add_group_tool(server, root, group, trusted, settings.script_timeout_seconds)

    if ask:
        @server.tool(description=ASK_DESCRIPTION, annotations=READ_ONLY)
        async def repo_ask(
            question: str, ctx: Context, audience: str = "", session_id: str = ""
        ) -> str:
            async def progress(step: int, message: str) -> None:
                try:
                    await ctx.report_progress(step, None, message)
                except Exception:  # a client without progress support still gets the answer
                    pass

            try:
                result = await ask_mod.run(
                    root,
                    adapter,
                    question,
                    audience=audience or None,
                    session_id=session_id or None,
                    client=_client_name(ctx),
                    trusted=trusted,
                    groups=narrowed,
                    on_progress=progress,
                )
            except ask_mod.RunnerUnavailable as exc:
                return "ask is not available: %s." % exc
            return result.render()

    @server.resource("repo://overview", mime_type="text/markdown")
    def repo_overview() -> str:
        return tools_mod.overview_text(root) or "No overview yet; run `repo-brief update`."

    @server.resource("repo://map", mime_type="text/markdown")
    def repo_map() -> str:
        return tools_mod.map_text(root) or "No map yet; run `repo-brief update`."

    return server


TOOLBOX_INSTRUCTIONS = (
    "repo-brief's tools for an `ask` agent: context, graph, the overview, the adapter's "
    "facts, and this repository's read-only scripts. There is no ask tool and no dev or "
    "debug script."
)
ANSWER_DESCRIPTION = (
    "Record your final answer. Call this once, last: `answer` is the Markdown answer "
    "your instructions ask for; `references` names each file it relies on with the "
    "lines that support it; `gaps` lists what the repository could not establish."
)


def _agent_toolbox(root: Path, adapter, trusted: bool) -> MCPServer:
    """The tool set `tools.agent_specs` describes, plus the answer tool.

    The names must match `agent_specs` exactly; a test compares the two lists.
    The functions are written out because MCP builds each tool's schema from the
    signature, including defaults.
    """
    from pydantic import BaseModel

    class Reference(BaseModel):
        path: str
        lines: str = ""
        note: str = ""

    server = MCPServer(root.name, instructions=TOOLBOX_INSTRUCTIONS)

    @server.tool(description=tools_mod.CONTEXT_DESCRIPTION, annotations=READ_ONLY)
    def repo_brief_context(query: str) -> str:
        return tools_mod.context_text(root, adapter, query, facts_tool="repo_brief_facts")

    @server.tool(description=tools_mod.GRAPH_DESCRIPTION, annotations=READ_ONLY)
    def repo_brief_graph(area: str = "") -> str:
        return tools_mod.graph_text(root, adapter, area=area)

    @server.tool(description=tools_mod.DEPENDENTS_DESCRIPTION, annotations=READ_ONLY)
    def repo_brief_dependents(name: str) -> str:
        return tools_mod.dependents_text(root, adapter, name)

    @server.tool(description=tools_mod.OVERVIEW_DESCRIPTION, annotations=READ_ONLY)
    def repo_brief_overview() -> str:
        return tools_mod.overview_text(root) or "No overview yet; run `repo-brief update`."

    if facts_mod.available(adapter):

        @server.tool(description=tools_mod.FACTS_DESCRIPTION, annotations=READ_ONLY)
        def repo_brief_facts(query: str = "", kind: str = "") -> str:
            return tools_mod.facts_text(root, adapter, query, kind, fact_tool="repo_brief_fact")

        @server.tool(description=tools_mod.FACT_DESCRIPTION, annotations=READ_ONLY)
        def repo_brief_fact(id: str) -> str:
            return tools_mod.fact_text(root, adapter, id)

    if trusted and tools_mod.scripts_in(root, "inspect"):
        timeout = settings_mod.load(root).script_timeout_seconds

        @server.tool(description=tools_mod.group_description(root, "inspect"), annotations=READ_ONLY)
        def repo_brief_inspect(script: str, args: str = "") -> str:
            return tools_mod.run_script(root, "inspect", script, args, trusted=trusted, timeout=timeout)

    @server.tool(description=ANSWER_DESCRIPTION, annotations=READ_ONLY)
    def repo_brief_answer(answer: str, references: list[Reference] = [], gaps: list[str] = []) -> str:
        return "Recorded; stop here."

    return server


def _add_group_tool(server: MCPServer, root: Path, group: str, trusted: bool, timeout: float) -> None:
    def run(script: str, args: str = "") -> str:
        return tools_mod.run_script(root, group, script, args, trusted=trusted, timeout=timeout)

    server.tool(
        name="repo_%s" % group,
        description=tools_mod.group_description(root, group),
        annotations=READ_ONLY if group == "inspect" else RUNS_PROJECT,
    )(run)


# -- HTTP -----------------------------------------------------------------

LOOPBACK = ("127.0.0.1", "localhost", "::1")
TOKEN_ENV = "REPO_BRIEF_HTTP_TOKEN"
HTTP_PATH = "/mcp"


class HttpRefused(Exception):
    """`serve --http` was asked for something unsafe."""


def _bracketed(name: str) -> str:
    # An IPv6 literal appears in a Host header as `[::1]:8765`.
    return "[%s]" % name if name.count(":") >= 2 and not name.startswith("[") else name


def http_security(host: str, allowed_hosts=()) -> TransportSecuritySettings:
    """Which Host and Origin headers a request may carry.

    Always loopback, the address the server binds to (unless it binds to every
    address), and each `--allowed-host`, with or without a port. Anything else
    is refused, which is what stops a web page from reaching the server through
    DNS rebinding.
    """
    names = list(LOOPBACK)
    if host not in ("0.0.0.0", "::", ""):
        names.append(host)
    names += [name.strip() for name in allowed_hosts if name.strip()]
    hosts, origins = [], []
    for name in dict.fromkeys(names):
        bare = _bracketed(name)
        hosts += [bare, bare + ":*"]
        origins += ["%s://%s%s" % (scheme, bare, port) for scheme in ("http", "https") for port in ("", ":*")]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


class BearerToken:
    """Refuse every HTTP request without `Authorization: Bearer <token>`."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)  # lifespan events pass through
            return
        given = b""
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                given = value
        scheme, _, credentials = given.partition(b" ")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(credentials.strip(), self.token):
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"www-authenticate", b'Bearer realm="repo-brief"'),
                ],
            })
            await send({"type": "http.response.body", "body": b"Missing or wrong bearer token.\n"})
            return
        await self.app(scope, receive, send)


def http_app(server: MCPServer, host: str, allowed_hosts=(), token: str = ""):
    """The ASGI app `serve --http` runs, with every check SPEC.md asks for.

    A token is optional on loopback and required anywhere else: a server bound
    to a reachable address would otherwise run scripts for anyone who finds it.
    """
    if not token and host not in LOOPBACK:
        raise HttpRefused(
            "serving on %s reaches other machines: set %s to a secret first, and send it "
            "as `Authorization: Bearer <secret>`" % (host, TOKEN_ENV)
        )
    app = server.streamable_http_app(
        streamable_http_path=HTTP_PATH,
        transport_security=http_security(host, allowed_hosts),
        host=host,
    )
    return BearerToken(app, token) if token else app


def run(
    root: Path,
    adapter,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    groups=None,
    allowed_hosts=(),
    toolbox="full",
    ask=True,
) -> None:
    server = build_server(root, adapter, groups=groups, toolbox=toolbox, ask=ask)
    if transport != "http":
        server.run("stdio")
        return
    import uvicorn

    token = os.environ.get(TOKEN_ENV, "")
    app = http_app(server, host, allowed_hosts, token)
    print(
        "repo-brief: serving %s on http://%s:%d%s (%s)"
        % (root.name, _bracketed(host), port, HTTP_PATH, "bearer token required" if token else "no token, loopback only"),
        file=sys.stderr,
    )
    uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning")).run()
