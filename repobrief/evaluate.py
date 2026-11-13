"""Replay recorded questions against the tree each one was asked on.

A benchmark written for the tool can be tuned to its own fixtures. The local
log already holds what someone really asked and which files the session then
touched, so replaying it at the commit of the moment is a before-and-after for
every change to ranking or plan size.

The score is the files the session edited afterwards: those are the ones a
brief that pointed the right way would have named. Files the session read are
reported separately, as a guard against a brief that grew too narrow.

A question with no commit before it (a log imported on a shallow clone, an ask
that never reached a commit) is counted and left out, never guessed at.
"""
from __future__ import annotations

import io
import json
import re
import statistics
import subprocess
import tarfile
import tempfile
from pathlib import Path
from shutil import rmtree

from . import kernel, log as log_mod
from . import index as index_mod
from .contract import Adapter

# Tool names as the two clients record them. Lowercased before comparing.
EDIT_TOOLS = {
    "edit", "write", "multiedit", "notebookedit", "apply_patch", "patch", "create",
}
READ_TOOLS = {"read", "view"}
SHELL_TOOLS = {"bash", "shell", "run_command", "terminal"}
PATH_KEYS = ("file_path", "filePath", "path", "filename")
CONTEXT_CMD = re.compile(r"(?:dev\.sh|repo-brief)\s+context\b(.*)$", re.I)
QUOTED = re.compile(r'"([^"]+)"|\'([^\']+)\'')
BUDGETS = (10, 15, 20)


def _input(call: dict) -> dict:
    """A call's arguments, whichever shape the import gave them."""
    value = call.get("input")
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _paths(calls: list[dict], tools: set[str]) -> list[str]:
    """The files a run of calls touched, in order, without duplicates."""
    found: list[str] = []
    for call in calls:
        if str(call.get("tool") or "").lower() not in tools:
            continue
        data = _input(call)
        for key in PATH_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value and value not in found:
                found.append(value)
                break
    return found


def _query_from_command(command: str) -> str:
    """The query inside `repo-brief context ...` or `tools/dev.sh context ...`."""
    match = CONTEXT_CMD.search(command)
    if match is None:
        return ""
    rest = match.group(1).strip()
    rest = re.sub(r"^--brief\s+", "", rest)
    quoted = QUOTED.search(rest)
    if quoted:
        return (quoted.group(1) or quoted.group(2)).strip()
    return " ".join(word for word in rest.split() if not word.startswith("-"))


def _tool_name(call: dict) -> str:
    name = str(call.get("tool") or "").lower()
    # An MCP call can arrive as `mcp__server__repo_context`.
    return name.split("__")[-1]


def queries_for(calls: list[dict], question: str) -> dict:
    """The two query styles: the agent's own brief call and the typed question."""
    agent = ""
    for call in calls:
        tool = _tool_name(call)
        data = _input(call)
        if tool == "repo_context":
            agent = str(data.get("query") or "").strip()
        elif tool in SHELL_TOOLS:
            command = data.get("command") or data.get("cmd") or data.get("script") or ""
            if isinstance(command, str):
                agent = _query_from_command(command)
        if agent:
            break
    return {"agent": agent, "user": question.strip()}


def commit_at(root: Path, at: str) -> str:
    """The commit that was current when a question was asked."""
    if not at:
        return ""
    try:
        found = subprocess.run(
            ["git", "-C", str(root), "rev-list", "-1", "--before", at, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return found.stdout.strip() if found.returncode == 0 else ""


def _tree(root: Path, sha: str, workdir: Path) -> Path | None:
    """Write the commit's tree into a temporary directory, once per commit."""
    dest = workdir / sha[:12]
    if dest.is_dir():
        return dest
    try:
        found = subprocess.run(
            ["git", "-C", str(root), "archive", sha], capture_output=True, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if found.returncode != 0:
        return None
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(found.stdout)) as archive:
        archive.extractall(dest)
    return dest


def _existing(tree: Path, paths: list[str]) -> list[str]:
    """Drop paths that did not exist yet: a file the session created is new."""
    return [path for path in paths if (tree / path).is_file()]


def _recall(rows: list[dict], field: str, truth: str) -> tuple[int, int]:
    """Hits, and the rows that could be hit at all."""
    graded = [row for row in rows if row[truth]]
    hits = sum(1 for row in graded if set(row[field]) & set(row[truth]))
    return hits, len(graded)


def _within(row: dict, budget: int) -> set[str]:
    """The plan's paths that fit in a byte budget, cut at slice boundaries."""
    total = 0
    kept: list[str] = []
    for item in row["plan_items"]:
        if total + item["bytes"] > budget:
            break
        total += item["bytes"]
        kept.append(item["path"])
    return set(kept)


def _median_bytes(rows: list[dict]) -> float:
    return statistics.median([row["bytes"] for row in rows]) if rows else 0.0


def summarize(rows: list[dict]) -> dict:
    """One summary per query style, over the rows it was given."""
    summary = {}
    for style in ("agent", "user"):
        found = [row for row in rows if row["style"] == style]
        plan_hits, edited = _recall(found, "plan", "edited")
        named_hits, _ = _recall(found, "named", "edited")
        read_hits, read = _recall(found, "read", "read")
        budgets = {
            budget: sum(
                1
                for row in found
                if row["edited"] and _within(row, budget * 1024) & set(row["edited"])
            )
            for budget in BUDGETS
        }
        summary[style] = {
            "questions": len(found),
            "edited": edited,
            "read": read,
            "plan_hits": plan_hits,
            "named_hits": named_hits,
            "read_hits": read_hits,
            "median_bytes": _median_bytes(found),
            "budget_hits": budgets,
        }
    return summary


def run(
    root: Path,
    adapter: Adapter,
    limit: int = 6,
    split: str = "all",
    at_head: bool = False,
    max_questions: int = 200,
    on_progress=None,
) -> dict:
    """Replay the newest questions; return rows and the ones left out.

    `max_questions` keeps one run to a few minutes. It takes the newest, so a
    replay measures the tool a session would meet today; `0` takes every
    imported question.
    """
    root = Path(root).resolve()
    asks = log_mod.imported_asks(root)
    asks.sort(key=lambda ask: ask["at"] or "")
    if max_questions:
        asks = asks[-max_questions:]
    holdout_from = len(asks) - (len(asks) + 2) // 3
    commits: dict[str, str] = {}
    for index, ask in enumerate(asks):
        ask["split"] = "holdout" if index >= holdout_from else "tune"
        ask["commit"] = "HEAD" if at_head else commit_at(root, ask["at"])

    rows: list[dict] = []
    skipped = {"no_commit": 0, "no_files": 0}
    workdir = Path(tempfile.mkdtemp(prefix="repo-brief-eval-"))
    try:
        groups: dict[str, list[dict]] = {}
        for ask in asks:
            if not ask["commit"]:
                skipped["no_commit"] += 1
                continue
            # A turn that touched no file says nothing about a read plan; skip
            # it before writing a tree for its commit.
            calls = ask["calls"]
            if not _paths(calls, EDIT_TOOLS) and not _paths(calls, READ_TOOLS):
                skipped["no_files"] += 1
                continue
            groups.setdefault(ask["commit"], []).append(ask)

        for sha, group in sorted(groups.items()):
            tree = root if at_head else _tree(root, sha, workdir)
            if tree is None:
                skipped["no_commit"] += len(group)
                continue
            units, state, _stats = index_mod.ensure(tree, adapter, build=True)
            try:
                for ask in group:
                    calls = ask["calls"]
                    edited = _existing(tree, _paths(calls, EDIT_TOOLS))
                    read = _existing(tree, _paths(calls, READ_TOOLS))
                    if not edited and not read:
                        skipped["no_files"] += 1
                        continue
                    for style, query in queries_for(calls, ask["question"]).items():
                        if not query:
                            continue
                        context = kernel.build_context(
                            tree, adapter, query, limit, units=units, usage_index=state
                        )
                        plan_items = context["read_plan"]
                        plan = [item["path"] for item in plan_items]
                        # The brief names files three ways: the plan, the paths
                        # after it, and the path of each decision or open loop
                        # it cites. A cited row is not a miss.
                        cited = [
                            item["path"]
                            for item in context.get("decisions", [])
                            + context.get("open_work", [])
                        ]
                        named = list(
                            dict.fromkeys(
                                plan + list(context.get("also_matched") or []) + cited
                            )
                        )
                        rows.append(
                            {
                                "id": ask["external_id"],
                                "question": ask["question"],
                                "at": ask["at"],
                                "split": ask["split"],
                                "commit": sha,
                                "style": style,
                                "query": query,
                                "plan": plan,
                                "plan_items": [
                                    {"path": item["path"], "bytes": item["bytes"]}
                                    for item in plan_items
                                ],
                                "named": named,
                                "bytes": context["budget"]["bytes"],
                                "edited": edited,
                                "read": read,
                            }
                        )
                    if on_progress is not None:
                        on_progress(ask)
            finally:
                if state is not None:
                    state.close()
    finally:
        rmtree(workdir, ignore_errors=True)

    if split != "all":
        rows = [row for row in rows if row["split"] == split]
    return {
        "root": str(root),
        "questions": len(asks),
        "skipped": skipped,
        "rows": rows,
        "summary": summarize(rows),
    }


def _percent(hits: int, total: int) -> str:
    return "%d/%d %d%%" % (hits, total, round(100 * hits / total)) if total else "0/0 -"


def render_summary(result: dict) -> str:
    """The four lines per style: what was named, what was read, and at what cost."""
    skipped = result["skipped"]
    lines = [
        "Replay: %d question(s); %d without a commit, %d with no file touched"
        % (result["questions"], skipped["no_commit"], skipped["no_files"]),
    ]
    labels = {"agent": "the brief's own call", "user": "the question as typed"}
    for style in ("agent", "user"):
        found = result["summary"].get(style) or {}
        if not found.get("questions"):
            continue
        lines.append("%s:" % labels[style])
        lines.append(
            "  plan %s   named %s   read %s"
            % (
                _percent(found["plan_hits"], found["edited"]),
                _percent(found["named_hits"], found["edited"]),
                _percent(found["read_hits"], found["read"]),
            )
        )
        lines.append("  median %.1f KB" % (found["median_bytes"] / 1024))
        lines.append(
            "  "
            + "   ".join(
                "%d KB %s" % (budget, _percent(found["budget_hits"][budget], found["edited"]))
                for budget in BUDGETS
            )
        )
    return "\n".join(lines)


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(old: dict, new: dict) -> str:
    """The delta between two saved runs, matched by question and style."""
    before = {(row["id"], row["style"]): row for row in old.get("rows", [])}
    lines = ["Compare: %d -> %d question(s)" % (len(before), len(new.get("rows", [])))]
    for style in ("agent", "user"):
        pairs = [
            (before[(row["id"], row["style"])], row)
            for row in new.get("rows", [])
            if row["style"] == style and (row["id"], row["style"]) in before
        ]
        if not pairs:
            continue
        graded = [(a, b) for a, b in pairs if b["edited"]]
        old_hits = sum(1 for a, _b in graded if set(a["plan"]) & set(a["edited"]))
        new_hits = sum(1 for _a, b in graded if set(b["plan"]) & set(b["edited"]))
        old_named = sum(1 for a, _b in graded if set(a["named"]) & set(a["edited"]))
        new_named = sum(1 for _a, b in graded if set(b["named"]) & set(b["edited"]))
        lost = [
            b["question"] for a, b in graded
            if set(a["plan"]) & set(a["edited"]) and not set(b["plan"]) & set(b["edited"])
        ]
        lines.append(
            "%s: plan %s -> %s   named %s -> %s   median %.1f -> %.1f KB"
            % (
                style,
                _percent(old_hits, len(graded)),
                _percent(new_hits, len(graded)),
                _percent(old_named, len(graded)),
                _percent(new_named, len(graded)),
                statistics.median([a["bytes"] for a, _b in pairs]) / 1024,
                statistics.median([b["bytes"] for _a, b in pairs]) / 1024,
            )
        )
        if lost:
            lines.append("  lost a plan hit on %d question(s):" % len(lost))
            lines.extend("    %s" % question.replace("\n", " ")[:90] for question in lost[:5])
    return "\n".join(lines)
