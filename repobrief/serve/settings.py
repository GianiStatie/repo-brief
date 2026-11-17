"""Settings for `serve`: `.repo-brief/serve.json`, then the environment.

The file is committed in a repo that commits its derived layer, so everyone who
clones it serves with the same limits. An environment variable overrides one
setting for one process, which is how a deploy changes them without an edit. A
value that does not make sense falls back to the default.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

FILE = "serve.json"
ENV = {
    "script_timeout_seconds": "REPO_BRIEF_SCRIPT_TIMEOUT",
}
# The description is text, not a limit, so it lives beside the settings rather
# than among them: no environment override, no fallback to a default.
DESCRIPTION_KEY = "description"
DESCRIPTION_LIMIT = 300


@dataclass
class Settings:
    script_timeout_seconds: float = 600.0


def path_for(root: Path) -> Path:
    return Path(root) / ".repo-brief" / FILE


def _cast(value, default):
    try:
        cast = type(default)(value)
    except (TypeError, ValueError):
        return default
    if isinstance(cast, (int, float)) and cast <= 0:
        return default
    return cast


def load(root: Path) -> Settings:
    defaults = Settings()
    try:
        data = json.loads(path_for(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    values = {}
    for item in fields(Settings):
        default = getattr(defaults, item.name)
        value = data.get(item.name, default)
        override = os.environ.get(ENV[item.name])
        if override not in (None, ""):
            value = override
        values[item.name] = _cast(value, default)
    return Settings(**values)


def _raw(root: Path) -> dict:
    try:
        data = json.loads(path_for(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def description(root: Path) -> str:
    """The one line that tells this repository's server apart from another's.

    A client that connects several repositories sees the same tool names from
    each; the line is what it routes a question by. "" until someone sets it.
    """
    value = _raw(root).get(DESCRIPTION_KEY, "")
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:DESCRIPTION_LIMIT]


def set_description(root: Path, text: str) -> str:
    """Store the line, on one line and within the limit; "" removes it.

    The other settings in the file stay as they are.
    """
    line = " ".join(text.split())
    if len(line) > DESCRIPTION_LIMIT:
        raise ValueError(
            "the description is %d characters; keep it under %d" % (len(line), DESCRIPTION_LIMIT)
        )
    data = _raw(root)
    if line:
        data[DESCRIPTION_KEY] = line
    else:
        data.pop(DESCRIPTION_KEY, None)
    target = path_for(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return line


def write_defaults(root: Path):
    """Write the defaults once, so the settings are visible and editable."""
    target = path_for(root)
    if target.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(Settings()), indent=2) + "\n", encoding="utf-8")
    return target
