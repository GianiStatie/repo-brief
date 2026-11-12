"""An optional record of every `ask`, kept on this machine.

Nothing is recorded until `repo-brief log --enable` creates the database; from
then on every ask in that repository is added to it, and deleting the file turns
it off. The log is one SQLite database per repository, under the user's data
directory, never in the repo and never committed.

What it keeps is what `tools suggest` will mine: the question and the answer,
the tool calls the agent made to get there (the grep patterns and file reads
are the useful part), the MCP calls an orchestrator made itself, and what all of
it cost. `log import` adds questions and tool calls from Claude Code's and
OpenCode's own session stores, with an external id per question so importing
twice adds nothing twice.

Python's standard library includes sqlite3, so this costs the core no
dependency. A failure to write is swallowed: an answer is never lost because its
history could not be saved.
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
# Columns added after the first version of each table; an older file gains them
# on open, so a log kept across upgrades stays one file.
ASK_COLUMNS = {
    "client": "TEXT",
    "runner": "TEXT",
    "audience": "TEXT",
    "turns": "INTEGER",
    "commit_sha": "TEXT",
    "references_json": "TEXT",
    "gaps_json": "TEXT",
    "source": "TEXT",
    "external_id": "TEXT",
}
CALL_COLUMNS = {
    "client": "TEXT",
    "client_session": "TEXT",
}
# An imported question is identified by where it came from, so importing the
# same sessions again changes nothing. A normal ask has no external id.
EXTERNAL_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS asks_external ON asks(external_id) "
    "WHERE external_id IS NOT NULL"
)


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


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    present = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
    for name, kind in columns.items():
        if name not in present:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, kind))


def _open(target: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(target))
    conn.executescript(SCHEMA)
    _add_columns(conn, "asks", ASK_COLUMNS)
    _add_columns(conn, "calls", CALL_COLUMNS)
    conn.execute(EXTERNAL_INDEX)
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
    source: str = "ask",
    external_id: str = "",
    at: str = "",
    refresh: bool = False,
):
    """Add one ask and its tool calls; a no-op unless the log is enabled.

    Returns the ask's row id, or None when nothing was written: the log is off,
    or a question with this `external_id` is already there and `refresh` is
    false. `refresh` rewrites that question and its calls, which is how a
    session imported while it was still running is completed later.
    """
    target = path_for(root)
    if not target.is_file():
        return None
    now = at or time.strftime("%Y-%m-%dT%H:%M:%S")
    common = (
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
        source,
    )
    columns = (
        "at, repo, question, answer, session_id, model, duration_ms, cost_usd, status, "
        "client, runner, audience, turns, commit_sha, references_json, gaps_json, source"
    )
    sql = "INSERT INTO asks (%s, external_id) VALUES (%s)" % (
        columns, ", ".join("?" * (len(common) + 1))
    )
    try:
        conn = _open(target)
        try:
            with conn:
                ask_id = None
                known = None
                if external_id:
                    known = conn.execute(
                        "SELECT id FROM asks WHERE external_id = ?", (external_id,)
                    ).fetchone()
                if known is not None:
                    if not refresh:
                        return None
                    ask_id = known[0]
                    conn.execute(
                        "UPDATE asks SET %s WHERE id = ?"
                        % ", ".join("%s = ?" % name.strip() for name in columns.split(",")),
                        common + (ask_id,),
                    )
                    conn.execute("DELETE FROM calls WHERE ask_id = ?", (ask_id,))
                else:
                    ask_id = conn.execute(sql, common + (external_id or None,)).lastrowid
                conn.executemany(
                    "INSERT INTO calls (ask_id, at, source, tool, input, result_bytes, "
                    "duration_ms, is_error, client, client_session) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                            client,
                            session_id,
                        )
                        for call in calls
                    ],
                )
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None
    return ask_id


def record_call(
    root: Path,
    tool: str,
    input=None,
    *,
    client: str = "",
    client_session: str = "",
    result_bytes: int = 0,
    duration_ms: int = 0,
    is_error: bool = False,
    at: str = "",
):
    """Add one tool call an MCP client made itself, outside any `ask`."""
    target = path_for(root)
    if not target.is_file():
        return None
    try:
        conn = _open(target)
        try:
            with conn:
                return conn.execute(
                    "INSERT INTO calls (ask_id, at, source, tool, input, result_bytes, "
                    "duration_ms, is_error, client, client_session) "
                    "VALUES (NULL, ?, 'mcp', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        at or time.strftime("%Y-%m-%dT%H:%M:%S"),
                        tool,
                        json.dumps(input or {}, sort_keys=True),
                        result_bytes,
                        duration_ms,
                        1 if is_error else 0,
                        client,
                        client_session,
                    ),
                ).lastrowid
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None


def recent(root: Path, limit: int = 10) -> list[dict]:
    target = path_for(root)
    if not target.is_file():
        return []
    keys = (
        "id", "at", "question", "status", "model", "duration_ms", "cost_usd",
        "session_id", "client", "runner", "turns", "source",
    )
    try:
        conn = _open(target)
        try:
            rows = conn.execute(
                "SELECT %s FROM asks ORDER BY at DESC, id DESC LIMIT ?" % ", ".join(keys),
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


def recent_calls(root: Path, limit: int = 20) -> list[dict]:
    """The client's own MCP calls, which belong to no ask."""
    target = path_for(root)
    if not target.is_file():
        return []
    keys = ("at", "tool", "input", "client", "client_session", "result_bytes", "is_error")
    try:
        conn = _open(target)
        try:
            rows = conn.execute(
                "SELECT %s FROM calls WHERE ask_id IS NULL ORDER BY id DESC LIMIT ?"
                % ", ".join(keys),
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    return [dict(zip(keys, row)) for row in rows]


def external_ids(root: Path) -> set:
    """The imported questions already in the log, by where they came from."""
    target = path_for(root)
    if not target.is_file():
        return set()
    try:
        conn = _open(target)
        try:
            rows = conn.execute(
                "SELECT external_id FROM asks WHERE external_id IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return set()
    return {row[0] for row in rows}


def session_ids(root: Path, include_imported: bool = False) -> set:
    """The agent sessions repo-brief ran here, whose files an import can skip.

    Imported sessions are left out: their own external ids de-duplicate them,
    so a session that was only partly imported can be completed by a later run.
    """
    target = path_for(root)
    if not target.is_file():
        return set()
    sql = "SELECT session_id FROM asks WHERE session_id IS NOT NULL AND session_id != ''"
    if not include_imported:
        sql += " AND (source IS NULL OR source = 'ask')"
    try:
        conn = _open(target)
        try:
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return set()
    return {row[0] for row in rows}


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
            imported = conn.execute(
                "SELECT COUNT(*) FROM asks WHERE source IS NOT NULL AND source != 'ask'"
            ).fetchone()[0]
            calls = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return {}
    return {
        "asks": count,
        "imported": imported,
        "calls": calls,
        "cost_usd": round(cost, 4),
        "seconds": round(millis / 1000, 1),
    }
