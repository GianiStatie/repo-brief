"""One MCP server per repository, plus the agent `ask` runs.

Two faces, one set of facts:

- The MCP server exposes repo-brief's deterministic tools to an orchestrator:
  `context`, `graph`, the overview and map as resources, and one tool per
  exposed group of the repo's own scripts.
- `ask` runs a read-only Claude Agent SDK session over the repo. It gets the
  same tools in-process, and never `dev` or `debug`: those belong to the
  orchestrator and only when the repo is trusted.

Optional dependencies: `repo-brief[mcp]` for the server, `repo-brief[agent]`
for `ask`. The rest of repo-brief does not import either.
"""
from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import catalog, graph as graph_mod, kernel, log as log_mod, overview, render, trust
from . import index as index_mod

SERVER_NAME = "repo"
CLIP = 12_000
SCRIPT_TIMEOUT = 600
ASK_TIMEOUT = 600
MODEL = os.environ.get("REPO_BRIEF_MODEL", "claude-haiku-5-5")
MAX_TURNS = int(os.environ.get("REPO_BRIEF_MAX_TURNS", "40"))
MAX_BUDGET = float(os.environ.get("REPO_BRIEF_MAX_BUDGET_USD", "2.0"))


# -- shared reads ----------------------------------------------------------


def _units(root: Path, adapter):
    units, state, _stats = index_mod.ensure(root, adapter)
    return units, state


def context_text(root: Path, adapter, query: str, limit: int = 6) -> str:
    units, state = _units(root, adapter)
    try:
        context = kernel.build_context(
            root, adapter, query, limit, units=units, usage_index=state
        )
    finally:
        if state is not None:
            state.close()
    return render.render_brief(context)


def graph_text(root: Path, adapter, area: str = "", task: str = "") -> str:
    units, state = _units(root, adapter)
    try:
        detected = adapter.detected(root) if hasattr(adapter, "detected") else None
        edges, areas = graph_mod.build(
            root,
            adapter,
            units,
            detected.source_root if detected else "",
            detected.autoloads if detected else {},
        )
        kinds = graph_mod.DEFAULT_KINDS
        if task:
            context = kernel.build_context(
                root, adapter, task, 8, units=units, usage_index=state
            )
            found = graph_mod.task_graph(context, edges, kinds)
        elif area:
            found = graph_mod.area_graph(units, edges, areas, area, kinds)
        else:
            found = graph_mod.package_graph(units, edges, areas, kinds)
    finally:
        if state is not None:
            state.close()
    return graph_mod.mermaid(found)


def overview_text(root: Path) -> str:
    try:
        return (root / overview.OVERVIEW).read_text(encoding="utf-8")
    except OSError:
        return ""


def map_text(root: Path) -> str:
    try:
        return (root / ".repo-brief" / "map.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def tools_summary(root: Path) -> str:
    data = catalog.load(root)
    lines = ["repo-brief's own: context, graph, overview (resource), map (resource)."]
    for group in catalog.GROUPS:
        if group == "brief":
            continue
        items = [item for item in data.get("commands", []) if item.get("group") == group]
        settings = data.get("groups", {}).get(group, {})
        label = "exposed" if settings.get("exposed") else "not exposed"
        names = ", ".join(item["id"] for item in items[:10]) or "-"
        more = "" if len(items) <= 10 else " (+%d more)" % (len(items) - 10)
        lines.append("%s (%s): %s%s" % (group, label, names, more))
    return "\n".join(lines)


def run_command(root: Path, command_id: str, args: str = "") -> str:
    """Run one command from the catalog. Only exposed, non-manual groups pass."""
    if not trust.is_trusted(root):
        return "This repository is not trusted; repo-brief may not run its scripts (`repo-brief trust`)."
    data = catalog.load(root)
    item = next(
        (entry for entry in data.get("commands", []) if entry.get("id") == command_id),
        None,
    )
    if item is None:
        return "No command %r; run `repo-brief tools` for ids." % command_id
    refused = catalog.runnable(root, item)
    if refused:
        return "Refused: %s." % refused
    group = item.get("group")
    if group not in catalog.GROUPS or group == "manual" or group == "brief":
        return "Command %r is in the %s group and is never exposed." % (command_id, group)
    if not data.get("groups", {}).get(group, {}).get("exposed"):
        return "Group %r is not exposed." % group
    extra = shlex.split(args)
    if item["kind"] == "script":
        command = [sys.executable, str(root / item["path"]), *extra]
    elif item["kind"] == "devsh":
        command = ["sh", str(root / item["path"]), item["name"], *extra]
    else:
        command = ["make", item["name"], *extra]
    try:
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "Could not run %s: %s" % (command_id, exc)
    output = (result.stdout or "") + (result.stderr or "")
    output = output.strip() or "(no output)"
    if result.returncode != 0:
        output = "exit %d\n%s" % (result.returncode, output)
    return output[:CLIP]


# -- MCP server ------------------------------------------------------------


def build_server(root: Path, adapter, trusted: bool | None = None):
    """The MCP server for one repo, without starting a transport."""
    from mcp.server.mcpserver import MCPServer

    if trusted is None:
        trusted = trust.is_trusted(root)
    server = MCPServer(
        root.name,
        instructions=_instructions(root, trusted),
    )

    @server.tool(description="Rank the files that matter for a query and return a read plan.")
    def repo_context(query: str) -> str:
        return context_text(root, adapter, query)

    @server.tool(description="Dependencies between areas, or the files inside one area.")
    def repo_graph(area: str = "", task: str = "") -> str:
        return graph_text(root, adapter, area=area, task=task)

    @server.tool(description="The repo's commands and the group each one is in.")
    def repo_tools() -> str:
        return tools_summary(root)

    @server.tool(description="Run one command from an exposed group (trusted repos only).")
    def repo_run(id: str, args: str = "") -> str:
        if not trusted:
            return "This repo is not trusted; repo-brief may not run its scripts."
        return run_command(root, id, args)

    @server.tool(
        description=(
            "Ask a read-only agent a question about this repository. It returns an answer "
            "with path:line references, gaps, and a session id for a follow-up. Slow: "
            "seconds to minutes."
        )
    )
    async def repo_ask(question: str, audience: str = "developer", session_id: str = "") -> str:
        if importlib.util.find_spec("claude_agent_sdk") is None:
            return "ask needs `repo-brief[agent]` (the Claude Agent SDK) installed."
        return await ask_async(
            root,
            adapter,
            question,
            audience=audience,
            session_id=session_id or None,
            allowed_groups={"brief", "inspect"} if trusted else {"brief"},
        )

    @server.resource("repo://overview", mime_type="text/markdown")
    def repo_overview() -> str:
        return overview_text(root) or "No overview yet; run `repo-brief update`."

    @server.resource("repo://map", mime_type="text/markdown")
    def repo_map() -> str:
        return map_text(root) or "No map yet; run `repo-brief update`."

    return server


def serve(root: Path, adapter, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765):
    try:
        server = build_server(root, adapter)
    except ImportError as exc:  # pragma: no cover - the extra is optional
        raise SystemExit(
            "The MCP server needs the optional dependency: install `repo-brief[mcp]`."
        ) from exc
    if transport == "http":
        server.run("streamable-http", host=host, port=port)
    else:
        server.run("stdio")


def _instructions(root: Path, trusted: bool) -> str:
    short = overview.short(root)
    lines = [
        "repo-brief server for the repository %r." % root.name,
        short,
        "Tools: repo_context for where something lives, repo_graph for dependencies, "
        "repo_tools for the repo's commands, repo_ask for a question that needs an "
        "investigation. Resources: repo://overview and repo://map.",
    ]
    if not trusted:
        lines.append(
            "This repository is not trusted, so its own scripts are not exposed; "
            "`repo-brief trust` enables them."
        )
    return "\n\n".join(line for line in lines if line)


# -- ask: a read-only agent inside the server ------------------------------


def _agent_tools(root: Path, adapter, allowed_groups: set):
    """The agent's toolbox, served in-process so it never needs a shell."""
    from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

    read_only = ToolAnnotations(readOnlyHint=True)

    @tool(
        "repo_brief_context",
        "Rank the files that matter for a query and get a read plan. Use the code's "
        "words (class, method, package names), not product phrases.",
        {"query": str},
        annotations=read_only,
    )
    async def repo_brief_context(args):
        return _text(context_text(root, adapter, str(args.get("query", ""))))

    @tool(
        "repo_brief_graph",
        "How the repo's areas depend on each other, as a Mermaid flowchart. Pass an "
        "area to expand its files.",
        {"area": str},
        annotations=read_only,
    )
    async def repo_brief_graph(args):
        return _text(graph_text(root, adapter, area=str(args.get("area") or "")))

    @tool(
        "repo_brief_overview",
        "The generated project overview: stack, layout, areas, entry points, tests, docs.",
        {},
        annotations=read_only,
    )
    async def repo_brief_overview(args):
        return _text(overview_text(root) or "No overview yet; run `repo-brief update`.")

    tools = [repo_brief_context, repo_brief_graph, repo_brief_overview]
    if "inspect" in allowed_groups and trust.is_trusted(root):
        @tool(
            "repo_brief_run",
            "Run one read-only script from the repo's inspect group. Get ids from "
            "`run` with no id.",
            {"id": str, "args": str},
            annotations=read_only,
        )
        async def repo_brief_run(args):
            return _text(run_command(root, str(args.get("id") or ""), str(args.get("args") or "")))
        tools.append(repo_brief_run)
    allowed = ["mcp__%s__%s" % (SERVER_NAME, item.name) for item in tools]
    return create_sdk_mcp_server(SERVER_NAME, tools=tools), allowed


def _text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text[:CLIP]}]}


PROMPT = """You answer questions about the repository at {root} for another agent or a person.

Investigate with the read-only tools you have, starting with repo_brief_context when you do
not know where something lives. Every claim about the code cites `path:line`. If the repo
does not contain the answer, say so and list what is missing; never invent code or intent.
Answer for a {audience}. Keep the answer short enough to read in one screen, and put the
references at the end."""


async def ask_async(
    root: Path,
    adapter,
    question: str,
    audience: str = "developer",
    session_id: str | None = None,
    allowed_groups=None,
    client_cls=None,
) -> str:
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
        )
    except ImportError as exc:  # pragma: no cover - the extra is optional
        raise SystemExit("ask needs `repo-brief[agent]` (the Claude Agent SDK) installed.") from exc

    toolbox, allowed = _agent_tools(root, adapter, allowed_groups or {"brief"})
    options = ClaudeAgentOptions(
        cwd=root,
        tools=["Read", "Grep", "Glob"],
        mcp_servers={SERVER_NAME: toolbox},
        allowed_tools=allowed,
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        verbatim_prompts=True,
        system_prompt=PROMPT.format(root=root, audience=audience),
        model=MODEL,
        max_turns=MAX_TURNS,
        max_budget_usd=MAX_BUDGET,
        resume=session_id,
    )
    client_factory = client_cls or ClaudeSDKClient
    answer = ""
    session = session_id or ""
    duration_ms = 0
    cost = 0.0
    status = "ok"
    async with client_factory(options=options) as client:
        await client.query(question)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        answer = block.text
            elif isinstance(message, ResultMessage):
                session = message.session_id or session
                duration_ms = message.duration_ms or duration_ms
                cost = message.total_cost_usd or cost
                if message.result:
                    answer = message.result
                if message.is_error:
                    status = message.subtype or "error"
    if not answer:
        answer = "The agent returned no answer."
    log_mod.record_ask(
        root,
        question,
        answer,
        session_id=session,
        model=MODEL,
        duration_ms=duration_ms,
        cost_usd=cost,
        status=status,
        runner="claude",
        audience=audience,
    )
    if status != "ok":
        return "The agent failed (%s): %s" % (status, answer)
    if session:
        answer += "\n\nSession: %s" % session
    return answer
