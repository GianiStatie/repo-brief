"""Find material that has outlived its usefulness, and move it to the archive.

Review is advice; apply is explicit. Nothing here runs during `index` beyond a
count, and nothing moves without a list of paths.

The rules look for orphans, not irrelevance. An unlinked doc can still be the
only record of why something is the way it is, which is why `--apply` takes
paths and there is no `--all`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import graph
from .contract import Adapter, Edge, Unit
from .detect import DEFAULT_ARCHIVE, WRITING_DOC, is_archived

WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(\|[^\[\]]*)?\]\]")
MD_MENTION_RE = re.compile(r"[\w][\w./-]*\.md\b")
ROOT_SCRATCH = ("PLAN.md", "TODO.md", "NOTES.md", "SCRATCH.md")

ARCHIVE_README = """# Archive

Completed and superseded material, kept so old links keep resolving.

Nothing here is current guidance: `repo-brief context` ignores this directory
unless it is asked for with `--archived`. Move things here; do not delete them.
"""


@dataclass
class Candidate:
    path: str
    reason: str
    detail: str = ""


def candidates(root: Path, adapter: Adapter, units: list[Unit], edges: list[Edge], canonical=()) -> list[Candidate]:
    archived_dirs = adapter.archived(root)
    canonical = set(canonical or ())

    inbound_live: dict[str, int] = {}
    inbound_archived: dict[str, int] = {}
    for edge in edges:
        if edge.kind != "doc":
            continue
        bucket = inbound_archived if is_archived(edge.source, archived_dirs) else inbound_live
        bucket[edge.target] = bucket.get(edge.target, 0) + 1

    by_name: dict[str, list] = {}
    for unit in units:
        by_name.setdefault(Path(unit.path).name, []).append(unit.path)

    mentioned: set[str] = set()
    for unit in units:
        if unit.kind == "archive" or not unit.text:
            continue
        for mention in set(MD_MENTION_RE.findall(unit.text)):
            matches = by_name.get(Path(mention).name) or []
            if len(matches) == 1 and matches[0] != unit.path:
                mentioned.add(matches[0])

    live = [
        unit
        for unit in units
        if unit.kind != "archive" and unit.path.startswith("docs/") and unit.path.endswith(".md")
    ]

    by_title: dict[str, list] = {}
    for unit in live:
        headings = unit.fields.get("headings") or []
        if headings:
            by_title.setdefault(headings[0].strip().casefold(), []).append(unit.path)
    duplicated = {path for paths in by_title.values() if len(paths) > 1 for path in paths}

    found: list[Candidate] = []
    for name in ROOT_SCRATCH:
        if (root / name).is_file():
            found.append(Candidate(name, "root scratch", "belongs in docs/ or the archive"))

    scratch = {candidate.path for candidate in found}
    for unit in live:
        if unit.path in canonical or unit.path in scratch:
            continue
        # Generated conventions, like the skill: never a candidate, whatever the
        # adapter's canonical list says.
        if unit.path == WRITING_DOC:
            continue
        if unit.path in duplicated:
            title = (unit.fields.get("headings") or [""])[0]
            found.append(Candidate(unit.path, "duplicate title", title))
            continue
        if inbound_live.get(unit.path, 0) or unit.path in mentioned:
            continue
        archived_hits = inbound_archived.get(unit.path, 0)
        if archived_hits:
            found.append(Candidate(unit.path, "only archived links", "%d archived" % archived_hits))
        else:
            found.append(Candidate(unit.path, "orphaned", "0 inbound links"))

    for name in ROOT_SCRATCH:
        if (root / name).is_file():
            found.append(Candidate(name, "root scratch", "belongs in docs/ or the archive"))

    seen = set()
    unique = []
    for candidate in sorted(found, key=lambda item: item.path):
        if candidate.path in seen:
            continue
        seen.add(candidate.path)
        unique.append(candidate)
    return unique


def apply(
    root: Path,
    adapter: Adapter,
    paths: list[str],
    dry_run: bool = False,
    emit=print,
) -> int:
    units = adapter.units(root)
    by_path = {unit.path: unit for unit in units}
    canonical = set(adapter.canonical(root))
    archived_dirs = adapter.archived(root)
    archive_dir = archived_dirs[0] if archived_dirs else DEFAULT_ARCHIVE

    moves: dict[str, str] = {}
    destinations: set[str] = set()
    for raw in paths:
        path = raw.strip().removeprefix("./")
        if path in canonical:
            emit("refusing to archive a canonical doc: %s" % path)
            return 1
        if is_archived(path, archived_dirs):
            emit("already archived: %s" % path)
            return 1
        if path not in by_path and not (root / path).is_file():
            emit("no such file: %s" % path)
            return 1
        destination = "%s/%s" % (archive_dir, Path(path).name)
        if destination in destinations:
            emit("two paths would land on %s" % destination)
            return 1
        if (root / destination).exists():
            emit("destination already exists: %s" % destination)
            return 1
        destinations.add(destination)
        moves[path] = destination

    paths_all = set(by_path)
    rewrites: list[tuple[str, str]] = []
    final: dict[str, str] = {}
    for unit in units:
        if not unit.text:
            continue
        updated = _rewrite_links(unit.text, paths_all, moves)
        final[unit.path] = updated
        if updated != unit.text:
            rewrites.append((unit.path, updated))

    if dry_run:
        emit("would move:")
        for source, destination in sorted(moves.items()):
            emit("  %s -> %s" % (source, destination))
        emit("would rewrite %d file(s)" % len(rewrites))
        for path, _ in rewrites:
            emit("  %s" % path)
        return 0

    for unit_path, text in rewrites:
        (root / unit_path).write_text(text, encoding="utf-8")

    state = root / archive_dir
    created = not state.is_dir()
    state.mkdir(parents=True, exist_ok=True)
    if created or not (state / "README.md").is_file():
        (state / "README.md").write_text(ARCHIVE_README, encoding="utf-8")

    for source, destination in sorted(moves.items()):
        (root / source).rename(root / destination)

    for source, destination in sorted(moves.items()):
        emit("moved %s -> %s" % (source, destination))
    emit("rewrote %d file(s) to point at the new path" % len(rewrites))
    if created:
        emit("run `repo-brief index` and `repo-brief profile` (the archive directory is new)")
    else:
        emit("run `repo-brief index` to refresh the map")

    leftovers = [
        (unit_path, source)
        for unit_path, text in final.items()
        for source in moves
        if source != unit_path and source in text
    ]
    for unit_path, source in sorted(set(leftovers)):
        emit("warn: %s still mentions `%s` as plain text" % (unit_path, source))
    return 0


def _rewrite_links(text: str, paths: set[str], moves: dict[str, str]) -> str:
    """Point every wikilink at a moved file to its new home.

    Links are re-resolved rather than string-matched, so only links that really
    referred to the moved file are touched, the label is preserved, and the
    vault-root convention of the original link is kept.
    """

    def replace(match: re.Match) -> str:
        body = match.group(1).strip()
        target, base = graph.resolve_doc_base(body, paths)
        if target not in moves:
            return match.group(0)
        return "[[%s%s]]" % (link_body(moves[target], base), match.group(2) or "")

    return WIKILINK_RE.sub(replace, text)


def link_body(destination: str, base: str = "docs/") -> str:
    """`docs/archive/foo.md` -> `archive/foo`, in the vault's own convention."""
    body = destination[len(base):] if base and destination.startswith(base) else destination
    return body[:-3] if body.endswith(".md") else body
