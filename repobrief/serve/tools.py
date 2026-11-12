"""What `serve` and `ask` can do, defined once.

The MCP server wraps these functions for an orchestrator, and each runner wraps
`agent_specs` for its agent. The rule that `ask` never gets `dev` or `debug` is
enforced in `agent_specs`, before any runner sees a tool, so it holds for every
agent CLI whatever its own permission settings say.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import catalog, facts as facts_mod, graph as graph_mod, kernel, overview, render
from .. import index as index_mod

CLIP = 12_000
SCRIPT_GROUPS = ("inspect", "dev", "debug")

CONTEXT_DESCRIPTION = (
    "Rank the files that matter for a query and return a read plan: files with line "
    "ranges and sizes, then the lines that matched. Use the code's words (class, "
    "function and package names), not product phrases."
)
GRAPH_DESCRIPTION = (
    "How the repository's areas depend on each other, as a Mermaid flowchart. Pass "
    "`area` to list the files inside one area, or `task` for the files a query surfaces."
)
OVERVIEW_DESCRIPTION = (
    "The project overview: stack, layout, areas and their dependencies, entry points, "
    "tests, docs and commands."
)
FACTS_DESCRIPTION = (
    "Search the named things this repository's adapter extracted (feature flags, settings, "
    "routes): the id, a summary, tickets, and how many places declare or use each. An empty "
    "query lists them by id; `kind` narrows it to one kind."
)
FACT_DESCRIPTION = (
    "One extracted fact by its id (from repo_facts), with every place it is declared, used or "
    "named as data, and the line that shows it."
)
GROUP_INTROS = {
    "inspect": "Run one of this repository's scripts that only read and report, and return its output.",
    "dev": (
        "Run one of this repository's build, test, run, benchmark or training scripts and "
        "return its output. These run the project and may take minutes or write files."
    ),
    "debug": (
        "Run one of this repository's debugging scripts, which look inside a running game "
        "or model, and return its output."
    ),
}


@dataclass
class ToolSpec:
    """One tool, independent of how a runner or the server exposes it."""

    name: str
    description: str
    params: dict
    call: Callable[[dict], str]
    read_only: bool = True


def clip(text: str) -> str:
    if len(text) <= CLIP:
        return text
    return text[:CLIP] + "\n... (cut at %d characters)" % CLIP


# -- reads ----------------------------------------------------------------


def _units(root: Path, adapter):
    units, state, _stats = index_mod.ensure(root, adapter)
    return units, state


def context_text(root: Path, adapter, query: str, limit: int = 6) -> str:
    if not query.strip():
        return "Pass a query: the class, function or file names you are looking for."
    units, state = _units(root, adapter)
    try:
        context = kernel.build_context(root, adapter, query, limit, units=units, usage_index=state)
    finally:
        if state is not None:
            state.close()
    return clip(render.render_brief(context))


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
            context = kernel.build_context(root, adapter, task, 8, units=units, usage_index=state)
            found = graph_mod.task_graph(context, edges, kinds)
        elif area:
            found = graph_mod.area_graph(units, edges, areas, area, kinds)
        else:
            found = graph_mod.package_graph(units, edges, areas, kinds)
    finally:
        if state is not None:
            state.close()
    return clip(graph_mod.mermaid(found))


def overview_text(root: Path) -> str:
    try:
        return (Path(root) / overview.OVERVIEW).read_text(encoding="utf-8")
    except OSError:
        return ""


def facts_text(
    root: Path, adapter, query: str = "", kind: str = "", limit: int = 15,
    fact_tool: str = "repo_fact",
) -> str:
    data, loaded = facts_mod.load(root, adapter)
    return clip(facts_mod.render_search(data, query, kind, limit, loaded, fact_tool))


def fact_text(root: Path, adapter, fact_id: str = "") -> str:
    data, _loaded = facts_mod.load(root, adapter)
    fact = facts_mod.find(data.get("facts") or [], fact_id)
    if fact is None:
        return "No fact %r. Call repo_facts (repo_brief_facts for the agent) for the ids." % fact_id
    return clip(facts_mod.render_fact(fact))


def map_text(root: Path) -> str:
    try:
        return (Path(root) / ".repo-brief" / "map.md").read_text(encoding="utf-8")
    except OSError:
        return ""


# -- scripts --------------------------------------------------------------


def _script_line(item: dict) -> str:
    label = item["name"] if item.get("kind") == "script" else item["id"]
    line = "`%s`" % label
    if item.get("summary"):
        line += ": %s" % item["summary"]
    if item.get("args"):
        line += " (options: %s)" % " ".join(item["args"][:12])
    return line


def scripts_in(root: Path, group: str) -> list:
    return [item for item in catalog.commands(catalog.load(root)) if item.get("group") == group]


def group_description(root: Path, group: str) -> str:
    """One group's tool description: what it does, then every script in it."""
    lines = [
        GROUP_INTROS[group],
        "Pass `script`, one of the names below, and `args`, a command-line string.",
        "",
        "Scripts:",
    ]
    lines += ["- %s" % _script_line(item) for item in scripts_in(root, group)]
    return "\n".join(lines)


def catalog_text(root: Path, trusted: bool, groups=None) -> str:
    """The repo's scripts by group, and which groups this caller may run."""
    allowed = catalog.allowed_groups(trusted, narrowed=groups)
    lines = [
        "repo-brief's own tools: repo_context, repo_graph, repo_tools and repo_ask; "
        "resources repo://overview and repo://map."
    ]
    if not trusted:
        lines.append(
            "This repository is not trusted, so none of its scripts run here "
            "(`repo-brief trust` allows them)."
        )
    for group in catalog.GROUPS[1:]:
        items = scripts_in(root, group)
        if not items:
            continue
        if group in allowed:
            state = "run them with repo_%s" % group
        elif group == "manual":
            state = "never run by repo-brief"
        else:
            state = "not available to this client"
        lines += ["", "%s (%d, %s):" % (group, len(items), state)]
        lines += ["- %s" % _script_line(item) for item in items]
    return "\n".join(lines)


def run_script(root: Path, group: str, script: str, args: str = "", *, trusted: bool, timeout: float) -> str:
    """Run one script of `group`, after every check SPEC.md ("Trust") lists."""
    root = Path(root)
    if not trusted:
        return (
            "This repository is not trusted, so repo-brief may not run its scripts "
            "(`repo-brief trust`)."
        )
    items = scripts_in(root, group)
    item = next((entry for entry in items if script in (entry.get("id"), entry.get("name"))), None)
    if item is None:
        names = ", ".join(entry["name"] for entry in items) or "none"
        return "No %s script named %r. The %s scripts are: %s." % (group, script, group, names)
    refused = catalog.runnable(root, item)
    if refused:
        return "Refused: %s." % refused
    try:
        extra = shlex.split(args or "")
    except ValueError as exc:
        return "Could not read the arguments: %s" % exc
    if item["kind"] == "script":
        command = [sys.executable, str(root / item["path"]), *extra]
    elif item["kind"] == "devsh":
        command = ["sh", str(root / item["path"]), item["name"], *extra]
    else:
        command = ["make", item["name"], *extra]
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "Stopped %s after %g s." % (item["id"], timeout)
    except OSError as exc:
        return "Could not run %s: %s" % (item["id"], exc)
    output = ((result.stdout or "") + (result.stderr or "")).strip() or "(no output)"
    if result.returncode:
        output = "exit %d\n%s" % (result.returncode, output)
    return clip(output)


# -- the agent's toolbox --------------------------------------------------


def agent_specs(root: Path, adapter, *, trusted: bool, groups=None, script_timeout: float = 600) -> list:
    """The tools a runner gives `ask`'s agent.

    repo-brief's own read-only tools always, and the `inspect` scripts in a
    trusted repo. `dev` and `debug` are never offered, whatever `groups` asks
    for: `catalog.allowed_groups(..., for_ask=True)` cannot return them.
    """
    root = Path(root)
    allowed = catalog.allowed_groups(trusted, for_ask=True, narrowed=_with_brief(groups))
    specs = [
        ToolSpec(
            "repo_brief_context",
            CONTEXT_DESCRIPTION,
            {"query": str},
            lambda args: context_text(root, adapter, str(args.get("query") or "")),
        ),
        ToolSpec(
            "repo_brief_graph",
            GRAPH_DESCRIPTION,
            {"area": str},
            lambda args: graph_text(root, adapter, area=str(args.get("area") or "")),
        ),
        ToolSpec(
            "repo_brief_overview",
            OVERVIEW_DESCRIPTION,
            {},
            lambda args: overview_text(root) or "No overview yet; run `repo-brief update`.",
        ),
    ]
    if facts_mod.available(adapter):
        specs += [
            ToolSpec(
                "repo_brief_facts",
                FACTS_DESCRIPTION,
                {"query": str, "kind": str},
                lambda args: facts_text(
                    root,
                    adapter,
                    str(args.get("query") or ""),
                    str(args.get("kind") or ""),
                    fact_tool="repo_brief_fact",
                ),
            ),
            ToolSpec(
                "repo_brief_fact",
                FACT_DESCRIPTION,
                {"id": str},
                lambda args: fact_text(root, adapter, str(args.get("id") or "")),
            ),
        ]
    if "inspect" in allowed and scripts_in(root, "inspect"):
        specs.append(
            ToolSpec(
                "repo_brief_inspect",
                group_description(root, "inspect"),
                {"script": str, "args": str},
                lambda args: run_script(
                    root,
                    "inspect",
                    str(args.get("script") or ""),
                    str(args.get("args") or ""),
                    trusted=trusted,
                    timeout=script_timeout,
                ),
            )
        )
    return specs


def _with_brief(groups):
    """A client narrows the script groups; repo-brief's own tools always stay."""
    return None if groups is None else set(groups) | {"brief"}
