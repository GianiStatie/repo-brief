"""An optional word index beside the map, so a query opens only the files it can hit.

Without it, `context` reads every source file on every call: scoring needs the
parsed fields, the usage scan needs every line, and the read plan needs the text
again. On a large repo that reading is most of the cost.

The index keeps, per file, what parsing produced (fields, symbols and the words
the file uses) and its length in words, plus one row per word with how often the
file uses it. A query asks which files could contain a token; only those files
are opened, then the same `best_line` ranks them. The counts are what the kernel
ranks code bodies with (BM25); without the index it counts the same words from
the text it has read. A file whose size and modification time still match is not
read at all.

It is opt-in. Nothing creates it but a command run with `--index`; once it
exists every command keeps it current, and deleting the file turns it off. It
is local and git-ignored, like the parse cache.

Words are matched as whole words or prefixes, not arbitrary substrings. The full
scan matches `cat` inside `concatenate`; that is a false positive, not a feature, and
it is the one deliberate difference in what `context` returns with the index on.
"""
from __future__ import annotations

import functools
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from . import parsers
from .cache import ignore_local_files, parser_version
from .contract import Unit
from .detect import STATE_DIR, is_archived

INDEX_FILE = "index.sqlite"
# Bumped when the rows this module writes change shape. The parser stamp is
# folded in below, so a parser change rebuilds too. A mismatch deletes the file
# and starts again: it is a cache, and a fresh file is also a compact one.
INDEX_VERSION = "3"
WORD_RE = re.compile(r"[A-Za-z0-9_]+")
# `HermitPurple` is also `hermit` and `purple`: a query
# names the word a developer would say, not the concatenation.
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# Identifiers keep their case for graph inference; qualified Java names keep
# their dots, so a map can still resolve `com.example.jojo.Diary.record`.
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
QUALIFIED_RE = re.compile(r"\b(?:[a-z_][\w$]*\.)+[A-Z][\w$]*")

# One row per word and file, keyed by an integer file id: a path is stored once,
# not once per word, which is most of what made the first version of this file
# large.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    size INTEGER,
    mtime_ns INTEGER,
    kind TEXT,
    fields TEXT,
    symbols TEXT,
    words TEXT,
    length INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS words (
    word TEXT NOT NULL,
    file INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (word, file)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS words_file ON words(file);
"""


def index_path(root: Path) -> Path:
    return Path(root) / STATE_DIR / INDEX_FILE


def enabled(root: Path) -> bool:
    """Whether someone asked for the index. Its file existing is the answer."""
    return index_path(root).is_file()


def _version() -> str:
    return "%s/%s" % (INDEX_VERSION, parser_version())


@functools.lru_cache(maxsize=1 << 17)
def _forms(raw: str) -> tuple[str, ...]:
    """The words one token stands for: itself, and its CamelCase and snake_case pieces."""
    found = {raw.lower()}
    for part in raw.split("_"):
        found.update(piece.lower() for piece in CAMEL_RE.split(part) if piece)
    return tuple(found)


def word_counts(text: str) -> Counter:
    """How often the file uses each word: the term frequencies BM25 reads.

    A token counts once for every word it stands for, so `getStarFinger` adds one
    to `getstarfinger`, `get`, `star` and `finger`, and `stop_time` to `stop_time`,
    `stop` and `time`. Distinct tokens are expanded once per file, which keeps
    counting cheap enough to run on every query when there is no index.
    """
    counts: Counter = Counter()
    for raw, seen in Counter(WORD_RE.findall(text)).items():
        for word in _forms(raw):
            counts[word] += seen
    return counts


def words_for(text: str) -> set[str]:
    """Every word a line-level query could name, lowercased.

    The whole token and its pieces: `BearerToken` is found by `bearer` and by
    `token`, which is how the full scan behaves for the useful cases. A word that
    appears anywhere in the file is enough here; the exact line is computed from
    the file text once the file is a candidate.
    """
    return set(word_counts(text))


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
        """Open the file. Only `create` may make a new one."""
        if not create and not self.path.is_file():
            raise FileNotFoundError(self.path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # The file is local; make sure git agrees before it exists.
            ignore_local_files(self.path.parent)
        self.conn = sqlite3.connect(str(self.path))
        if self._stored_version() not in (None, _version()):
            # An older layout or parser: start from an empty, compact file.
            self.conn.close()
            self.path.unlink()
            self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        return self

    def _stored_version(self):
        try:
            row = self.conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None

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
            row[0]: (row[1], row[2], row[3])
            for row in self.conn.execute("SELECT path, size, mtime_ns, id FROM files")
        }
        version = _version()
        if self._stored_version() != version:
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
                previous = stored.get(rel)
                if not force and previous and previous[:2] == (stat.st_size, stat.st_mtime_ns):
                    seen.add(rel)
                    kept += 1
                    continue
                suffix = path.suffix.lower()
                reader = parsers.PARSERS.get(suffix)
                text = parsers.read_text(path) if reader is not None else ""
                if reader is not None and not is_generated(text):
                    unit = parsers.resolve(self.root, reader(self.root, path, text))
                elif suffix not in BINARY_EXTS:
                    # A generated file is listed by its path, like a file no
                    # parser reads: exactly what the full scan does, so turning
                    # the index on never changes the map.
                    unit = parsers.parse_generic(self.root, path)
                else:
                    continue
                if is_archived(rel, archived):
                    unit.kind = "archive"
                identifiers = sorted(set(IDENT_RE.findall(unit.text)))
                qualified = sorted(set(QUALIFIED_RE.findall(unit.text)))
                graph_words = identifiers + [name for name in qualified if name not in identifiers]
                counts = word_counts(unit.text)
                row = (
                    stat.st_size,
                    stat.st_mtime_ns,
                    unit.kind,
                    json.dumps(unit.fields, sort_keys=True),
                    json.dumps(unit.symbols, sort_keys=True),
                    json.dumps(graph_words),
                    sum(counts.values()),
                )
                if previous:
                    file_id = previous[2]
                    self.conn.execute(
                        "UPDATE files SET size=?, mtime_ns=?, kind=?, fields=?, symbols=?, words=?, "
                        "length=? WHERE id=?",
                        row + (file_id,),
                    )
                    self.conn.execute("DELETE FROM words WHERE file = ?", (file_id,))
                else:
                    file_id = self.conn.execute(
                        "INSERT INTO files (path, size, mtime_ns, kind, fields, symbols, words, "
                        "length) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (rel,) + row,
                    ).lastrowid
                self.conn.executemany(
                    "INSERT OR IGNORE INTO words (word, file, count) VALUES (?, ?, ?)",
                    [(word, file_id, count) for word, count in counts.items()],
                )
                seen.add(rel)
                parsed += 1
            gone = [(entry[2],) for path, entry in stored.items() if path not in seen]
            if gone:
                self.conn.executemany("DELETE FROM words WHERE file = ?", gone)
                self.conn.executemany("DELETE FROM files WHERE id = ?", gone)
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
            # A range over the primary key uses the index, where LIKE would
            # read every row.
            rows = self.conn.execute(
                "SELECT DISTINCT f.path FROM words w JOIN files f ON f.id = w.file "
                "WHERE w.word >= ? AND w.word < ?",
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

    def lengths(self) -> dict[str, int]:
        """Each file's length in words, for the files that have any."""
        return dict(self.conn.execute("SELECT path, length FROM files WHERE length > 0"))

    def postings(self, words) -> list[tuple[str, int]]:
        """Each file that uses any of these words, and how often: the forms add up."""
        words = list(dict.fromkeys(words))
        if not words:
            return []
        marks = ", ".join("?" for _ in words)
        return self.conn.execute(
            "SELECT f.path, SUM(w.count) FROM words w JOIN files f ON f.id = w.file "
            f"WHERE w.word IN ({marks}) GROUP BY w.file",
            words,
        ).fetchall()

    def paths_with_word(self, word: str) -> set[str]:
        """Files that use this exact word."""
        rows = self.conn.execute(
            "SELECT f.path FROM words w JOIN files f ON f.id = w.file WHERE w.word = ?",
            (word,),
        )
        return {row[0] for row in rows}

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


def ensure(root: Path, adapter, force: bool = False, build: bool = False) -> tuple[list[Unit], "Index | None", dict]:
    """The units a command should use, plus the live index when it applies.

    The index is used when it exists, and created only when `build` asks for
    it. Otherwise, and for an adapter that owns `units()`, the caller gets the
    adapter's units and no index.
    """
    if not build and not enabled(root):
        return adapter.units(root), None, {}
    state = Index(root)
    stats = state.ensure(adapter, force=force)
    if "skipped" in stats:
        state.close()
        return adapter.units(root), None, stats
    return state.units(), state, stats
