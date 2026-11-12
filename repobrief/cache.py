"""Parsed files, remembered between runs.

Reading thousands of files on every `context` call is most of what a large repo
costs. A reader's output is a function of one file's text, so each entry is
keyed by a hash of that text, and the whole cache by a hash of the parsing code
and the Python that ran it: a changed file or a changed parser never matches an
old entry.

What a file says about other files -- which import or link resolves -- is not
cached. `parsers.resolve` recomputes it on every pass, so adding a module can
never leave a stale edge behind.

The cache lives in `.repo-brief/` beside the committed map, but it is local:
rebuilt on demand, never committed. `ignore_local_files` keeps it out of git.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from . import parsers
from .contract import Unit
from .detect import STATE_DIR

CACHE_FILE = "parse-cache.json"
# Everything repo-brief writes into the state directory that git must never
# see. The `.gitignore` names itself, so it needs no commit either.
# `index.sqlite*` covers the optional index and SQLite's journal files beside it;
# `facts.json` is the adapter's fact cache.
LOCAL_FILES = (".gitignore", "*.tmp", "cache.json", "local.json", CACHE_FILE, "index.sqlite*", "facts.json")
IGNORE_HEADER = "# Written by repo-brief: local caches, rebuilt on demand, never committed."


def parser_version() -> str:
    """Changes whenever the parsing code, or the Python that runs it, does."""
    try:
        source = Path(parsers.__file__).read_bytes()
    except OSError:
        return ""
    stamp = source + ("python %d.%d" % sys.version_info[:2]).encode()
    return hashlib.blake2b(stamp, digest_size=16).hexdigest()


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()


def ignore_local_files(state: Path) -> None:
    """Make `.repo-brief/.gitignore` name every local file. Never removes a line."""
    path = state / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [name for name in LOCAL_FILES if name not in present]
    if not missing:
        return
    if existing and not existing.endswith("\n"):
        existing += "\n"
    header = [] if existing else [IGNORE_HEADER]
    path.write_text(existing + "\n".join(header + missing) + "\n", encoding="utf-8")


class ParseCache:
    """One pass of reads. Hits come from the last pass; misses are parsed and kept."""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / STATE_DIR / CACHE_FILE
        self.version = parser_version()
        self.previous = self._load()
        self.current: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0

    def _load(self) -> dict:
        if not self.version:
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("parser") != self.version:
            return {}
        files = data.get("files")
        return files if isinstance(files, dict) else {}

    def read(self, reader, path: Path, rel: str) -> Unit:
        """`reader(root, path, text)`, or what it returned for this exact text last time."""
        text = parsers.read_text(path)
        digest = _digest(text)
        entry = self.previous.get(rel)
        if entry is not None and entry.get("digest") == digest:
            self.hits += 1
            unit = Unit(path=rel, kind=entry["kind"], fields=entry["fields"], symbols=entry["symbols"], text=text)
        else:
            self.misses += 1
            unit = reader(self.root, path, text)
            entry = {"digest": digest, "kind": unit.kind, "fields": unit.fields, "symbols": unit.symbols}
        self.current[rel] = entry
        return unit

    def save(self) -> None:
        """Keep the entries this pass used, if any changed.

        A tree that cannot be written to still gets an answer; it just parses
        again next time.
        """
        if not self.version or (not self.misses and self.current.keys() == self.previous.keys()):
            return
        state = self.path.parent
        temporary = state / ("%s.%d.tmp" % (CACHE_FILE, os.getpid()))
        try:
            state.mkdir(parents=True, exist_ok=True)
            ignore_local_files(state)
            temporary.write_text(
                json.dumps({"parser": self.version, "files": self.current}, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
