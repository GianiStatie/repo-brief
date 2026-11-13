"""What `ask` returns, whichever runner produced it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

STATUSES = ("ok", "max_turns", "max_budget", "timeout", "error")
NOTICES = {
    "max_turns": "Stopped at the turn limit; the answer may be partial.",
    "max_budget": "Stopped at the budget limit; the answer may be partial.",
    "timeout": "Stopped at the time limit; the answer may be partial.",
    "error": "The agent failed.",
}

# The JSON every runner asks its agent to fill in.
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "The answer, in Markdown, for the reader your instructions name.",
        },
        "references": {
            "type": "array",
            "description": (
                "One entry per file the answer relies on: files you read, or file:line "
                "locations a tool returned."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative path."},
                    "lines": {
                        "type": "string",
                        "description": 'Lines that support the claim, e.g. "12-40"; "" for the whole file.',
                    },
                    "note": {"type": "string", "description": "What this file shows, in a few words."},
                },
                "required": ["path", "lines", "note"],
                "additionalProperties": False,
            },
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What could not be established from the repository. Empty if nothing.",
        },
    },
    "required": ["answer", "references", "gaps"],
    "additionalProperties": False,
}

# "12", "12-40", or several of those separated by commas.
LINES_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def check_references(root: Path, references) -> list:
    """One problem per reference that doesn't match the files; none when all do.

    This catches invented paths and line ranges, not wrong claims: whether the
    cited lines support the answer still needs a reader.
    """
    root = Path(root).resolve()
    problems = []
    for ref in references:
        path = str(ref.get("path") or "").strip()
        lines = str(ref.get("lines") or "").strip()
        problem = _problem(root, path, lines)
        if problem:
            problems.append("%s: %s" % ("%s:%s" % (path, lines) if lines else path, problem))
    return problems


def _problem(root: Path, path: str, lines: str):
    if not path:
        return "no path"
    file = (root / path).resolve()
    if not file.is_relative_to(root):
        return "outside the repository"
    if not file.exists():
        return "no such file"
    if not lines:
        return None
    if not file.is_file():
        return "not a file"
    with file.open("rb") as handle:
        count = sum(1 for _ in handle)
    for part in lines.split(","):
        match = LINES_RE.match(part)
        if match is None:
            return 'lines should look like "12" or "12-40"'
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if not 1 <= start <= end <= count:
            return "lines %s are not in the file (%d lines)" % (part.strip(), count)
    return None


@dataclass
class AskResult:
    answer: str
    references: list = field(default_factory=list)
    # From check_references: what `ask` found wrong with the references.
    reference_problems: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    session_id: str = ""
    status: str = "ok"
    turns: int = 0
    duration_ms: int = 0
    cost_usd: float = 0.0
    model: str = ""
    runner: str = ""
    # One dict per tool the agent called: tool, input, at, result_bytes,
    # duration_ms, is_error. The log keeps them when it is on.
    calls: list = field(default_factory=list)

    def render(self) -> str:
        lines = []
        if self.status in NOTICES:
            lines += [NOTICES[self.status], ""]
        lines.append(self.answer.strip() or "(no answer)")
        if self.references:
            lines += ["", "References:"]
            for ref in self.references:
                where = str(ref.get("path") or "")
                if ref.get("lines"):
                    where += ":%s" % ref["lines"]
                note = " - %s" % ref["note"] if ref.get("note") else ""
                lines.append("- %s%s" % (where, note))
        if self.reference_problems:
            lines += ["", "Reference problems:"] + ["- %s" % problem for problem in self.reference_problems]
        if self.gaps:
            lines += ["", "Gaps:"] + ["- %s" % gap for gap in self.gaps]
        if self.session_id:
            lines += ["", "Session: %s (pass it back to ask a follow-up)" % self.session_id]
        return "\n".join(lines)
