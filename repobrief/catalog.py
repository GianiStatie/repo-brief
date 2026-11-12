"""The scripts a repo ships, grouped by what they do.

A mature repo has dozens of scripts. Exposing each as its own tool would drown
the orchestrator and expose everything a script can do. Instead every script is
placed in one group and each group is exposed as one tool:

    brief    repo-brief itself (context, graph, overview, facts)
    inspect  reads and reports, no side effects
    dev      builds, tests, runs, trains
    debug    looks inside a running game or model
    manual   writes files, touches secrets, publishes; never exposed

The group is a judgement, so `.repo-brief/tools.json` keeps it beside the
detected facts and `repo-brief tools --set` changes it without a code edit.
Detection reads the files, never runs them.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

STATE_FILE = "tools.json"
GROUPS = ("brief", "inspect", "dev", "debug", "manual")
# Where a repo's scripts live. A local-only repo adds `.repo-brief/tools/`, the
# one script folder that is never committed.
SCRIPT_FOLDERS = ("tools", "scripts")
LOCAL_FOLDER = ".repo-brief/tools"
DEVSH_PATHS = ("tools/dev.sh", "dev.sh")
MAKEFILES = ("Makefile", "makefile")
# `brief` is repo-brief's own and needs no entry here; the rest describe scripts.
DEFAULTS = {
    "brief": {"exposed": True, "ask": True},
    "inspect": {"exposed": True, "ask": True},
    "dev": {"exposed": True, "ask": False},
    "debug": {"exposed": True, "ask": False},
    "manual": {"exposed": False, "ask": False},
}
MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_-]+):.*?##\s*(.*)$", re.M)
DEVSH_TARGET_RE = re.compile(r"^\s{2,}([a-z][a-z0-9_-]*)\)\s*$", re.M)
MANUAL_WORDS = (
    "generate", "pack", "release", "publish", "upload", "deploy", "refresh",
    "secret", "credential", "auth", "login", "install",
)
DEBUG_WORDS = ("diagnose", "debug", "watch", "benchmark", "profile", "trace", "runtime")
DEV_WORDS = ("test", "build", "run ", "train", "collect", "evaluate", "bench", "export")
INSPECT_WORDS = (
    "check", "validate", "audit", "visualize", "show", "list", "print", "report",
    "summar", "brief", "search", "find", "lookup", "inspect", "dump", "status",
)


def guess_group(name: str, summary: str = "") -> str:
    text = ("%s %s" % (name, summary)).lower()
    if any(word in text for word in MANUAL_WORDS):
        return "manual"
    if any(word in text for word in DEBUG_WORDS):
        return "debug"
    if any(word in text for word in DEV_WORDS):
        return "dev"
    if any(word in text for word in INSPECT_WORDS):
        return "inspect"
    return "manual"


def _script_summary(tree: ast.AST) -> str:
    doc = (ast.get_docstring(tree) or "").strip()
    return doc.splitlines()[0].strip() if doc else ""


def _script_args(tree: ast.AST) -> list[str]:
    """The long and short flags a script accepts, in the order written.

    Reading argparse is enough to tell an agent what can be varied; the exact
    spelling and help come from `--help` when the tool finally runs.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else ""
        if name != "add_argument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
                if value.startswith("-") and value not in found:
                    found.append(value)
    return found


def _python_commands(root: Path, paths: set[str]) -> list[dict]:
    found = []
    for rel in sorted(paths):
        if not (rel.startswith("tools/") or rel.startswith("scripts/")):
            continue
        if not rel.endswith(".py") or rel.endswith("brief_adapter.py"):
            continue
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        summary = _script_summary(tree)
        name = Path(rel).stem
        found.append(
            {
                "id": "script:%s" % rel,
                "kind": "script",
                "path": rel,
                "name": name,
                "summary": summary,
                "args": _script_args(tree),
                "group": guess_group(name, summary),
            }
        )
    return found


def _devsh_commands(root: Path, paths: set[str]) -> list[dict]:
    rel = next((candidate for candidate in ("tools/dev.sh", "dev.sh") if candidate in paths), "")
    if not rel:
        return []
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for name in dict.fromkeys(DEVSH_TARGET_RE.findall(text)):
        if name in {"usage"}:
            continue
        found.append(
            {
                "id": "devsh:%s" % name,
                "kind": "devsh",
                "path": rel,
                "name": name,
                "summary": "",
                "args": [],
                "group": guess_group(name),
            }
        )
    return found


def _make_commands(root: Path, paths: set[str]) -> list[dict]:
    if "Makefile" not in paths and "makefile" not in paths:
        return []
    rel = "Makefile" if "Makefile" in paths else "makefile"
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for name, summary in MAKE_TARGET_RE.findall(text):
        found.append(
            {
                "id": "make:%s" % name,
                "kind": "make",
                "path": rel,
                "name": name,
                "summary": summary.strip(),
                "args": [],
                "group": guess_group(name, summary),
            }
        )
    return found


def scan(root: Path, detected) -> list[dict]:
    """Every script and command the walk can see, with a group for each."""
    paths = detected.paths
    commands = _python_commands(root, paths) + _devsh_commands(root, paths) + _make_commands(root, paths)
    commands.sort(key=lambda item: (GROUPS.index(item["group"]), item["id"]))
    return commands


def load(root: Path) -> dict:
    try:
        data = json.loads((root / ".repo-brief" / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write(root: Path, detected) -> tuple[Path, dict, int]:
    """Refresh the detected commands; keep every judgement already made."""
    previous = load(root)
    judgments = dict(previous.get("judgments") or {})
    groups = dict(DEFAULTS)
    for name, settings in (previous.get("groups") or {}).items():
        if name in groups and isinstance(settings, dict):
            groups[name].update(settings)
    commands = scan(root, detected)
    added = 0
    known = {item["id"] for item in previous.get("commands") or []}
    for item in commands:
        if item["id"] in judgments:
            item["group"] = judgments[item["id"]]
        if item["id"] not in known:
            added += 1
    path = root / ".repo-brief" / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated": True,
        "groups": groups,
        "judgments": judgments,
        "commands": commands,
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, data, added


def script_folders(root: Path) -> list[str]:
    folders = list(SCRIPT_FOLDERS)
    if (root / ".repo-brief" / "local.json").is_file():
        folders.append(LOCAL_FOLDER)
    return folders


def runnable(root: Path, item: dict) -> str:
    """Why this catalog entry may not run, or "" when it may.

    `tools.json` is a file in the repo, so its paths are checked again here
    rather than trusted: a script must sit inside a script folder of this
    repository, and a shell or make target must name the repo's own file.
    """
    rel = str(item.get("path") or "")
    kind = item.get("kind")
    top = Path(root).resolve()
    target = (top / rel).resolve()
    try:
        inside = target.relative_to(top).as_posix()
    except ValueError:
        return "%s is outside the repository" % rel
    if not target.is_file():
        return "%s does not exist" % rel
    if kind == "script":
        if not any(inside.startswith(folder + "/") for folder in script_folders(top)):
            return "%s is not in a script folder (%s)" % (rel, ", ".join(script_folders(top)))
        return ""
    if kind == "devsh":
        return "" if inside in DEVSH_PATHS else "%s is not the repo's dev.sh" % rel
    if kind == "make":
        return "" if inside in MAKEFILES else "%s is not the repo's Makefile" % rel
    return "unknown kind %r" % kind


def set_group(root: Path, command_id: str, group: str) -> dict:
    if group not in GROUPS:
        raise ValueError("unknown group %r (use %s)" % (group, ", ".join(GROUPS)))
    data = load(root)
    known = {item["id"] for item in data.get("commands") or []}
    if command_id not in known:
        raise ValueError("unknown command %r; run `repo-brief tools` for ids" % command_id)
    judgments = data.setdefault("judgments", {})
    judgments[command_id] = group
    for item in data.get("commands") or []:
        if item["id"] == command_id:
            item["group"] = group
    target = root / ".repo-brief" / STATE_FILE
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data
