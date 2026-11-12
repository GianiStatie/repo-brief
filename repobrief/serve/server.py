"""The MCP server for one repository: `repo-brief serve`.

It offers repo-brief's own tools, one tool per script group this client may use,
`repo_ask`, and the overview and map as resources. What a client may use follows
the fixed rules in `catalog` (SPEC.md, "Tool groups"), narrowed by `--groups`.
Needs `repo-brief[mcp]`.
"""
from pathlib import Path

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from .. import catalog, overview, trust
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


def _client_name(ctx) -> str:
    try:
        return ctx.session.client_params.client_info.name or "mcp"
    except AttributeError:
        return "mcp"


def _instructions(root: Path, trusted: bool, groups: list) -> str:
    lines = [
        "repo-brief server for the repository %r." % root.name,
        overview.short(root),
        "repo_context finds where something lives and returns a read plan; repo_graph "
        "shows how the areas depend on each other; repo_tools lists the repo's scripts; "
        "repo_ask hands a question that needs an investigation to a read-only agent. "
        "Resources: repo://overview and repo://map.",
    ]
    if groups:
        lines.append(
            "Script tools: %s." % ", ".join("repo_%s (%s)" % (group, group) for group in groups)
        )
    if not trusted:
        lines.append(
            "This repository is not trusted, so its scripts do not run here; "
            "`repo-brief trust` allows them."
        )
    return "\n\n".join(line for line in lines if line)


def build_server(root: Path, adapter, trusted=None, groups=None) -> MCPServer:
    """The server, without a transport. `groups` narrows the script groups."""
    root = Path(root)
    trusted = trust.is_trusted(root) if trusted is None else trusted
    narrowed = None if groups is None else set(groups) | {"brief"}
    allowed = catalog.allowed_groups(trusted, narrowed=narrowed)
    settings = settings_mod.load(root)
    script_groups = [
        group for group in tools_mod.SCRIPT_GROUPS
        if group in allowed and tools_mod.scripts_in(root, group)
    ]
    server = MCPServer(root.name, instructions=_instructions(root, trusted, script_groups))

    @server.tool(description=tools_mod.CONTEXT_DESCRIPTION, annotations=READ_ONLY)
    def repo_context(query: str) -> str:
        return tools_mod.context_text(root, adapter, query)

    @server.tool(description=tools_mod.GRAPH_DESCRIPTION, annotations=READ_ONLY)
    def repo_graph(area: str = "", task: str = "") -> str:
        return tools_mod.graph_text(root, adapter, area=area, task=task)

    @server.tool(
        description="The repository's scripts by group, and which groups this client may run.",
        annotations=READ_ONLY,
    )
    def repo_tools() -> str:
        return tools_mod.catalog_text(root, trusted, groups=narrowed)

    for group in script_groups:
        _add_group_tool(server, root, group, trusted, settings.script_timeout_seconds)

    @server.tool(description=ASK_DESCRIPTION, annotations=READ_ONLY)
    async def repo_ask(question: str, ctx: Context, audience: str = "", session_id: str = "") -> str:
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


def _add_group_tool(server: MCPServer, root: Path, group: str, trusted: bool, timeout: float) -> None:
    def run(script: str, args: str = "") -> str:
        return tools_mod.run_script(root, group, script, args, trusted=trusted, timeout=timeout)

    server.tool(
        name="repo_%s" % group,
        description=tools_mod.group_description(root, group),
        annotations=READ_ONLY if group == "inspect" else RUNS_PROJECT,
    )(run)


def run(root: Path, adapter, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765, groups=None) -> None:
    server = build_server(root, adapter, groups=groups)
    if transport == "http":
        server.run("streamable-http", host=host, port=port)
    else:
        server.run("stdio")
