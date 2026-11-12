"""A word index beside the map, so a query opens only the files it can hit.

`context` used to read every source file on every call: scoring needed the
parsed fields, the usage scan needed every line, and the read plan needed the
text again. On a large repo that reading is most of the cost.

This keeps, per file, what parsing produced (fields, symbols and the words the
file uses), plus one row per word. A query asks which files could contain a
token; only those files are opened, then the same `best_line` ranks them. The
index is local, rebuilt on demand, and kept current from the file stats: a file
whose size and modification time still match is not read at all.

Words are matched as whole words or prefixes, not arbitrary substrings. The old
scan matched `cat` inside `concatenate`; that was a false positive, not a feature,
and it is the one deliberate difference in what `context` returns.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

from . import parsers
from .cache import parser_version
from .contract import Unit
from .detect import STATE_DIR, detect, is_archived

INDEX_FILE = "index.sqlite"
# Bumped when the rows this module writes change shape. The parser stamp is
# folded in below, so a parser change rebuilds too.
INDEX_VERSION = "1"
WORD_RE = re.compile(r"[A-Za-z0-9_]+")
# `HermitPurple` is also `hermit` and `purple`: a query
# names the word a developer would say, not the concatenation.
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# Identifiers keep their case for graph inference; qualified Java names keep
# their dots, so a map can still resolve `com.example.jojo.Diary.record`.
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
QUALIFIED_RE = re.compile(r"\b(?:[a-z_][\w$]*\.)+[A-Z][\w$]*")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INTEGER,
    mtime_ns INTEGER,
    kind TEXT,
    fields TEXT,
    symbols TEXT,
    words TEXT
);
CREATE TABLE IF NOT EXISTS words (word TEXT, path TEXT);
CREATE INDEX IF NOT EXISTS words_word ON words(word);
CREATE INDEX IF NOT EXISTS words_path ON words(path);
"""


def index_path(root: Path) -> Path:
    return root / STATE_DIR / INDEX_FILE


def _version() -> str:
    return "%s/%s" % (INDEX_VERSION, parser_version())


def words_for(text: str) -> set[str]:
    """Every word a line-level query could name, lowercased.

    The whole token and its CamelCase pieces: `BearerToken` is found by `bearer`
    and by `token`, which is how the old substring scan behaved for the useful
    cases. A word that appears anywhere in the file is enough here; the exact
    line is computed from the file text once the file is a candidate.
    """
    found = set()
    for raw in WORD_RE.findall(text):
        lowered = raw.lower()
        found.add(lowered)
        pieces = CAMEL_RE.split(raw)
        if len(pieces) > 1:
            found.update(piece.lower() for piece in pieces if piece)
    return found


class Index:
    """One open index. `ensure` refreshes it; the rest only reads."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = index_path(self.root)
        self.conn = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def connect(self, create: bool = False) -> "Index":
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        return self

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def exists(self) -> bool:
        return self.path.is_file()

    # -- build -------------------------------------------------------------

    def ensure(self, adapter, force: bool = False) -> dict:
        """Refresh from the tree, then leave it ready to query.

        Only files whose size or modification time changed are read and
        parsed. An adapter that overrides `units()` is not served by the index:
        its own transformation is the source of truth, and guessing at it here
        would make the map disagree with `context`.
        """
        from .adapters.generic import BINARY_EXTS, GenericAdapter, is_generated

        if type(adapter).units is not GenericAdapter.units:
            self.close()
            return {"skipped": "adapter overrides units()"}
        detected = adapter.detected(self.root)
        if self.conn is None:
            self.connect(create=True)
        started = time.time()
        stored = {
            row[0]: (row[1], row[2])
            for row in self.conn.execute("SELECT path, size, mtime_ns FROM files")
        }
        version = _version()
        fresh = self.conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        if fresh is None or fresh[0] != version:
            force = True
        claimed = adapter._claimed_paths(self.root, detected)
        excluded = adapter.excluded(self.root)
        archived = adapter.archived(self.root)
        seen = set()
        parsed = kept = 0

        with self.conn:
            for relative in detected.files:
                rel = relative.as_posix()
                if rel in claimed or rel in excluded:
                    continue
                path = self.root / relative
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if not force and stored.get(rel) == (stat.st_size, stat.st_mtime_ns):
                    seen.add(rel)
                    kept += 1
                    continue
                suffix = path.suffix.lower()
                reader = parsers.PARSERS.get(suffix)
                if reader is not None:
                    text = parsers.read_text(path)
                    if is_generated(text):
                        continue
                    unit = reader(self.root, path, text)
                    unit = parsers.resolve(self.root, unit)
                elif suffix not in BINARY_EXTS:
                    unit = parsers.parse_generic(self.root, path)
                else:
                    continue
                if is_archived(rel, archived):
                    unit.kind = "archive"
                file_words = sorted(words_for(unit.text))
                identifiers = sorted(set(IDENT_RE.findall(unit.text)))
                qualified = sorted(set(QUALIFIED_RE.findall(unit.text)))
                graph_words = identifiers + [name for name in qualified if name not in identifiers]
                self.conn.execute("DELETE FROM words WHERE path = ?", (rel,))
                self.conn.executemany(
                    "INSERT OR IGNORE INTO words (word, path) VALUES (?, ?)",
                    [(word, rel) for word in file_words],
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO files (path, size, mtime_ns, kind, fields, symbols, words) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        rel,
                        stat.st_size,
                        stat.st_mtime_ns,
                        unit.kind,
                        json.dumps(unit.fields, sort_keys=True),
                        json.dumps(unit.symbols, sort_keys=True),
                        json.dumps(graph_words),
                    ),
                )
                seen.add(rel)
                parsed += 1
            missing = [path for path in stored if path not in seen]
            if missing:
                self.conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in missing])
                self.conn.executemany("DELETE FROM words WHERE path = ?", [(p,) for p in missing])
            self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('version', ?)", (version,)
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"),),
            )
        total = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {
            "files": total,
            "kept": kept,
            "parsed": parsed,
            "seconds": round(time.time() - started, 2),
            "rebuilt": force,
        }

    # -- reads -------------------------------------------------------------

    def units(self) -> list[Unit]:
        rows = self.conn.execute(
            "SELECT path, kind, fields, symbols, words FROM files ORDER BY path"
        )
        found = []
        for path, kind, fields, symbols, words in rows:
            found.append(
                Unit(
                    path=path,
                    kind=kind,
                    fields=json.loads(fields or "{}"),
                    symbols=json.loads(symbols or "[]"),
                    text="",
                    words=json.loads(words or "[]"),
                )
            )
        return found

    def candidate_paths(self, tokens, roots=(), limit: int = 2000) -> list[str]:
        """Files that contain a token as a word or a prefix of one.

        `roots` mirrors the adapter's usage roots: a query word in `tools/` is
        not a usage site when the product code lives under `src/`.
        """
        found = set()
        for token in tokens:
            # BINARY collation: a range scan over `word` uses the index, where
            # LIKE would have to read every row.
            rows = self.conn.execute(
                "SELECT DISTINCT path FROM words WHERE word >= ? AND word < ?",
                (token, token + "\U0010ffff"),
            )
            found.update(row[0] for row in rows)
            if len(found) >= limit:
                break
        if roots:
            found = {
                path for path in found if any(path.startswith(root) for root in roots)
            }
        return sorted(found)[:limit]

    def text(self, rel: str) -> str:
        try:
            return parsers.read_text(self.root / rel)
        except OSError:
            return ""

    def counts(self) -> dict:
        try:
            files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            words = self.conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
            built = self.conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        except sqlite3.Error:
            return {}
        return {"files": files, "words": words, "built_at": built[0] if built else ""}


def ensure(root: Path, adapter, force: bool = False) -> tuple[list[Unit], "Index | None", dict]:
    """The units a command should use, plus the live index when it applies.

    A missing index is built. An adapter that owns `units()` gets no index and
    the caller falls back to it.
    """
    state = Index(root)
    stats = state.ensure(adapter, force=force)
    if "skipped" in stats:
        state.close()
        return adapter.units(root), None, stats
    return state.units(), state, stats
