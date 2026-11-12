"""An optional record of every `ask`, kept on this machine.

Nothing is recorded until `repo-brief log --enable` creates the database; from
then on every ask in that repository is added to it, and deleting the file turns
it off. The log is one SQLite database per repository, under the user's data
directory, never in the repo and never committed.

What it keeps is what `tools suggest` will mine: the question and the answer,
the tool calls the agent made to get there (the grep patterns and file reads
are the useful part), and what it cost. Python's standard library includes
sqlite3, so this costs the core no dependency. A failure to write is swallowed:
an answer is never lost because its history could not be saved.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
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
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    ask_id INTEGER,
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    tool TEXT NOT NULL,
    input TEXT,
    result_bytes INTEGER,
    duration_ms INTEGER,
    is_error INTEGER
);
CREATE INDEX IF NOT EXISTS calls_ask ON calls(ask_id);
"""
# Columns added after the first version of `asks`; an older file gains them on
# open, so a log kept across upgrades stays one table.
ASK_COLUMNS = {
    "client": "TEXT",
    "runner": "TEXT",
    "audience": "TEXT",
    "turns": "INTEGER",
    "commit_sha": "TEXT",
    "references_json": "TEXT",
    "gaps_json": "TEXT",
}


def data_dir() -> Path:
    if value := os.environ.get(ENV_DIR):
        return Path(value).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "repo-brief"


def path_for(root: Path) -> Path:
    key = hashlib.blake2b(str(Path(root).resolve()).encode(), digest_size=6).hexdigest()
    return data_dir() / ("%s-%s.sqlite" % (Path(root).resolve().name, key))


def enabled(root: Path) -> bool:
    return path_for(root).is_file()


def _open(target: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(target))
    conn.executescript(SCHEMA)
    present = {row[1] for row in conn.execute("PRAGMA table_info(asks)")}
    for name, kind in ASK_COLUMNS.items():
        if name not in present:
            conn.execute("ALTER TABLE asks ADD COLUMN %s %s" % (name, kind))
    conn.commit()
    return conn


def enable(root: Path) -> Path:
    """Create the log for this repository; recording starts with the next ask."""
    target = path_for(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    _open(target).close()
    return target


def _commit(root: Path) -> str:
    try:
        found = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return found.stdout.strip() if found.returncode == 0 else ""


def record_ask(
    root: Path,
    question: str,
    answer: str,
    *,
    session_id: str = "",
    model: str = "",
    duration_ms: int = 0,
    cost_usd: float = 0.0,
    status: str = "ok",
    client: str = "",
    runner: str = "",
    audience: str = "",
    turns: int = 0,
    references=(),
    gaps=(),
    calls=(),
):
    """Add one ask and its tool calls; a no-op unless the log is enabled.

    Returns the ask's row id, or None when nothing was written.
    """
    target = path_for(root)
    if not target.is_file():
        return None
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        conn = _open(target)
        try:
            with conn:
                ask_id = conn.execute(
                    "INSERT INTO asks (at, repo, question, answer, session_id, model, duration_ms, "
                    "cost_usd, status, client, runner, audience, turns, commit_sha, "
                    "references_json, gaps_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        now,
                        str(Path(root).resolve()),
                        question,
                        answer,
                        session_id,
                        model,
                        duration_ms,
                        cost_usd,
                        status,
                        client,
                        runner,
                        audience,
                        turns,
                        _commit(root),
                        json.dumps(list(references)),
                        json.dumps(list(gaps)),
                    ),
                ).lastrowid
                conn.executemany(
                    "INSERT INTO calls (ask_id, at, source, tool, input, result_bytes, "
                    "duration_ms, is_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            ask_id,
                            call.get("at") or now,
                            call.get("source") or "ask",
                            call.get("tool") or "",
                            json.dumps(call.get("input") or {}, sort_keys=True),
                            call.get("result_bytes") or 0,
                            call.get("duration_ms") or 0,
                            1 if call.get("is_error") else 0,
                        )
                        for call in calls
                    ],
                )
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None
    return ask_id


def recent(root: Path, limit: int = 10) -> list[dict]:
    target = path_for(root)
    if not target.is_file():
        return []
    keys = (
        "id", "at", "question", "status", "model", "duration_ms", "cost_usd",
        "session_id", "client", "runner", "turns",
    )
    try:
        conn = _open(target)
        try:
            rows = conn.execute(
                "SELECT %s FROM asks ORDER BY id DESC LIMIT ?" % ", ".join(keys),
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    return [dict(zip(keys, row)) for row in rows]


def calls_for(root: Path, ask_id: int) -> list[dict]:
    target = path_for(root)
    if not target.is_file():
        return []
    keys = ("tool", "input", "result_bytes", "duration_ms", "is_error")
    try:
        conn = _open(target)
        try:
            rows = conn.execute(
                "SELECT %s FROM calls WHERE ask_id = ? ORDER BY id" % ", ".join(keys),
                (ask_id,),
            ).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    return [dict(zip(keys, row)) for row in rows]


def summary(root: Path) -> dict:
    target = path_for(root)
    if not target.is_file():
        return {}
    try:
        conn = _open(target)
        try:
            count, cost, millis = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0), COALESCE(SUM(duration_ms), 0) FROM asks"
            ).fetchone()
            calls = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return {}
    return {
        "asks": count,
        "calls": calls,
        "cost_usd": round(cost, 4),
        "seconds": round(millis / 1000, 1),
    }
