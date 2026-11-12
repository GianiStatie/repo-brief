"""Named things an adapter extracts, kept in a local cache and searched by word.

A fact is a thing the repository has rather than a file: a feature flag, a
setting, a route. The adapter knows how to read them (`Adapter.facts`); this
module builds the set, caches it, and answers the two questions a reader asks:
which facts match these words, and where is this one declared and used.

The cache is `.repo-brief/facts.json`, git-ignored and rebuilt when a file it was
built from, the adapter's own code, or this module's format changes. It is never
committed and deleting it costs one rebuild.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
import unicodedata
from pathlib import Path

from .contract import Adapter
from .detect import STATE_DIR

FILE = "facts.json"
# Bumped when the shape of the cached set changes.
VERSION = "1"
STOP_WORDS = {"the", "and", "for", "with", "what", "which", "does", "from", "pentru", "din", "sau"}
MAX_TEXT = 200


def path_for(root: Path) -> Path:
    return Path(root) / STATE_DIR / FILE


def enabled(root: Path) -> bool:
    """Whether the cache exists; a tool call or `facts` creates it."""
    return path_for(root).is_file()


def available(adapter) -> bool:
    """Whether this adapter extracts any facts at all."""
    return type(adapter).facts is not Adapter.facts


def _adapter_hash(adapter) -> str:
    """A hash of the adapter's own code, so editing it rebuilds the cache.

    The hook methods' files come from their code objects, which works for the
    module `load_adapter` builds without registering it in `sys.modules` (the
    old lookup returned an empty hash there, so an adapter edit never
    invalidated the cache).
    """
    digest = hashlib.sha256()
    paths = []
    for method in (type(adapter).facts, type(adapter).fact_notes, type(adapter).fact_inputs):
        try:
            paths.append(inspect.getsourcefile(method) or "")
        except (TypeError, OSError):
            continue
    for path in dict.fromkeys(path for path in paths if path):
        try:
            digest.update(Path(path).read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:12]


def inputs(root: Path, adapter, detected=None) -> list:
    """The files the cache is keyed on: the adapter's choice, else every detected file."""
    chosen = [str(path) for path in adapter.fact_inputs(root) or []]
    if chosen:
        return chosen
    detected = detected if detected is not None else adapter.detected(root)
    return [path.as_posix() for path in detected.files]


def fingerprint(root: Path, paths) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        try:
            stat = (Path(root) / path).stat()
        except OSError:
            continue
        digest.update(("%s\0%s\0%s\n" % (path, stat.st_size, stat.st_mtime_ns)).encode(errors="replace"))
    return digest.hexdigest()[:16]


def _counts(facts) -> dict:
    found = {}
    for fact in facts:
        kind = str(fact.get("kind") or "")
        found[kind] = found.get(kind, 0) + 1
    return found


def build(root: Path, adapter) -> dict:
    started = time.perf_counter()
    detected = adapter.detected(root)
    facts = [fact for fact in adapter.facts(root) or [] if isinstance(fact, dict) and fact.get("id")]
    files = inputs(root, adapter, detected)
    return {
        "version": VERSION,
        "adapter": _adapter_hash(adapter),
        "fingerprint": fingerprint(root, files),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "build_seconds": round(time.perf_counter() - started, 2),
        "counts": _counts(facts),
        "notes": adapter.fact_notes(root) or {},
        "facts": facts,
    }


def _read(target: Path):
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save(root: Path, data: dict) -> None:
    from .cache import ignore_local_files

    target = path_for(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    ignore_local_files(target.parent)
    partial = target.with_suffix(".partial")
    partial.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    partial.replace(target)


def cached(root: Path):
    """The cache as it is on disk, without rebuilding; None when absent or old.

    `context` reads this: it must stay cheap and must not create or refresh the
    fact cache just to print a pointer.
    """
    if not enabled(root):
        return None
    data = _read(path_for(root))
    if data is None or data.get("version") != VERSION:
        return None
    return data


def load(root: Path, adapter, rebuild: bool = False) -> tuple:
    """The fact set and where it came from: `cache` or `build`."""
    root = Path(root)
    target = path_for(root)
    if not rebuild and target.is_file():
        stored = _read(target)
        if (
            stored is not None
            and stored.get("version") == VERSION
            and stored.get("adapter") == _adapter_hash(adapter)
            and stored.get("fingerprint") == fingerprint(root, inputs(root, adapter))
        ):
            return stored, "cache"
    data = build(root, adapter)
    _save(root, data)
    return data, "build"


# -- search ---------------------------------------------------------------

def _words(text: str) -> list:
    """Lowercase words, with CamelCase and snake_case split into their parts."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", text.lower())


def _prefix_match(word: str, words) -> bool:
    return len(word) >= 4 and any(
        other.startswith(word) or (len(other) >= 4 and word.startswith(other)) for other in words
    )


def _score(query, fact: dict) -> int:
    """3 per word in the name, summary, tickets or tags; 2 for a prefix; 1 in a user."""
    primary = set(_words(" ".join(
        [str(fact.get("name") or ""), str(fact.get("summary") or "")]
        + [str(item) for item in fact.get("tickets") or []]
        + [str(item) for item in fact.get("tags") or []]
    )))
    places = list(fact.get("used_at") or []) + list(fact.get("mentioned_at") or [])
    secondary = set(_words(" ".join(
        "%s %s" % (place.get("where") or "", Path(str(place.get("path") or "")).stem)
        for place in places
        if isinstance(place, dict)
    )))
    score = 0
    for word in query:
        if word in primary:
            score += 3
        elif _prefix_match(word, primary):
            score += 2
        elif word in secondary or _prefix_match(word, secondary):
            score += 1
    return score


def search(facts, query: str, kind: str = "", limit: int = 15) -> tuple:
    """The best matches and how many matched in all; an empty query lists by id."""
    found = [fact for fact in facts if not kind or fact.get("kind") == kind]
    words = [word for word in _words(query) if len(word) > 1 and word not in STOP_WORDS]
    if not words:
        return sorted(found, key=lambda fact: str(fact.get("id")))[:limit], len(found)
    scored = [(_score(words, fact), fact) for fact in found]
    matched = sorted(
        ((score, fact) for score, fact in scored if score),
        key=lambda pair: (-pair[0], str(pair[1].get("id"))),
    )
    return [fact for _score_value, fact in matched[:limit]], len(matched)


def find(facts, fact_id: str):
    wanted = str(fact_id or "").strip()
    return next((fact for fact in facts if fact.get("id") == wanted), None)


def hint(root: Path, adapter, query: str, tool: str = "repo_facts", limit: int = 3):
    """A pointer from a `context` query to the facts, or None.

    Reads the cache only, so `context` stays cheap and never creates or
    refreshes facts just to print a pointer. It fires on a matching fact, or
    when the query names a kind ("flags"), which is what makes a generic
    "where are the feature flags checked?" query point at the extractor.
    Without a cache there is no hint; `repo-brief facts` builds one.
    """
    if not available(adapter):
        return None
    data = cached(root)
    if data is None:
        return None
    facts = data.get("facts") or []
    if not facts:
        return None
    words = {word for word in _words(query) if word not in STOP_WORDS}
    if not words:
        return None
    counts = data.get("counts") or {}
    kind = next(
        (name for name in sorted(counts) if name in words or name + "s" in words), ""
    )
    found, total = search(facts, query, limit=limit)
    if not found and not kind:
        return None
    if not kind and len(counts) == 1:
        kind = next(iter(counts))
    return {
        "total": len(facts),
        "matches": [fact.get("id") for fact in found],
        "match_total": total,
        "kind": kind,
        "tool": tool,
    }


# -- rendering ------------------------------------------------------------

def _place(place: dict) -> str:
    where = place.get("where") or Path(str(place.get("path") or "")).name
    how = place.get("how") or ""
    text = str(place.get("text") or "")
    line = "%s:%s" % (place.get("path") or "?", place.get("line") or "?")
    detail = " — %s" % how if how else ""
    return "%s  %s%s%s" % (line, where, detail, "  `%s`" % text if text else "")


def render_search(
    data: dict,
    query: str = "",
    kind: str = "",
    limit: int = 15,
    loaded: str = "",
    fact_tool: str = "repo_fact",
) -> str:
    """The list, then the way to open one, then the caveats.

    `fact_tool` is how this caller opens a single fact: `repo_fact`, the
    agent's `repo_brief_fact`, or `repo-brief facts --id`.
    """
    facts = data.get("facts") or []
    if not facts:
        return (
            "No facts: this repository's adapter has no `facts()` hook. Add one to "
            "`tools/brief_adapter.py` (or `.repo-brief/adapter.py` in a local setup) "
            "(SPEC.md, \"Facts\")."
        )
    found, total = search(facts, query, kind, limit)
    counts = ", ".join("%d %s" % (count, name) for name, count in sorted((data.get("counts") or {}).items()))
    lines = [
        "%d fact(s)%s, built %s (%s)."
        % (len(facts), " (%s)" % counts if counts else "", data.get("built_at") or "?", loaded or "build")
    ]
    if query or kind:
        lines += ["", "%d match(es):" % total]
    for fact in found:
        uses = len(fact.get("used_at") or []) + len(fact.get("mentioned_at") or [])
        lines.append(
            "- %s — %s (%d place(s))"
            % (fact.get("id"), fact.get("summary") or fact.get("name"), uses)
        )
    if total > len(found):
        lines.append("... %d more" % (total - len(found)))
    if found:
        lines += ["", "One fact in full: `%s <id>`." % fact_tool]
    # Notes are caveats about a kind, not part of the ranking; a query should
    # show its matches first, especially when a note carries locations.
    for note_kind, notes in sorted((data.get("notes") or {}).items()):
        if kind and note_kind != kind:
            continue
        lines += ["", "%s:" % note_kind] + ["- %s" % note for note in notes]
    return "\n".join(lines)


def render_fact(fact: dict) -> str:
    lines = ["%s — %s" % (fact.get("id"), fact.get("summary") or fact.get("name"))]
    if fact.get("tickets"):
        lines.append("Tickets: %s" % ", ".join(fact["tickets"]))
    if fact.get("tags"):
        lines.append("Tags: %s" % ", ".join(fact["tags"]))
    declared = fact.get("declared_at") or {}
    if declared:
        lines += ["", "Declared:", "- %s" % _place(declared)]
    for label, key in (("Used", "used_at"), ("Named as data", "mentioned_at")):
        places = fact.get(key) or []
        if not places:
            continue
        lines += ["", "%s (%d):" % (label, len(places))]
        lines += ["- %s" % _place(place) for place in places[:20]]
        if len(places) > 20:
            lines.append("... %d more" % (len(places) - 20))
    return "\n".join(lines)
