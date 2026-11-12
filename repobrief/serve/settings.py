"""Settings for `serve` and `ask`: `.repo-brief/serve.json`, then the environment.

The file is committed in a repo that commits its derived layer, so everyone who
clones it asks with the same runner, model and caps. An environment variable
overrides one setting for one process, which is how a deploy changes them
without an edit. A value that does not make sense falls back to the default.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

FILE = "serve.json"
AUDIENCES = ("developer", "product")
ENV = {
    "runner": "REPO_BRIEF_RUNNER",
    "model": "REPO_BRIEF_MODEL",
    "max_turns": "REPO_BRIEF_MAX_TURNS",
    "max_budget_usd": "REPO_BRIEF_MAX_BUDGET_USD",
    "timeout_seconds": "REPO_BRIEF_TIMEOUT",
    "concurrency": "REPO_BRIEF_CONCURRENCY",
    "audience": "REPO_BRIEF_AUDIENCE",
    "script_timeout_seconds": "REPO_BRIEF_SCRIPT_TIMEOUT",
}


@dataclass
class Settings:
    runner: str = "claude"
    model: str = "claude-haiku-5-5"
    max_turns: int = 40
    max_budget_usd: float = 2.0
    timeout_seconds: float = 600.0
    concurrency: int = 2
    audience: str = "developer"
    script_timeout_seconds: float = 600.0


def path_for(root: Path) -> Path:
    return Path(root) / ".repo-brief" / FILE


def _cast(name: str, value, default):
    try:
        cast = type(default)(value)
    except (TypeError, ValueError):
        return default
    if isinstance(cast, (int, float)) and cast <= 0:
        return default
    if name == "audience" and cast not in AUDIENCES:
        return default
    if isinstance(cast, str) and not cast.strip():
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
        values[item.name] = _cast(item.name, value, default)
    return Settings(**values)


def write_defaults(root: Path):
    """Write the defaults once, so the settings are visible and editable."""
    target = path_for(root)
    if target.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(Settings()), indent=2) + "\n", encoding="utf-8")
    return target
