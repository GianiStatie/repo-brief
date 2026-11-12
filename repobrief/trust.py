"""Which repositories repo-brief may run code from.

Trust is a machine-local fact, not a repository one: a repo that could grant
itself trust would not be a gate. A trusted repo's adapter, extractors and tools
may run; an untrusted repo still gets the map, the overview, `context`, `graph`
and `ask`, with repo-brief's own tools and the generic adapter.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FILE = "trust.json"
ENV_DIR = "REPO_BRIEF_CONFIG_DIR"


def config_dir() -> Path:
    if value := os.environ.get(ENV_DIR):
        return Path(value).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "repo-brief"


def path() -> Path:
    return config_dir() / FILE


def _load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> Path:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _key(root: Path) -> str:
    return str(Path(root).resolve())


def is_trusted(root: Path) -> bool:
    return _key(root) in _load().get("trusted", {})


def trusted() -> dict:
    return dict(_load().get("trusted", {}))


def trust(root: Path, note: str = "") -> Path:
    data = _load()
    trusted_map = data.setdefault("trusted", {})
    trusted_map[_key(root)] = note or ""
    return _save(data)


def untrust(root: Path) -> Path:
    data = _load()
    data.get("trusted", {}).pop(_key(root), None)
    return _save(data)
