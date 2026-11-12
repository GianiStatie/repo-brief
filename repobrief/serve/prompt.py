"""The prompt every runner gives `ask`'s agent, assembled from Markdown files.

The base instructions, a section for each tool family the agent has, the answer
format for the audience, then what this repository says about itself: its
overview, and `.repo-brief/prompt.md` when the owner wrote one. Read on every
ask, so an edit applies without a restart.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import tools as tools_mod

PROMPTS = Path(__file__).resolve().parent / "prompts"
NOTES = ".repo-brief/prompt.md"
OVERVIEW_LIMIT = 8000
COMMENT_RE = re.compile(r"<!--.*?-->\n?", re.S)


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def _overview(root: Path) -> str:
    text = COMMENT_RE.sub("", tools_mod.overview_text(root))
    lines = [line for line in text.splitlines() if not line.startswith("# ")]
    text = "\n".join(lines).strip()
    if len(text) > OVERVIEW_LIMIT:
        text = text[:OVERVIEW_LIMIT] + "\n\n(The overview continues: call repo_brief_overview.)"
    return text


def build(root: Path, audience: str, families, structured: bool = True) -> str:
    """`families` names the tool sections to include, e.g. ["brief", "inspect"].

    `structured` is for runners that get the answer as a schema (the Claude
    runner); the others are told to hand it over with the answer tool.
    """
    root = Path(root)
    parts = [_read("system.md")]
    parts += [_read("tools-%s.md" % family) for family in families]
    parts.append(_read("answer-%s.md" % audience))
    if not structured:
        parts.append(_read("answer-tool.md"))
    overview = _overview(root)
    if overview:
        parts.append("## This repository\n\n" + overview)
    try:
        notes = (root / NOTES).read_text(encoding="utf-8").strip()
    except OSError:
        notes = ""
    if notes:
        parts.append("## Notes for this repository\n\n" + notes)
    return "\n\n".join(parts) + "\n"
