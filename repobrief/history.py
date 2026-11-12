"""Import questions and tool calls from Claude Code's and OpenCode's sessions.

Both clients keep their own history on this machine: Claude Code one JSONL file
per session under `~/.claude/projects/<project>/`, OpenCode one exportable
record per session. `repo-brief log import` copies the questions and the tool
calls out of them, so `tools suggest` can learn from sessions repo-brief never
ran.

What is copied is deliberately narrow. A question is stored as it was typed,
minus the reminders both clients inject; a tool call is stored as the tool, the
arguments a search or a read was given, its size and whether it failed. File
contents a write or an edit carried are dropped: the log is a record of what was
asked and tried, not a second copy of the repository.

An OpenCode session that ran Claude Code carries the Claude session's id on each
tool call, so the two stores can be de-duplicated exactly: a Claude session that
OpenCode already covers is not imported twice.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import log as log_mod

CLAUDE = "claude-code"
OPENCODE = "opencode"
SOURCES = (CLAUDE, OPENCODE)
MAX_QUESTION = 4000
MAX_VALUE = 200
MAX_INPUT = 1500
# The answer itself, not a step of the investigation; repo-brief's own runner
# leaves it out of the log too.
ANSWER_TOOLS = {"StructuredOutput"}
DROP_KEYS = {
    # Carried file contents, not history worth keeping.
    "content", "new_string", "old_string", "newString", "oldString", "code",
    "edits", "patch", "diff", "file_text", "fileText", "newText", "oldText",
    "text", "body", "output",
}
INJECTED_PREFIXES = ("[request interrupted", "[structured-output-enforce]")
REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
# Claude Code wraps its own slash-command plumbing in these; none is a question.
COMMAND_RE = re.compile(
    r"<(command-name|command-message|command-args|local-command-caveat|"
    r"local-command-stdout|local-command-stderr)>.*?</\1>",
    re.S,
)


class HistoryError(Exception):
    """A session store could not be read."""


@dataclass
class Question:
    external_id: str
    at: str
    question: str
    client: str
    session: str = ""
    turns: int = 0
    duration_ms: int = 0
    cost_usd: float = 0.0
    model: str = ""
    calls: list = field(default_factory=list)


# -- shared cleaning ------------------------------------------------------


def _local(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))


def _epoch_iso(value) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _at_ms(value) -> str:
    try:
        return _local(float(value) / 1000)
    except (TypeError, ValueError):
        return ""


def _size(value) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, default=str))


def clean_question(text) -> str:
    """The question as it was typed: no injected reminders, no command plumbing."""
    if not isinstance(text, str):
        return ""
    text = REMINDER_RE.sub("", text)
    text = COMMAND_RE.sub("", text)
    text = text.strip()
    lowered = text.lower()
    if not text or any(lowered.startswith(prefix) for prefix in INJECTED_PREFIXES):
        return ""
    if text.startswith("<") and text.endswith(">") and "\n" not in text:
        return ""  # a lone tag, e.g. a reminder with the text removed
    return text[:MAX_QUESTION]


def _relativize(text: str, root: Path | None) -> str:
    """A path inside the repo, relative to it.

    Both spellings are tried: on macOS `/var` is a symlink to `/private/var`, so
    a session may record either one.
    """
    if root is None or not text.startswith("/"):
        return text
    root = Path(root)
    prefixes = list(dict.fromkeys([str(root), str(root.resolve())]))
    try:
        candidates = list(dict.fromkeys([text, str(Path(text).resolve())]))
    except OSError:
        candidates = [text]
    for candidate in candidates:
        for prefix in prefixes:
            if candidate == prefix:
                return "."
            if candidate.startswith(prefix + os.sep) or candidate.startswith(prefix + "/"):
                return candidate[len(prefix) + 1:]
    return text


def clean_input(value, root: Path | None = None, depth: int = 0):
    """Tool arguments, minus anything that carries file contents.

    Both clients pass a write's or an edit's body in the arguments. Those keys
    are dropped, long strings are cut, and absolute paths inside the repository
    become relative, which is how the rest of the log names files.
    """
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        found = {}
        for key, item in value.items():
            if str(key) in DROP_KEYS:
                continue
            found[str(key)] = clean_input(item, root, depth + 1)
        if depth == 0 and len(json.dumps(found, default=str)) > MAX_INPUT:
            # Keep the short arguments; a subagent's instructions or a list of
            # options can still add up.
            found = {
                key: item for key, item in found.items()
                if len(json.dumps(item, default=str)) <= MAX_VALUE * 2
            }
            found["_note"] = "long arguments trimmed"
        return found
    if isinstance(value, (list, tuple)):
        return [clean_input(item, root, depth + 1) for item in value[:10]]
    if isinstance(value, str):
        value = _relativize(value, root)
        if "\n" in value:
            value = value.splitlines()[0]
        return value if len(value) <= MAX_VALUE else value[:MAX_VALUE] + "..."
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_VALUE]


def _read_jsonl(path: Path):
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


# -- Claude Code ----------------------------------------------------------


def claude_projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return Path(base).expanduser() / "projects"


def _first_cwd(path: Path) -> Path | None:
    for index, entry in enumerate(_read_jsonl(path)):
        if index > 20:
            break
        cwd = entry.get("cwd")
        if cwd:
            try:
                return Path(cwd).resolve()
            except OSError:
                return None
    return None


def claude_project(root: Path) -> Path | None:
    """The project folder whose sessions ran in `root`, matching by recorded cwd."""
    base = claude_projects_dir()
    if not base.is_dir():
        return None
    root = Path(root).resolve()
    for candidate in sorted(base.iterdir()):
        if not candidate.is_dir():
            continue
        for path in sorted(candidate.glob("*.jsonl"))[:3]:
            if _first_cwd(path) == root:
                return candidate
    return None


def claude_questions(directory: Path, root: Path):
    """One question per real prompt, with the tool calls that followed it."""
    for path in sorted(directory.glob("*.jsonl")):
        session = path.stem
        current = None
        calls_by_id = {}
        for entry in _read_jsonl(path):
            session = entry.get("sessionId") or session
            kind = entry.get("type")
            stamp = entry.get("timestamp")
            epoch = _epoch_iso(stamp)
            if kind == "user":
                message = entry.get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        call = calls_by_id.pop(block.get("tool_use_id"), None)
                        if call is None:
                            continue
                        call["result_bytes"] = _size(block.get("content"))
                        call["is_error"] = bool(block.get("is_error"))
                        if epoch and call.get("raw_at"):
                            call["duration_ms"] = max(0, int((epoch - call["raw_at"]) * 1000))
                        call.pop("raw_at", None)
                        if current is not None and epoch:
                            current["end"] = max(current["end"], epoch)
                    continue
                if not isinstance(content, str) or entry.get("isMeta") or entry.get("isSidechain"):
                    continue
                text = clean_question(content)
                if not text:
                    continue
                if current is not None:
                    yield _finish_claude(current)
                current = {
                    "external_id": "%s:%s:%s" % (CLAUDE, session, entry.get("uuid") or len(calls_by_id)),
                    "at": _local(epoch) if epoch else "",
                    "question": text,
                    "start": epoch,
                    "end": epoch,
                    "turns": 0,
                    "calls": [],
                }
                calls_by_id = {}
            elif kind == "assistant" and current is not None:
                current["turns"] += 1
                if epoch:
                    current["end"] = max(current["end"], epoch)
                content = (entry.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") in ANSWER_TOOLS:
                        continue
                    call = {
                        "tool": block.get("name") or "tool",
                        "input": clean_input(block.get("input"), root),
                        "at": _local(epoch) if epoch else "",
                        "raw_at": epoch,
                        "source": CLAUDE,
                    }
                    current["calls"].append(call)
                    calls_by_id[block.get("id")] = call
        if current is not None:
            yield _finish_claude(current)


def _finish_claude(current: dict) -> Question:
    for call in current["calls"]:
        call.pop("raw_at", None)
    start, end = current["start"], current["end"]
    return Question(
        external_id=current["external_id"],
        at=current["at"],
        question=current["question"],
        client=CLAUDE,
        session=current["external_id"].split(":")[1],
        turns=current["turns"],
        duration_ms=max(0, int((end - start) * 1000)) if start and end else 0,
        calls=current["calls"],
    )


# -- OpenCode -------------------------------------------------------------


def _run(command: list, cwd: Path) -> str:
    try:
        found = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HistoryError("could not run `%s`: %s" % (" ".join(command), exc)) from exc
    if found.returncode != 0:
        detail = (found.stderr or found.stdout).strip().splitlines()
        raise HistoryError(
            "`%s` failed: %s" % (" ".join(command), detail[-1] if detail else "no output")
        )
    return found.stdout


def opencode_available() -> bool:
    return shutil.which(OPENCODE) is not None


def opencode_sessions(root: Path) -> list:
    root = Path(root).resolve()
    try:
        listed = json.loads(_run([OPENCODE, "session", "list", "--format", "json", "-n", "200"], root))
    except ValueError as exc:
        raise HistoryError("OpenCode's session list was not JSON: %s" % exc) from exc
    items = listed if isinstance(listed, list) else listed.get("sessions") or []
    found = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        directory = item.get("directory")
        if directory and Path(directory).resolve() != root:
            continue
        found.append(item)
    return found


def opencode_questions(root: Path):
    """Every question and its calls, plus the Claude sessions these cover."""
    root = Path(root).resolve()
    covered = set()
    questions = []
    for session in opencode_sessions(root):
        try:
            data = json.loads(_run([OPENCODE, "session", "export", session["id"]], root))
        except ValueError as exc:
            raise HistoryError("OpenCode's export of %s was not JSON: %s" % (session["id"], exc)) from exc
        info = data.get("info") or {}
        session_id = info.get("id") or session["id"]
        current = None
        for message in data.get("messages") or []:
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            created = (message.get("time") or {}).get("created")
            if kind == "user":
                text = clean_question(message.get("text"))
                if not text:
                    continue
                if current is not None:
                    questions.append(_finish_opencode(current))
                current = {
                    "external_id": "%s:%s:%s" % (OPENCODE, session_id, message.get("id")),
                    "at": _at_ms(created),
                    "question": text,
                    "session": session_id,
                    "turns": 0,
                    "cost": 0.0,
                    "model": "",
                    "start": created or 0,
                    "end": created or 0,
                    "calls": [],
                }
            elif kind == "assistant" and current is not None:
                current["turns"] += 1
                current["cost"] += float(message.get("cost") or 0)
                model = message.get("model") or {}
                if model.get("id"):
                    current["model"] = str(model["id"])
                if created:
                    current["end"] = max(current["end"], created)
                for block in message.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool":
                        continue
                    if block.get("name") in ANSWER_TOOLS:
                        continue
                    native = block.get("native") or {}
                    if isinstance(native.get("sessionID"), str):
                        covered.add(native["sessionID"])
                    state = block.get("state") or {}
                    current["calls"].append({
                        "tool": block.get("name") or "tool",
                        "input": clean_input(state.get("input"), root),
                        "at": _at_ms(((block.get("time") or {}).get("created")) or created),
                        "result_bytes": _size(state.get("content")),
                        "is_error": state.get("status") == "error",
                        "source": OPENCODE,
                    })
        if current is not None:
            questions.append(_finish_opencode(current))
    return questions, covered


def _finish_opencode(current: dict) -> Question:
    start, end = current["start"], current["end"]
    return Question(
        external_id=current["external_id"],
        at=current["at"],
        question=current["question"],
        client=OPENCODE,
        session=current["session"],
        turns=current["turns"],
        duration_ms=max(0, int(end - start)) if start and end else 0,
        cost_usd=round(current["cost"], 6),
        model=current["model"],
        calls=current["calls"],
    )


# -- the import -----------------------------------------------------------


def import_sessions(root: Path, source: str = "all", on_progress=None) -> dict:
    """Copy both stores into the log; a question already there is skipped.

    Returns counts per source, plus `skipped` and `notes` for what was not
    available. The log is created when it does not exist yet: importing is an
    explicit request to keep this history.
    """
    root = Path(root).resolve()
    if source == "claude":
        source = CLAUDE
    created = False
    if not log_mod.enabled(root):
        log_mod.enable(root)
        created = True
    known = log_mod.session_ids(root)
    existing = log_mod.external_ids(root)
    counts = {"created": created, "updated": 0, "notes": [], "sources": {}}

    batches = []
    covered = set()
    if source in ("all", OPENCODE):
        if not opencode_available():
            counts["notes"].append("OpenCode is not installed; its sessions were not read")
        else:
            questions, covered = opencode_questions(root)
            batches.append((OPENCODE, questions))
    if source in ("all", CLAUDE):
        directory = claude_project(root)
        if directory is None:
            counts["notes"].append("no Claude Code sessions recorded for this directory")
        else:
            questions = [
                question
                for question in claude_questions(directory, root)
                if question.session not in covered and question.session not in known
            ]
            batches.append((CLAUDE, questions))

    for client, questions in batches:
        found = {"questions": 0, "updated": 0, "calls": 0}
        for question in questions:
            seen = question.external_id in existing
            ask_id = log_mod.record_ask(
                root,
                question.question,
                "",
                session_id=question.session,
                model=question.model,
                duration_ms=question.duration_ms,
                cost_usd=question.cost_usd,
                status="imported",
                client=client,
                turns=question.turns,
                calls=question.calls,
                source=client,
                external_id=question.external_id,
                at=question.at,
                refresh=True,
            )
            if ask_id is not None:
                found["updated" if seen else "questions"] += 1
                found["calls"] += len(question.calls)
                existing.add(question.external_id)
            if on_progress is not None:
                on_progress(question)
        counts["sources"][client] = found
        counts["updated"] += found["updated"]
    return counts
