"""Matching, ranking and context assembly.

Adapters declare structure. This file decides what matters. Everything an
adapter hands over is scored here, so a ranking fix made once reaches every
repository that uses the kernel.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .contract import Adapter, Item, Source, Unit
from .detect import is_archived

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "how",
    "does", "do", "is", "are", "was", "were", "what", "where", "why", "when",
    "which", "who", "this", "that", "it", "its", "from", "into", "by", "as",
    "at", "be", "can", "should", "would", "could", "we", "i", "you", "they",
    "our", "your", "their", "not", "no", "if", "then", "than", "about", "work",
    "works", "working", "code", "file", "files", "thing", "things",
}

SHORT_WORDS = {"hp", "ui", "gd", "ai", "qr", "db", "os", "io"}

# Field name -> weight when a query token appears in it. Adapters earn these by
# naming their fields the same way; anything unrecognised scores 1.
FIELD_WEIGHTS = {
    "class_name": 8,
    "title": 8,
    "name": 6,
    "description": 5,
    "headings": 3,
    "summary": 3,
    "tags": 2,
    "extends": 1,
    "signals": 1,
    "scripts": 1,
    "nodes": 1,
}

SOURCE_KIND_BOOST = {"docs": 1, "rules": 1, "tool": 1, "build": 3}

# `StandServiceImpl` is three words, the same as `stand_service_impl`.
# Splitting CamelCase lets a query word find a class by the file it lives in.
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def name_words(name: str) -> set[str]:
    """Lowercase words of a path or name: `StoneFree.gd` -> stonefree, stone, free, gd."""
    words = set()
    for part in re.split(r"[^A-Za-z0-9]+", name):
        if part:
            words.add(part.lower())
            words.update(piece.lower() for piece in CAMEL_RE.split(part) if piece)
    return words


def tokenize(query: str) -> list[str]:
    words = re.split(r"[^a-z0-9]+", query.lower())
    tokens = []
    for word in words:
        if not word or word in STOPWORDS:
            continue
        if len(word) < 3 and word not in SHORT_WORDS:
            continue
        tokens.append(word)
        if word.endswith("s") and len(word) > 4:
            tokens.append(word[:-1])
    seen = set()
    return [t for t in tokens if not (t in seen or seen.add(t))]


def phrases(query: str) -> list[str]:
    """Literal phrases worth a bonus: underscored identifiers and the exact query."""
    found = re.findall(r"[a-z0-9]+(?:_[a-z0-9]+)+", query.lower())
    normalized = re.sub(r"\s+", " ", query.lower()).strip()
    if " " in normalized and normalized not in found:
        found.append(normalized)
    return found


def _field_text(value) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value or "")


def score_unit(unit: Unit, tokens: Iterable[str]) -> int:
    tokens = list(tokens)
    stem = Path(unit.path).stem.lower()
    # Whole words only. Substring matching on paths makes "reset" find
    # "export_presets.cfg", which is how a config file wins a query about cooldowns.
    stem_words = name_words(Path(unit.path).stem)
    path_words = name_words(unit.path)
    # Only weighted fields score; a Java file's import list is not worth lowering.
    fields = {key: _field_text(unit.fields[key]).lower() for key in FIELD_WEIGHTS if key in unit.fields}
    symbol_names = " ".join(str(s.get("name", "")).lower() for s in unit.symbols)

    score = 0
    for token in tokens:
        if token in stem_words:
            score += 5
        elif stem.startswith(token) or stem.endswith(token):
            # EmeraldBarrier, StandUser: the class name is a compound of the query.
            score += 5
        if token in path_words:
            score += 2
        for key, weight in FIELD_WEIGHTS.items():
            if key in fields and token in fields[key]:
                score += weight
        if token in symbol_names:
            score += 3

    if score > 0 and unit.kind in ("source", "note"):
        score += 1
    return score


def score_item(item: Item, tokens: Iterable[str], phrases_: Iterable[str] = ()) -> int:
    tokens = list(tokens)
    phrases_ = list(phrases_)
    title = item.text.lower()
    body = item.body.lower()

    title_hits = {token for token in tokens if token in title}
    body_hits = {token for token in tokens if token in body}
    matched = title_hits | body_hits
    phrase_hits = sum(1 for phrase in phrases_ if phrase in title or phrase in body)

    if not matched and not phrase_hits:
        return 0
    # A three-word query should not pull in every record that shares one generic word.
    if len(tokens) >= 2 and len(matched) < 2 and not phrase_hits:
        return 0

    # The title is the claim; the body is context. A long document that repeats
    # a word must not outrank a short one that states it.
    score = 8 * len(title_hits)
    score += 2 * len(body_hits - title_hits)
    score += min(phrase_hits * 12, 24)

    words = max(len(item.body.split()), 1)
    occurrences = sum(body.count(token) for token in matched)
    score += min(occurrences * 1000 // words, 6)

    if item.kind == "decision":
        score += 2
    return score


def score_source(source: Source, tokens: Iterable[str], phrases_: Iterable[str] = ()) -> int:
    tokens = list(tokens)
    lowered = source.text.lower()
    path_lower = source.path.lower()
    matched = set()
    score = 0

    for token in tokens:
        hits = lowered.count(token)
        if hits:
            matched.add(token)
            score += 2 + min(hits, 3)
        if token in path_lower:
            matched.add(token)
            score += 4
    if not matched:
        return 0

    score += SOURCE_KIND_BOOST.get(source.kind, 0)
    score += min(sum(1 for phrase in phrases_ if phrase in lowered) * 12, 24)

    for line in lowered.splitlines():
        if line.startswith("#") and any(token in line for token in tokens):
            score += 3
            break
    head = "\n".join(lowered.splitlines()[:8])
    score += sum(2 for token in tokens if token in head)
    return score


def best_line(text: str, tokens: Iterable[str], skip_comments: bool = False) -> tuple[int, str]:
    """The line that best answers the query.

    When `skip_comments` is set, a real code line wins over a doc comment, but
    comments are not discarded: if nothing else matches, a `##` line is often
    the only place a symbol is explained.
    """
    tokens = list(tokens)

    def scan(allow_comments: bool) -> tuple[int, str]:
        best_score, best_no, best_text = -1, 1, ""
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            if not allow_comments and stripped.startswith(("#", "//", "/*", "*")):
                continue
            lowered = stripped.lower()
            hits = sum(1 for token in tokens if token in lowered)
            if not hits:
                continue
            score = hits * 4 + sum(min(lowered.count(token), 2) for token in tokens)
            if score > best_score:
                best_score, best_no, best_text = score, number, stripped
        if best_score < 0:
            return 0, ""
        return best_no, best_text

    number, snippet = scan(allow_comments=False)
    if snippet:
        return number, snippet[:180]
    if skip_comments:
        number, snippet = scan(allow_comments=True)
        if snippet:
            return number, snippet[:180]
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return 1, first[:180]


def matching_symbols(unit: Unit, tokens: Iterable[str], limit: int = 3) -> list[dict]:
    tokens = list(tokens)
    found = []
    for symbol in unit.symbols:
        name = str(symbol.get("name", ""))
        if any(token in name.lower() for token in tokens):
            found.append(symbol)
            if len(found) >= limit:
                break
    return found


def tests_for(units: list[Unit]) -> dict[str, list[str]]:
    """Map each test unit to the source units it names.

    Generic and deliberately conservative: a test covers a source file when it
    mentions that file's class name or stem. A class named by most of the test
    suite is a shared base, not a dependency -- counting it would make every
    test look like it covers every core file.
    """
    sources = [unit for unit in units if unit.kind == "source"]
    tests = [unit for unit in units if unit.kind == "test"]

    raw: dict[str, list[str]] = {}
    for test in tests:
        words = set(re.findall(r"[A-Za-z_]\w*", test.text))
        # A Godot test usually preloads the script instead of naming its class,
        # so a resource reference counts too: `res://src/x.gd` for `godot/src/x.gd`.
        resources = re.findall(r"res://([^\s\"']+)", test.text)
        covered = [
            source.path
            for source in sources
            if (source.fields.get("class_name") or Path(source.path).stem) in words
            or source.path in test.text
            or any(source.path.endswith(ref) for ref in resources)
        ]
        if covered:
            raw[test.path] = covered

    fanout: dict[str, int] = {}
    for covered in raw.values():
        for path in covered:
            fanout[path] = fanout.get(path, 0) + 1

    # Anything a quarter of the suite (or five tests) touches is infrastructure.
    limit = max(5, len(tests) // 4)
    mapping = {}
    for test_path, covered in raw.items():
        specific = [path for path in covered if fanout[path] < limit]
        if specific:
            mapping[test_path] = sorted(specific)
    return mapping


def _snippet(text: str, tokens: list[str]) -> tuple[int, str]:
    return best_line(text, tokens)


# A read plan is a starting point, not a mandate. The cap keeps "read this
# section" from quietly meaning "read all 2,700 lines of the decision log".
MAX_SECTION_LINES = 160
READ_PLAN_LIMIT = 5
# Below these sizes a whole file is cheaper than a slice, and a slice chosen
# from one generic token can point at the wrong function entirely.
SMALL_CODE_BYTES = 24 * 1024
SMALL_DOC_BYTES = 12 * 1024
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)


def _text_lines(text: str) -> int:
    return max(len(text.splitlines()), 1)


def _section_bytes(text: str, start: int, end: int) -> int:
    lines = text.splitlines()
    return len("\n".join(lines[start - 1:end]).encode("utf-8"))


def _stem_words(path: str) -> set[str]:
    return name_words(Path(path).stem)


def _stem_related(token: str, words: set[str]) -> bool:
    """Whether a token is just the file's own name, singular or plural."""
    return any(token.startswith(word) or word.startswith(token) for word in words)


def _best_symbol_line(unit: Unit, tokens: Iterable[str], fallback: int) -> int:
    """Prefer the symbol whose scope answers the query over the file header.

    `best_line` is content-based, so a query mentioning `hold` can still land
    on `class_name Rush` first. Scoring each symbol's name plus its doc
    comment and body keeps the plan anchored to the function that does the
    work, not the declaration at the top of the file.
    """
    stem = _stem_words(unit.path)
    tokens = [token for token in tokens if not _stem_related(token, stem)]
    if not tokens:
        return fallback
    symbols = sorted(
        [symbol for symbol in unit.symbols if symbol.get("line")],
        key=lambda symbol: int(symbol["line"]),
    )
    lines = unit.text.splitlines()
    total = _text_lines(unit.text)
    ranked = []
    for index, symbol in enumerate(symbols):
        line = int(symbol["line"])
        if line < 1 or line > total:
            continue
        start = _leading_comment_start(unit.text, line)
        end = int(symbols[index + 1]["line"]) - 1 if index + 1 < len(symbols) else total
        scope = "\n".join(lines[start - 1:end]).lower()
        name = str(symbol.get("name", "")).lower()
        name_hits = sum(1 for token in tokens if token in name)
        body_hits = sum(1 for token in tokens if token in scope)
        if not name_hits and not body_hits:
            continue
        kind = 1 if symbol.get("kind") in ("function", "const", "signal") else 0
        ranked.append(((name_hits * 2 + body_hits, kind), line))
    if not ranked:
        return fallback
    return max(ranked)[1] or fallback


def _headings(text: str) -> list[dict]:
    """Markdown headings with line numbers, for sources that have no parser."""
    return [
        {"kind": "heading", "name": match.group(1).strip(),
         "line": text.count("\n", 0, match.start()) + 1}
        for match in HEADING_RE.finditer(text)
    ]


def _leading_comment_start(text: str, start: int) -> int:
    """Include the doc comment directly above a symbol in its slice."""
    lines = text.splitlines()
    index = start - 2
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped.startswith(("#", "//", "/*", "*")):
            break
        start = index + 1
        index -= 1
    return start


def _section(text: str, symbols: list[dict], path: str, best: int,
             max_lines: int = MAX_SECTION_LINES) -> tuple[int, int, str]:
    """The smallest useful window around the best line.

    Markdown gets a heading section; code gets the enclosing symbol. Anything
    without symbols gets a window. The range always contains `best` and never
    exceeds `max_lines`, so the plan can ask for a slice without guessing how
    long the file is.
    """
    total = _text_lines(text)
    if not text:
        return 1, 1, ""
    markdown = path.endswith(".md")
    kinds = ("heading",) if markdown else ("function", "class", "const", "signal")
    ranked = [
        symbol for symbol in symbols
        if symbol.get("line") and symbol.get("kind") in kinds
    ]
    if not ranked:
        ranked = [symbol for symbol in symbols if symbol.get("line")]
    ranked.sort(key=lambda symbol: int(symbol["line"]))

    best = max(1, min(best or 1, total))
    if not ranked:
        start = max(1, best - max_lines // 3)
        return start, min(total, start + max_lines - 1), ""

    # A doc comment is often the best match and the next symbol is the code it
    # describes. Anchor to that symbol, then pull its comment block in.
    if not markdown:
        stripped = text.splitlines()[best - 1].strip()
        if stripped.startswith(("#", "//", "/*", "*")):
            upcoming = [s for s in ranked if int(s["line"]) >= best]
            if upcoming:
                best = int(upcoming[0]["line"])

    before = [symbol for symbol in ranked if int(symbol["line"]) <= best]
    start = int(before[-1]["line"]) if before else 1
    after = [symbol for symbol in ranked if int(symbol["line"]) > best]
    end = int(after[0]["line"]) - 1 if after else total

    if best > end:
        start = max(1, best - max_lines + 1)
        end = best
    if best < start:
        start = best
        end = min(total, start + max_lines - 1)
    if not markdown and start > 1:
        start = _leading_comment_start(text, start)
    if end - start + 1 > max_lines:
        end = start + max_lines - 1

    label = str(before[-1].get("name", "")) if before else ""
    return start, end, label


def _strong_symbol(unit: Unit, tokens: Iterable[str]) -> bool:
    """Whether a symbol name, not the file stem, names the query.

    `settings.gd` contains "settings" everywhere; that is the file's identity,
    not evidence that `_apply_settings` is the answer. Ignore stem words when
    deciding whether a narrow slice is safe.
    """
    stem_words = _stem_words(unit.path)
    meaningful = [
        token for token in tokens if not _stem_related(token, stem_words)
    ]
    if not meaningful:
        return False
    return any(
        symbol.get("kind") in ("function", "const", "signal")
        and any(token in str(symbol.get("name", "")).lower() for token in meaningful)
        for symbol in unit.symbols
    )


def _read_span(text: str, symbols: list[dict], path: str, best: int,
               strong: bool) -> tuple[int, int, str]:
    """Whole file when it is small, a section when it is not."""
    total = _text_lines(text)
    size = len(text.encode("utf-8"))
    if path.endswith(".md"):
        if size <= SMALL_DOC_BYTES:
            return 1, total, ""
    elif size <= SMALL_CODE_BYTES and not strong:
        return 1, total, ""
    return _section(text, symbols, path, best)


def _read_plan(units: list[dict], sources: list[dict]) -> list[dict]:
    """Deduplicate the ranked reads into one ordered plan."""
    plan: list[dict] = []
    seen: set[str] = set()
    for item in list(sources[:2]) + units:
        path = item["path"]
        if path in seen:
            continue
        seen.add(path)
        plan.append(
            {
                "path": path,
                "start": item.get("start", item.get("line", 1)),
                "end": item.get("end", item.get("lines", 1)),
                "line": item.get("line", item.get("start", 1)),
                "bytes": item.get("bytes", 0),
                "label": item.get("label", ""),
                "kind": item.get("kind", "source"),
                "archived": bool(item.get("archived")),
            }
        )
        if len(plan) >= READ_PLAN_LIMIT:
            break
    return plan


def build_context(
    root: Path,
    adapter: Adapter,
    query: str,
    limit: int = 6,
    max_source_bytes: int = 512 * 1024,
    archived: bool = False,
    units: list[Unit] | None = None,
    usage_index=None,
) -> dict:
    tokens = tokenize(query)
    if not tokens:
        tokens = [query.strip().lower()]
    phrases_ = phrases(query)

    if units is None:
        units = adapter.units(root)
    archived_dirs = adapter.archived(root)
    if not archived:
        # Archive is not an answer unless the question is about the past.
        units = [unit for unit in units if unit.kind != "archive"]

    scored_units = []
    for unit in units:
        score = score_unit(unit, tokens)
        if score > 0:
            scored_units.append((score, unit))
    scored_units.sort(key=lambda pair: (-pair[0], pair[1].path))
    top_units = scored_units[:limit]
    top_paths = {unit.path for _, unit in top_units}

    scored_decisions = []
    for item in adapter.decisions(root):
        score = score_item(item, tokens, phrases_)
        if score:
            item.score = score
            scored_decisions.append((score, item))
    scored_decisions.sort(key=lambda pair: (-pair[0], pair[1].line))
    top_decisions = scored_decisions[:3]

    scored_open = []
    for item in adapter.open_work(root):
        score = score_item(item, tokens, phrases_)
        if score:
            scored_open.append((score, item))
    scored_open.sort(key=lambda pair: (-pair[0], pair[1].line))
    top_open = scored_open[:3]

    scored_sources = []
    for source in adapter.sources(root):
        if not archived and is_archived(source.path, archived_dirs):
            continue
        if len(source.text.encode("utf-8", "ignore")) > max_source_bytes:
            continue
        score = score_source(source, tokens, phrases_)
        if not score:
            continue
        line, snippet = best_line(source.text, tokens)
        headings = _headings(source.text) if source.path.endswith(".md") else []
        start, end, label = _read_span(source.text, headings, source.path, line, False)
        scored_sources.append(
            {
                "path": source.path,
                "kind": source.kind,
                "archived": is_archived(source.path, archived_dirs),
                "line": line,
                "start": start,
                "end": end,
                "bytes": _section_bytes(source.text, start, end),
                "label": label,
                "snippet": snippet,
                "score": score,
            }
        )
    scored_sources.sort(key=lambda item: (-item["score"], item["path"]))
    top_sources = scored_sources[: max(limit, 5)]

    usage = []
    usage_roots = tuple(adapter.usage_roots(root))
    candidates = None
    if usage_index is not None:
        candidates = set(usage_index.candidate_paths(tokens, usage_roots))
    for unit in sorted(units, key=lambda u: u.path):
        # Usage means "where is this used in code", not "which prose mentions it".
        if unit.kind not in ("source", "test", "file"):
            continue
        if usage_roots and not unit.path.startswith(usage_roots):
            continue
        if candidates is not None:
            if unit.path not in candidates:
                continue
            text = usage_index.text(unit.path)
        else:
            text = unit.text
        if not text:
            continue
        line, snippet = best_line(text, tokens, skip_comments=(unit.kind != "note"))
        lowered = snippet.lower()
        hits = [token for token in tokens if token in lowered]
        if not hits:
            continue
        score = len(hits) * 6 + sum(min(lowered.count(token), 2) for token in hits)
        score += sum(4 for token in tokens if token in unit.path.lower())
        if "=" in snippet or "(" in snippet:
            score += 3
        usage.append({"path": unit.path, "line": line, "snippet": snippet, "score": score})
    usage.sort(key=lambda item: (-item["score"], item["path"]))

    coverage = tests_for(units)
    test_scores: dict[str, int] = {}
    # The index supplies units without their text. Tests are the one kind whose
    # text the coverage map needs, and there are few of them next to the code.
    if usage_index is not None:
        for unit in units:
            if unit.kind == "test" and not unit.text:
                unit.text = usage_index.text(unit.path)
    # Tests are ranked against the files actually shown. Grading a wider pool
    # drags in the whole suite through shared UI and base classes, and a test
    # for a file the brief did not point at is not a useful next read.
    for rank, (_, unit) in enumerate(top_units):
        weight = max(len(top_units) - rank, 1)
        for test_path, covers in coverage.items():
            if unit.path in covers:
                test_scores[test_path] = test_scores.get(test_path, 0) + weight
    for score, unit in top_units:
        if unit.kind == "test":
            test_scores[unit.path] = test_scores.get(unit.path, 0) + score
    tests = sorted(test_scores, key=lambda path: (-test_scores[path], path))

    resources = sorted(
        {
            unit.path
            for unit in units
            if unit.kind in ("scene", "resource")
            for script in unit.fields.get("scripts", [])
            if script in top_paths
        }
    )

    unit_payloads = []
    for score, unit in top_units:
        if usage_index is not None and not unit.text:
            unit.text = usage_index.text(unit.path)
        # The plan is a locator, not a proof: a doc comment is a valid anchor
        # even when the code line itself does not repeat the query word.
        best, _ = best_line(unit.text, tokens)
        strong = False
        if not unit.path.endswith(".md"):
            best = _best_symbol_line(unit, tokens, best)
            strong = _strong_symbol(unit, tokens)
        start, end, label = _read_span(unit.text, unit.symbols, unit.path, best, strong)
        unit_payloads.append(
            {
                "path": unit.path,
                "kind": unit.kind,
                "class_name": unit.fields.get("class_name", ""),
                "extends": unit.fields.get("extends", ""),
                "score": score,
                "line": best,
                "start": start,
                "end": end,
                "bytes": _section_bytes(unit.text, start, end),
                "label": label,
                "symbols": matching_symbols(unit, tokens),
                "archived": unit.kind == "archive",
                "text": unit.text,
            }
        )
    read_plan = _read_plan(unit_payloads, top_sources)
    budget = {
        "files": len(read_plan),
        "bytes": sum(item["bytes"] for item in read_plan),
    }

    return {
        "query": query,
        "root": str(root),
        "tokens": tokens,
        "phrases": phrases_,
        "archived": archived,
        "units": unit_payloads,
        "read_plan": read_plan,
        "budget": budget,
        "decisions": [
            {
                "path": item.path,
                "line": item.line,
                "text": item.text,
                "label": item.label,
                "date": item.date,
                "group": item.group,
                "score": score,
            }
            for score, item in top_decisions
        ],
        "open_work": [
            {
                "path": item.path,
                "line": item.line,
                "text": item.text,
                "group": item.group,
                "score": score,
            }
            for score, item in top_open
        ],
        "sources": top_sources,
        "usage": usage[:3],
        "tests": tests[:limit],
        "resources": resources[:limit],
        "top_paths": sorted(top_paths),
    }
