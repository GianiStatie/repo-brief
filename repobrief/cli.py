"""Command line: index, context, graph, archive, preflight, doctor, scaffold, profile, writing, skill."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

from repobrief import archive as archive_mod
from repobrief import graph as graph_mod
from repobrief import kernel, render
from repobrief.adapters.generic import GenericAdapter
from repobrief.contract import Adapter
from repobrief.detect import CAPTURE_DIR, DEFAULT_ARCHIVE, WRITING_DOC, detect, profile_drift
from repobrief.templates import ADAPTER_TEMPLATE, PROFILE_TEMPLATE, WRITING_TEMPLATE

STATE_DIR = ".repo-brief"
DEFAULT_ADAPTER = "tools/brief_adapter.py"
SKILL_NAME = "document"
SKILL_PATH = ".opencode/skills/%s/SKILL.md" % SKILL_NAME
SKILL_SOURCE = Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md"
TODO_RE = re.compile(r"#\s*TODO", re.M)
# Any ISO date near the top counts. Repos spell this differently -- `**Updated:**`,
# `Updated:`, frontmatter `date:` -- and the question is only whether the doc
# still carries evidence of when it was last true.
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATE_HEAD_LINES = 15
# A query that names the past is asking for the archive. These are explicit
# enough not to fire by accident; `old` and `past` are not on the list.
ARCHIVE_WORDS = {"archive", "archived", "archiving", "history", "historical", "superseded", "legacy"}


def load_adapter(root: Path, explicit: str | None = None) -> Adapter:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(root / DEFAULT_ADAPTER)
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("repo_brief_local_adapter", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        local = getattr(module, "Adapter", None)
        if isinstance(local, type) and issubclass(local, Adapter):
            return local()
    return GenericAdapter()


def cmd_index(args) -> int:
    root = Path(args.root).resolve()
    adapter = load_adapter(root, args.adapter)
    detected = adapter.detected(root) if isinstance(adapter, GenericAdapter) else detect(root)
    units = adapter.units(root)
    decisions = adapter.decisions(root)
    open_work = adapter.open_work(root)
    edges, _areas = graph_mod.build(root, adapter, units, detected.source_root, detected.autoloads)

    options = {"edges": edges, "coverage": _coverage_by_source(units)}
    structure = render.render_map(
        detected,
        units,
        decisions,
        open_work,
        adapter.name,
        descriptions=False,
        **options,
    )
    digest = render.structure_digest(structure)
    markdown = render.stamp(
        render.render_map(detected, units, decisions, open_work, adapter.name, **options),
        digest,
    )
    state = root / STATE_DIR
    target = state / "map.md"

    if args.check:
        if not target.is_file():
            print("stale: %s/%s is missing" % (STATE_DIR, "map.md"), file=sys.stderr)
            return 1
        stored = target.read_text(encoding="utf-8")
        found = render.STRUCTURE_RE.search(stored)
        if found is None or found.group(1) != digest:
            print("stale: run `repo-brief index`", file=sys.stderr)
            return 1
        note = "ok: map structure is current (%d units)" % len(units)
        if stored != markdown:
            note += "; descriptions refresh on the next index"
        print(note)
        return 0

    state.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    (state / "cache.json").write_text(
        json.dumps(
            {"adapter": adapter.name, "detected": detected.to_dict(), "units": [u.path for u in units]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print("indexed %d units, %d decisions, %d open items -> %s/map.md"
          % (len(units), len(decisions), len(open_work), STATE_DIR))
    candidate_count = len(archive_mod.candidates(root, adapter, units, edges, adapter.canonical(root)))
    if candidate_count:
        print("%d doc(s) look archivable; run `repo-brief archive`" % candidate_count, file=sys.stderr)
    return 0


def _coverage_by_source(units) -> dict:
    """Invert test->sources into source->tests for the map."""
    reverse: dict[str, list] = {}
    for test, sources in kernel.tests_for(units).items():
        for source in sources:
            reverse.setdefault(source, []).append(test)
    for tests in reverse.values():
        tests.sort()
    return reverse


def cmd_context(args) -> int:
    root = Path(args.root).resolve()
    adapter = load_adapter(root, args.adapter)
    query = " ".join(args.query)
    archived = args.archived or bool(set(kernel.tokenize(query)) & ARCHIVE_WORDS)
    context = kernel.build_context(root, adapter, query, args.limit, archived=archived)
    if args.json:
        print(json.dumps({k: v for k, v in context.items() if k != "top_paths"}, indent=2))
    elif args.brief:
        print(render.render_brief(context))
    else:
        print(render.render_full(context))
    return 0


def _unresolved_references(root: Path, units) -> list[tuple[str, str]]:
    """`res://` paths a file names that are not on disk.

    The old indexer had this as `--check-refs`; it is the one integrity check
    a derived map cannot do for you, because it compares the source to itself.
    """
    from repobrief.parsers import ref_to_repo

    broken = []
    for unit in units:
        refs = list(unit.fields.get("loads") or []) + list(unit.fields.get("scripts") or [])
        for ref in refs:
            target = ref_to_repo(ref) if ref.startswith("res://") else ref
            if not target or any(token in target for token in "%{}*$"):
                # A formatted path is built at runtime; it cannot be checked statically.
                continue
            if not (root / target).is_file():
                broken.append((unit.path, target))
    return sorted(set(broken))


def cmd_graph(args) -> int:
    root = Path(args.root).resolve()
    adapter = load_adapter(root, args.adapter)
    detected = adapter.detected(root) if isinstance(adapter, GenericAdapter) else detect(root)
    units = adapter.units(root)
    if not args.archived:
        units = [unit for unit in units if unit.kind != "archive"]
    edges, areas = graph_mod.build(root, adapter, units, detected.source_root, detected.autoloads)

    modes = sum(bool(value) for value in (args.area, args.task, args.hubs))
    if modes > 1:
        print("use only one of an area, --task, or --hubs", file=sys.stderr)
        return 2

    if args.kinds:
        kinds = tuple(part.strip() for part in args.kinds.split(",") if part.strip())
    else:
        kinds = graph_mod.ALL_KINDS if args.all else graph_mod.DEFAULT_KINDS

    if args.task:
        context = kernel.build_context(root, adapter, args.task, args.limit)
        found = graph_mod.task_graph(context, edges, kinds)
    elif args.hubs:
        found = graph_mod.hub_graph(units, edges, areas, detected.autoloads)
    elif args.area:
        found = graph_mod.area_graph(units, edges, areas, args.area, kinds)
    else:
        found = graph_mod.package_graph(units, edges, areas, kinds)

    if args.json:
        print(json.dumps(
            {"title": found["title"], "kinds": found["kinds"], "nodes": found["nodes"], "edges": found["edges"]},
            indent=2,
        ))
    else:
        print(graph_mod.mermaid(found))
    return 0


def cmd_archive(args) -> int:
    root = Path(args.root).resolve()
    adapter = load_adapter(root, args.adapter)

    if args.apply:
        if not args.paths:
            print("usage: repo-brief archive --apply <path> [<path> ...]", file=sys.stderr)
            return 2
        return archive_mod.apply(root, adapter, args.paths, dry_run=args.dry_run)

    if args.paths:
        print("paths need --apply: repo-brief archive --apply <path> ...", file=sys.stderr)
        return 2

    detected = adapter.detected(root) if isinstance(adapter, GenericAdapter) else detect(root)
    units = adapter.units(root)
    edges, _areas = graph_mod.build(root, adapter, units, detected.source_root, detected.autoloads)
    found = archive_mod.candidates(root, adapter, units, edges, adapter.canonical(root))

    if not found:
        print("nothing looks archivable")
        return 0

    print("%d candidate(s)" % len(found))
    print()
    for item in found:
        print("  %-46s %-18s %s" % (item.path, item.reason, item.detail))
    print()
    print("Move with `repo-brief archive --apply <path> ...` (add --dry-run to preview).")
    return 0


def cmd_preflight(args) -> int:
    root = Path(args.root).resolve()
    adapter = load_adapter(root, args.adapter)
    detected = adapter.detected(root) if isinstance(adapter, GenericAdapter) else detect(root)
    state = root / STATE_DIR
    failures, warnings = [], []

    if not (state / "map.md").is_file():
        failures.append("no map; run `repo-brief index`")
    else:
        print("ok: map present")

    for rel in adapter.canonical(root):
        path = root / rel
        if not path.is_file():
            failures.append("canonical doc missing: %s" % rel)
        else:
            print("ok: %s exists" % rel)

    for rel in adapter.dated(root):
        path = root / rel
        if not path.is_file():
            failures.append("dated doc missing: %s" % rel)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        head = "\n".join(text.splitlines()[:DATE_HEAD_LINES])
        if DATE_RE.search(head):
            print("ok: %s is dated" % rel)
        else:
            warnings.append("%s has no date in its first %d lines" % (rel, DATE_HEAD_LINES))

    adapter_path = root / DEFAULT_ADAPTER
    if adapter_path.is_file():
        todos = len(TODO_RE.findall(adapter_path.read_text(encoding="utf-8")))
        if todos:
            warnings.append("adapter has %d unfinished TODO(s)" % todos)
        else:
            print("ok: adapter has no TODOs")
    else:
        warnings.append("no adapter yet; using the generic one (`repo-brief scaffold`)")

    skill_path = root / SKILL_PATH
    if not skill_path.is_file():
        warnings.append("no %s skill; run `repo-brief skill`" % SKILL_NAME)
    elif skill_path.read_text(encoding="utf-8") != SKILL_SOURCE.read_text(encoding="utf-8"):
        failures.append(
            "skill drifted from the shipped copy: %s (run `repo-brief skill`)" % SKILL_PATH
        )
    else:
        print("ok: %s skill matches the shipped copy" % SKILL_NAME)
        if not (root / WRITING_DOC).is_file():
            failures.append(
                "skill installed but %s is missing; run `repo-brief writing`" % WRITING_DOC
            )

    if not detected.decisions_dir and not detected.decisions_file:
        warnings.append("no decision log detected")

    profile_path = state / "profile.json"
    if not profile_path.is_file():
        warnings.append("no profile; run `repo-brief scaffold`")
    else:
        try:
            stored = json.loads(profile_path.read_text(encoding="utf-8")).get("detected", {})
        except (OSError, ValueError):
            stored = {}
        drift = profile_drift(stored, detected.to_dict())
        if drift:
            warnings.append(
                "profile is stale (%s); run `repo-brief profile`" % ", ".join(drift)
            )
        else:
            print("ok: profile matches detection")

    units = adapter.units(root)
    broken = _unresolved_references(root, units)
    if broken:
        for unit_path, target in broken[:5]:
            print("FAIL: %s points at missing `%s`" % (unit_path, target))
        if len(broken) > 5:
            print("FAIL: ... and %d more" % (len(broken) - 5))
        failures.append("%d unresolved res:// reference(s)" % len(broken))
    else:
        print("ok: all res:// references resolve")

    edges, _areas = graph_mod.build(root, adapter, units, detected.source_root, detected.autoloads)
    stale_docs = len(archive_mod.candidates(root, adapter, units, edges, adapter.canonical(root)))
    if stale_docs:
        warnings.append("%d doc(s) look archivable; run `repo-brief archive`" % stale_docs)

    for message in failures:
        print("FAIL: %s" % message)
    for message in warnings:
        print("warn: %s" % message)
    return 1 if failures else 0


def cmd_doctor(args) -> int:
    root = Path(args.root).resolve()
    adapter = load_adapter(root, args.adapter)
    detected = adapter.detected(root) if isinstance(adapter, GenericAdapter) else detect(root)

    print("root:      %s" % root)
    print("adapter:   %s" % adapter.name)
    print("files:     %d" % detected.total_files)
    print("parsers:   %s" % ", ".join(detected.parsers) or "none")
    print("manifests: %s" % (", ".join(detected.manifests) or "none"))
    print("decisions: %s" % (detected.decisions_dir or detected.decisions_file or "none"))
    print("status:    %s" % (detected.status or "none"))
    print("open work: %s" % (", ".join(detected.open_work) or "none"))
    print("tests:     %s" % (", ".join(detected.test_dirs) or "none"))
    print("ci:        %d workflow(s)" % len(detected.ci))
    print("autoloads: %s" % (", ".join(sorted(detected.autoloads)) or "none"))
    print("archive:   %s" % (", ".join(detected.archived) or "none"))
    print()
    units = adapter.units(root)
    edges, areas = graph_mod.build(root, adapter, units, detected.source_root, detected.autoloads)
    empty = [unit.path for unit in units if not unit.fields]
    print("units:     %d (%d with no declared fields)" % (len(units), len(empty)))
    print("edges:     %d across %d area(s)" % (len(edges), len({a for a in areas.values() if a})))
    print("decisions: %d parsed" % len(adapter.decisions(root)))
    print("open work: %d parsed" % len(adapter.open_work(root)))
    return 0


def cmd_scaffold(args) -> int:
    root = Path(args.root).resolve()
    detected = detect(root)
    state = root / STATE_DIR
    adapter_path = root / DEFAULT_ADAPTER

    if adapter_path.is_file() and not args.force:
        print("adapter already exists: %s (use --force to overwrite)" % adapter_path, file=sys.stderr)
        return 1

    state.mkdir(parents=True, exist_ok=True)
    adapter_path.parent.mkdir(parents=True, exist_ok=True)
    name = root.name
    canonical = [
        rel
        for rel in (detected.index, detected.status, detected.changelog, *detected.open_work[:1])
        if rel
    ]
    adapter_path.write_text(
        ADAPTER_TEMPLATE.format(
            name=name,
            decisions=detected.decisions_dir or detected.decisions_file or "none",
            open_work=", ".join(detected.open_work) or "none",
            canonical=json.dumps(canonical),
        ),
        encoding="utf-8",
    )

    profile_path = state / "profile.json"
    if not profile_path.is_file() or args.force:
        write_profile(root, detected)

    writing_path = root / WRITING_DOC
    if not writing_path.is_file() or args.force:
        writing_path.parent.mkdir(parents=True, exist_ok=True)
        writing_path.write_text(_writing_text(detected, canonical), encoding="utf-8")

    skill_path = root / SKILL_PATH
    if not skill_path.is_file() or args.force:
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(SKILL_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")

    print("wrote %s" % adapter_path.relative_to(root))
    print("wrote %s" % profile_path.relative_to(root))
    print("wrote %s" % WRITING_DOC)
    print("wrote %s" % SKILL_PATH)
    print()
    if detected.archived:
        print("archive:   %s" % ", ".join(detected.archived))
    else:
        print("archive:   none; `repo-brief archive --apply` will create %s" % DEFAULT_ARCHIVE)
    print("Next: open the adapter and answer the two TODOs.")
    print("Then: `repo-brief context --brief \"<something you'd actually ask>\"`")
    return 0


def write_profile(root: Path, detected) -> Path:
    state = root / STATE_DIR
    state.mkdir(parents=True, exist_ok=True)
    path = state / "profile.json"
    facts = detected.to_dict()
    # The profile is committed, so it must not carry the machine it was made
    # on: an absolute root would go stale in every other checkout.
    facts.pop("root", None)
    path.write_text(
        PROFILE_TEMPLATE.format(
            detected=json.dumps(facts, indent=4, sort_keys=True),
        ),
        encoding="utf-8",
    )
    return path


def cmd_profile(args) -> int:
    """Refresh the recorded facts without touching the adapter.

    `scaffold` writes the profile once. A repo grows a test directory, renames
    its backlog, or swaps a manifest, and the snapshot ages out; this rewrites
    just the facts and leaves the adapter's judgements alone.
    """
    root = Path(args.root).resolve()
    path = write_profile(root, detect(root))
    print("wrote %s" % path.relative_to(root))
    return 0


def _writing_text(detected, canonical) -> str:
    return WRITING_TEMPLATE.format(
        decisions=detected.decisions_dir or detected.decisions_file or "none yet; add one",
        open_work=", ".join(detected.open_work) or "none detected",
        canonical=", ".join(canonical) or "none",
        capture=CAPTURE_DIR,
        archive=", ".join(detected.archived) or "%s (created on first `archive --apply`)" % DEFAULT_ARCHIVE,
    )


def cmd_writing(args) -> int:
    """Write the conventions the `document` skill reads before it writes anything.

    The shape of a repo's docs is not something a tool can infer reliably, so the
    detected facts are filled in and the judgment is stated once, in one file,
    instead of being re-invented every session.
    """
    root = Path(args.root).resolve()
    path = root / WRITING_DOC
    if path.is_file() and not args.force:
        print("%s exists (use --force to rewrite)" % WRITING_DOC, file=sys.stderr)
        return 1
    adapter = load_adapter(root, args.adapter)
    detected = adapter.detected(root) if isinstance(adapter, GenericAdapter) else detect(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_writing_text(detected, adapter.canonical(root)), encoding="utf-8")
    print("wrote %s" % WRITING_DOC)
    return 0


def cmd_skill(args) -> int:
    """Install the `document` skill into this repo, or verify the installed copy.

    The skill travels with the repo so the workflow works on any machine, but it
    is not hand-maintained: `preflight` compares it to the shipped file, so an
    edited copy fails loudly instead of drifting. The repo-specific half lives in
    `docs/meta/writing.md`, which the skill reads.
    """
    root = Path(args.root).resolve()
    target = root / SKILL_PATH
    shipped = SKILL_SOURCE.read_text(encoding="utf-8")

    if args.check:
        if not target.is_file():
            print("missing: %s (run `repo-brief skill`)" % SKILL_PATH, file=sys.stderr)
            return 1
        if target.read_text(encoding="utf-8") != shipped:
            print("drifted: %s (run `repo-brief skill`)" % SKILL_PATH, file=sys.stderr)
            return 1
        print("ok: %s matches the shipped skill" % SKILL_PATH)
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(shipped, encoding="utf-8")
    print("wrote %s" % SKILL_PATH)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="repo-brief", description=__doc__)
    parser.add_argument("--root", default=".", help="repository to inspect (default: cwd)")
    parser.add_argument("--adapter", help="path to an adapter module (default: tools/brief_adapter.py)")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="write or verify the repo map")
    index.add_argument("--check", action="store_true", help="fail if the map is stale")
    index.set_defaults(func=cmd_index)

    context = sub.add_parser("context", help="rank what matters for a query")
    context.add_argument("query", nargs="+")
    context.add_argument("--brief", action="store_true", help="one screen")
    context.add_argument("--json", action="store_true")
    context.add_argument("--archived", action="store_true", help="include archived material")
    context.add_argument("--limit", type=int, default=6)
    context.set_defaults(func=cmd_context)

    preflight = sub.add_parser("preflight", help="check the derived layer is still true")
    preflight.set_defaults(func=cmd_preflight)

    graph = sub.add_parser("graph", help="dependency view over the units")
    graph.add_argument("area", nargs="?", help="expand one area")
    graph.add_argument("--task", help="only the files a query surfaces")
    graph.add_argument("--hubs", action="store_true", help="singletons and the areas that reach them")
    graph.add_argument("--limit", type=int, default=8)
    graph.add_argument("--kinds", help="comma-separated edge kinds (default: %s)" % ",".join(graph_mod.DEFAULT_KINDS))
    graph.add_argument("--all", action="store_true", help="include scene and doc edges")
    graph.add_argument("--archived", action="store_true", help="include archived material")
    graph.add_argument("--json", action="store_true")
    graph.set_defaults(func=cmd_graph)

    archive = sub.add_parser("archive", help="review or move superseded docs")
    archive.add_argument("paths", nargs="*")
    archive.add_argument("--apply", action="store_true", help="move the listed paths")
    archive.add_argument("--dry-run", action="store_true")
    archive.set_defaults(func=cmd_archive)

    doctor = sub.add_parser("doctor", help="show what was detected and parsed")
    doctor.set_defaults(func=cmd_doctor)

    scaffold = sub.add_parser("scaffold", help="generate an adapter and profile")
    scaffold.add_argument("--force", action="store_true")
    scaffold.set_defaults(func=cmd_scaffold)

    profile = sub.add_parser("profile", help="refresh the recorded facts only")
    profile.set_defaults(func=cmd_profile)

    writing = sub.add_parser("writing", help="write the doc conventions the skill reads")
    writing.add_argument("--force", action="store_true")
    writing.set_defaults(func=cmd_writing)

    skill = sub.add_parser("skill", help="install or verify the document skill")
    skill.add_argument("--check", action="store_true", help="fail if the installed copy drifted")
    skill.set_defaults(func=cmd_skill)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
