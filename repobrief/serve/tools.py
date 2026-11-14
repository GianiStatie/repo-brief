"""What `serve` can do, defined once.

The MCP server wraps these functions for the client's agent, which searches and
reads with them itself; the CLI commands make the same reads in a shell.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .. import catalog, facts as facts_mod, graph as graph_mod, kernel, overview, render
from .. import index as index_mod

CLIP = 12_000
SCRIPT_GROUPS = ("inspect", "dev", "debug")

CONTEXT_DESCRIPTION = (
    "Rank the files that matter for a query and return a read plan: files with line "
    "ranges and sizes, then the lines that matched. Start with the question's own "
    "words, then query again with the class and function names the plan shows."
)
SEARCH_DESCRIPTION = (
    "Rank the files by the words they contain (BM25) and show each file's best-matching "
    "lines: code, tests, config, views and notes, in the modules that hold the product "
    "code. For words the code itself uses: a class, method, field, table, message key or "
    "config key, or an error's text. For a question in plain words, use repo_context."
)
GRAPH_DESCRIPTION = (
    "How the repository's areas depend on each other, as a Mermaid flowchart. Pass "
    "`area` to list the files inside one area, or `task` for the files a query surfaces."
)
DEPENDENTS_DESCRIPTION = (
    "Find usages of a class: the files that import, extend or name it, by directory. For "
    "\"what uses X\" and \"what would a change to X reach\", call it with the class name "
    "before grepping. Pass a class name, a qualified class name or a path."
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


def clip(text: str) -> str:
    if len(text) <= CLIP:
        return text
    return text[:CLIP] + "\n... (cut at %d characters)" % CLIP


# The CLI command that does the same read, for a caller told to try it in a shell.
TOOL_COMMANDS = {
    "repo_context": "context",
    "repo_search": "search",
    "repo_graph": "graph",
    "repo_dependents": "dependents",
}


def guard(tool: str, work: Callable[[], str]) -> str:
    """Run a read twice, then answer with the failure instead of raising.

    A tree can change while a read walks it, and a second attempt sees the
    settled tree. An error that survives it still reaches the client as a
    sentence naming the tool and the error, never as a bare MCP failure.
    """
    last: Exception | None = None
    for _ in range(2):
        try:
            return work()
        except Exception as exc:  # noqa: BLE001 - a tool call must always answer
            last = exc
    command = TOOL_COMMANDS.get(tool, tool)
    return "%s failed: %s: %s. Retry; if it repeats, run `repo-brief %s` in the shell." % (
        tool, type(last).__name__, last, command
    )


# -- reads ----------------------------------------------------------------


def _units(root: Path, adapter):
    units, state, _stats = index_mod.ensure(root, adapter)
    return units, state


def context_text(root: Path, adapter, query: str, limit: int = 6) -> str:
    if not query.strip():
        return "Pass a query: the question's own words, or the class and function names it involves."
    units, state = _units(root, adapter)
    try:
        context = kernel.build_context(root, adapter, query, limit, units=units, usage_index=state)
    finally:
        if state is not None:
            state.close()
    pointer = facts_mod.hint(root, adapter, query)
    if pointer:
        context["facts"] = pointer
    return clip(render.render_brief(context))


def search_text(root: Path, adapter, query: str, limit: int = 10) -> str:
    if not query.strip():
        return "Pass the words to look for, as the code writes them: a class, method, table or key."
    units, state = _units(root, adapter)
    try:
        found = kernel.search(root, adapter, query, limit, units=units, usage_index=state)
    finally:
        if state is not None:
            state.close()
    return clip(render.render_search(found))


def _edges(root: Path, adapter, units):
    detected = adapter.detected(root) if hasattr(adapter, "detected") else None
    return graph_mod.build(
        root,
        adapter,
        units,
        detected.source_root if detected else "",
        detected.autoloads if detected else {},
    )


def graph_text(root: Path, adapter, area: str = "", task: str = "") -> str:
    units, state = _units(root, adapter)
    try:
        edges, areas = _edges(root, adapter, units)
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


def dependents_text(root: Path, adapter, name: str) -> str:
    if not name.strip():
        return "Pass a class name, a qualified class name or a file path."
    units, state = _units(root, adapter)
    try:
        edges, _areas = _edges(root, adapter, units)
    finally:
        if state is not None:
            state.close()
    return clip(graph_mod.render_dependents(graph_mod.dependents(units, edges, name)))


def overview_text(root: Path) -> str:
    try:
        return (Path(root) / overview.OVERVIEW).read_text(encoding="utf-8")
    except OSError:
        return ""


def facts_text(root: Path, adapter, query: str = "", kind: str = "", limit: int = 15) -> str:
    data, loaded = facts_mod.load(root, adapter)
    return clip(facts_mod.render_search(data, query, kind, limit, loaded))


def fact_text(root: Path, adapter, fact_id: str = "") -> str:
    data, _loaded = facts_mod.load(root, adapter)
    fact = facts_mod.find(data.get("facts") or [], fact_id)
    if fact is None:
        return "No fact %r. Call repo_facts for the ids." % fact_id
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
        "repo-brief's own tools: repo_context, repo_search, repo_graph, repo_dependents, "
        "repo_tools, repo_facts and repo_fact; resources repo://overview and repo://map."
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

