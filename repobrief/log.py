"""Every `ask`, kept on this machine so later work can learn from it.

The log is one SQLite database per repository, under the user's data directory,
never in the repo and never committed. `tools suggest` is meant to mine it for
repeated greps, files read together and questions that took many turns; the log
is useful on its own as a record of what was asked and what it cost.

Python's standard library includes sqlite3, so this costs the core no
dependency. A failure to write is swallowed: an answer is never lost because its
history could not be saved.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path

ENV_DIR = "REPO_BRIEF_DATA_DIR"
SCHEMA = """
CREATE TABLE IF NOT EXISTS asks (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    repo TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    session_id TEXT,
    model TEXT,
    duration_ms INTEGER,
    cost_usd REAL,
    status TEXT
);
CREATE INDEX IF NOT EXISTS asks_at ON asks(at);
"""


def data_dir() -> Path:
    if value := os.environ.get(ENV_DIR):
        return Path(value).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "repo-brief"


def path_for(root: Path) -> Path:
    key = hashlib.blake2b(str(Path(root).resolve()).encode(), digest_size=6).hexdigest()
    return data_dir() / ("%s-%s.sqlite" % (Path(root).resolve().name, key))


def record(
    root: Path,
    question: str,
    answer: str,
    *,
    session_id: str = "",
    model: str = "",
    duration_ms: int = 0,
    cost_usd: float = 0.0,
    status: str = "ok",
) -> None:
    try:
        target = path_for(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(target)) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO asks (at, repo, question, answer, session_id, model, "
                "duration_ms, cost_usd, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    str(Path(root).resolve()),
                    question,
                    answer,
                    session_id,
                    model,
                    duration_ms,
                    cost_usd,
                    status,
                ),
            )
    except (OSError, sqlite3.Error):
        return


def recent(root: Path, limit: int = 10) -> list[dict]:
    try:
        with sqlite3.connect(str(path_for(root))) as conn:
            conn.executescript(SCHEMA)
            rows = conn.execute(
                "SELECT at, question, status, model, duration_ms, cost_usd, session_id "
                "FROM asks ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return []
    keys = ("at", "question", "status", "model", "duration_ms", "cost_usd", "session_id")
    return [dict(zip(keys, row)) for row in rows]


def summary(root: Path) -> dict:
    try:
        with sqlite3.connect(str(path_for(root))) as conn:
            conn.executescript(SCHEMA)
            count, cost, turns = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0), COALESCE(SUM(duration_ms), 0) FROM asks"
            ).fetchone()
    except (OSError, sqlite3.Error):
        return {}
    return {"asks": count, "cost_usd": round(cost, 4), "seconds": round(turns / 1000, 1)}
