"""The project overview: a cheap, always-current spec of the repo.

Two halves, deliberately separate:

- **Facts** are derived from the tree on every `update`: stack, layout, areas
  and their dependencies, entry points, tests, docs, commands. No model call,
  no judgement, never stale.
- **Written** is the part a model writes (purpose, flows, glossary) and an
  update must not clobber. It lives between two markers and is carried over
  verbatim until someone asks for a rewrite.

The `ask` agent starts from the facts half, so it spends its turns on the
question instead of rediscovering the repo's shape.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

from . import catalog, graph as graph_mod

OVERVIEW = ".repo-brief/overview.md"
WRITTEN_START = "<!-- written:start -->"
WRITTEN_END = "<!-- written:end -->"
DIGEST_RE = re.compile(r"<!-- facts: ([0-9a-f]+) -->")
MAIN_SCENE_RE = re.compile(r'^run/main_scene="([^"]+)"', re.M)
PLACEHOLDER = (
    "%s\n_Not written yet. An agent with a runner fills this section: what the project\n"
    "is for, the main flows, and the words the domain uses. `repo-brief update` keeps it._\n%s"
    % (WRITTEN_START, WRITTEN_END)
)
BIG_DIRS = {"project", "src", "app", "packages", "modules", "apps", "lib"}


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def _written(existing: str) -> str:
    if WRITTEN_START in existing and WRITTEN_END in existing:
        start = existing.index(WRITTEN_START)
        end = existing.index(WRITTEN_END) + len(WRITTEN_END)
        return existing[start:end]
    return PLACEHOLDER


def _summary(root: Path, detected) -> str:
    """The first real paragraph of the front-door doc, then the hard facts."""
    lead = ""
    if detected.index:
        try:
            text = (root / detected.index).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for block in re.split(r"\n\s*\n", text):
            stripped = block.strip()
            if not stripped or stripped.startswith(("#", "<!--", "---", "|", "```")):
                continue
            if stripped.startswith("[!["):
                continue
            lead = re.sub(r"\s+", " ", stripped)
            break
    languages = ", ".join("%s %d" % (lang, count) for lang, count in detected.languages[:4])
    parts = []
    if lead:
        parts.append(lead[:600])
    parts.append(
        "%d files: %s. Product code in %s."
        % (detected.total_files, languages or "no detected languages", ", ".join(
            "`%s`" % path for path in detected.source_roots
        ) or "no single source root")
    )
    return "\n\n".join(parts)


def _top_dirs(files) -> list[tuple[str, int]]:
    counts: Counter = Counter()
    for relative in files:
        parts = relative.parts
        if not parts:
            continue
        key = parts[0]
        if key in BIG_DIRS and len(parts) > 1:
            key = "%s/%s" % (parts[0], parts[1])
        counts[key] += 1
    return counts.most_common(12)


def _area_rows(units, edges, areas) -> tuple[list[tuple[str, int]], list[tuple[str, str, int]]]:
    counts: Counter = Counter()
    for unit in units:
        area = areas.get(unit.path) or "(root)"
        counts[area] += 1
    deps: Counter = Counter()
    for edge in edges:
        source = areas.get(edge.source) or "(root)"
        target = areas.get(edge.target) or "(root)"
        if source != target:
            deps[(source, target)] += 1
    return counts.most_common(20), deps.most_common(15)


def _entry_points(root: Path, detected) -> list[str]:
    lines = []
    project = next((rel for rel in ("project.godot", "godot/project.godot") if rel in detected.paths), "")
    if project:
        try:
            text = (root / project).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        scene = MAIN_SCENE_RE.search(text)
        if scene:
            lines.append("- Main scene: `%s`" % scene.group(1))
        if detected.autoloads:
            listed = ", ".join(
                "`%s`" % name for name in sorted(detected.autoloads)[:12]
            )
            lines.append("- Autoloads: %s" % listed)
    if detected.source_roots:
        lines.append(
            "- Source roots: %s" % ", ".join("`%s`" % path for path in detected.source_roots)
        )
    if detected.manifests:
        lines.append("- Manifests: %s" % ", ".join("`%s`" % rel for rel in detected.manifests))
    return lines or ["- None detected."]


def facts(root: Path, adapter, detected, units) -> str:
    edges, areas = graph_mod.build(root, adapter, units, detected.source_root, detected.autoloads)
    area_rows, deps = _area_rows(units, edges, areas)
    kinds = Counter(unit.kind for unit in units)
    lines = [
        "## Summary",
        "",
        _summary(root, detected),
        "",
        "## Stack",
        "",
        "| What | Value |",
        "|---|---|",
        "| Languages | %s |" % (", ".join(
            "%s %d" % (lang, count) for lang, count in detected.languages[:8]
        ) or "none detected"),
        "| Manifests | %s |" % (", ".join("`%s`" % rel for rel in detected.manifests) or "none"),
        "| CI | %s |" % (", ".join("`%s`" % rel for rel in detected.ci[:6]) or "none"),
        "| Units | %d (%s) |" % (
            len(units),
            ", ".join("%s %d" % (kind, count) for kind, count in kinds.most_common()),
        ),
        "",
        "## Layout",
        "",
        "| Directory | Files |",
        "|---|---:|",
    ]
    lines += ["| `%s` | %d |" % (name, count) for name, count in _top_dirs(detected.files)]
    lines += ["", "## Areas", "", "| Area | Units |", "|---|---:|"]
    lines += ["| `%s` | %d |" % (name, count) for name, count in area_rows]
    if deps:
        lines += ["", "Dependencies:", ""]
        lines += [
            "- `%s` → `%s` (%d)" % (source, target, count)
            for (source, target), count in deps
        ]
    lines += ["", "## Entry points", ""]
    lines += _entry_points(root, detected)
    lines += ["", "## Tests", ""]
    if detected.test_dirs:
        lines += ["- %s" % ", ".join("`%s`" % path for path in detected.test_dirs)]
        lines += ["- %d test unit(s)" % kinds.get("test", 0)]
    else:
        lines.append("- None detected.")
    lines += ["", "## Docs", ""]
    lines += [
        "- Decisions: %s" % (
            "`%s`" % (detected.decisions_dir or detected.decisions_file)
            if (detected.decisions_dir or detected.decisions_file)
            else "none detected"
        ),
        "- Status: %s" % ("`%s`" % detected.status if detected.status else "none detected"),
        "- Changelog: %s" % ("`%s`" % detected.changelog if detected.changelog else "none detected"),
        "- Open work: %s" % (
            ", ".join("`%s`" % rel for rel in detected.open_work) or "none detected"
        ),
        "- Front door: %s" % ("`%s`" % detected.index if detected.index else "none detected"),
        "- Archive: %s" % (", ".join("`%s`" % rel for rel in detected.archived) or "none"),
    ]
    items = catalog.commands(catalog.load(root))
    if items:
        lines += ["", "## Commands", ""]
        groups: dict[str, list[dict]] = {}
        for item in items:
            groups.setdefault(item.get("group", "manual"), []).append(item)
        for group in catalog.GROUPS:
            if group == "brief" or group not in groups:
                continue
            reach = "run in trusted repos" if catalog.ORCHESTRATOR[group] == "trusted" else "never run"
            examples = ", ".join("`%s`" % item["name"] for item in groups[group][:8])
            more = "" if len(groups[group]) <= 8 else " (+%d more)" % (len(groups[group]) - 8)
            lines.append("- **%s** (%s): %s%s" % (group, reach, examples, more))
    return "\n".join(lines).rstrip() + "\n"


def write(root: Path, adapter, detected, units) -> tuple[Path, bool]:
    target = root / OVERVIEW
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    body = facts(root, adapter, detected, units)
    digest = _digest(body)
    text = (
        "# Overview — %s\n\n"
        "<!-- Generated by `repo-brief update`; the facts are rewritten, the written "
        "section is kept. -->\n"
        "<!-- facts: %s -->\n\n%s\n%s\n"
        % (root.name, digest, body, _written(existing))
    )
    changed = text != existing
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target, changed


def short(root: Path) -> str:
    """The first paragraph of the Summary, for a server's instructions."""
    try:
        text = (root / OVERVIEW).read_text(encoding="utf-8")
    except OSError:
        return ""
    if "## Summary" not in text:
        return ""
    rest = text.split("## Summary", 1)[1]
    for block in re.split(r"\n\s*\n", rest):
        block = block.strip()
        if block and not block.startswith(("#", "<!--")):
            return re.sub(r"\s+", " ", block)[:600]
    return ""
