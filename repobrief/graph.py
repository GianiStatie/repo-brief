"""Dependency views over the derived layer.

A graph here is a **view**, not an artifact. It is computed from the same units
the map is, never committed, and never checked by `preflight`. The invariant --
derived, committed, verified -- applies to the map and the context pack; a graph
is a lens on them.

Edges are structure, so adapters may declare them. Everything the kernel can
infer it does, which is why two repos with 500 lines of near-identical graph
code end up sharing one implementation.
"""
from __future__ import annotations

import re
from pathlib import Path

from .contract import Adapter, Edge, Unit

DEFAULT_KINDS = ("extends", "loads", "uses")
ALL_KINDS = DEFAULT_KINDS + ("references", "doc")


def area_of(path: str, source_root: str) -> str:
    """`godot/src/player/x.gd` -> `player`. Empty for anything outside product code."""
    if not source_root:
        return ""
    prefix = source_root.rstrip("/") + "/"
    if not path.startswith(prefix):
        return ""
    parts = path[len(prefix):].split("/")
    if len(parts) < 2 or not parts[0]:
        return ""
    return parts[0]


def resolve_doc(link: str, paths: set[str]) -> str:
    """Resolve a `[[wikilink]]` to a real file.

    Obsidian links are written relative to the vault root, so `[[technical/x]]`
    means `docs/technical/x.md` in a repo whose docs live under `docs/`.
    """
    return resolve_doc_base(link, paths)[0]


def resolve_doc_base(link: str, paths: set[str]) -> tuple[str, str]:
    """Resolve a wikilink and say which root it was written against.

    The base matters when rewriting a link after a move: a vault rooted at
    `docs/` writes `[[archive/foo]]`, a vault rooted at the repository writes
    `[[docs/archive/foo]]`.
    """
    link = link.replace("\\|", "|")
    link = link.split("|")[0].strip().rstrip("/")
    if not link:
        return "", ""
    for base in ("docs/", ""):
        for suffix in (".md", "/index.md", ""):
            candidate = base + link + suffix
            if candidate in paths:
                return candidate, base
    stem = Path(link).name
    matches = sorted(path for path in paths if Path(path).stem == stem)
    if len(matches) == 1:
        return matches[0], ""
    return "", ""


def infer_edges(units: list[Unit], extra: list[Edge] | None = None, autoloads: dict | None = None) -> list[Edge]:
    """Everything the kernel can see without being told.

    Class names and autoload names both act as symbols a file can reference.
    An autoload is a name the project file declares, not a `class_name`, so it
    has to be passed in.
    """
    paths = {unit.path for unit in units}
    classes: dict[str, str] = {}
    for unit in units:
        name = unit.fields.get("class_name")
        if name and unit.path not in classes:
            classes[name] = unit.path
    symbols = dict(autoloads or {})
    symbols.update(classes)

    found: set[Edge] = set(edge for edge in (extra or []))

    def add(source: str, target: str, kind: str) -> None:
        if source and target and source != target and target in paths:
            found.add(Edge(source=source, target=target, kind=kind))

    for unit in units:
        extends = str(unit.fields.get("extends") or "").strip('"')
        if extends:
            add(unit.path, symbols.get(extends, ""), "extends")
        for ref in unit.fields.get("loads", []) or []:
            add(unit.path, ref, "loads")
        if unit.path.endswith(".gd"):
            # Any word-boundary mention of a declared symbol is a dependency:
            # type annotations and `Name.member` both count.
            words = set(re.findall(r"[A-Za-z_]\w*", unit.text))
            for name in words & symbols.keys():
                add(unit.path, symbols[name], "uses")
        for script in unit.fields.get("scripts", []) or []:
            add(unit.path, script, "references")
        for link in unit.fields.get("links", []) or []:
            add(unit.path, resolve_doc(link, paths), "doc")

    return sorted(found, key=lambda edge: (edge.source, edge.kind, edge.target))


def visible(unit: Unit, kinds) -> bool:
    """Which kinds of unit a dependency view shows.

    A graph of product code is source files by default. Scenes and docs join
    only when their edge kinds are asked for, because a scene node next to a
    class node reads as a dependency it does not have. Archived code comes in
    with `--all`; archived docs come in with doc edges.
    """
    if unit.kind == "source":
        return True
    if unit.kind in ("scene", "resource"):
        return "references" in kinds
    if unit.kind == "note":
        return "doc" in kinds
    if unit.kind == "archive":
        return "doc" in kinds or "references" in kinds
    return False


def build_areas(root: Path, adapter: Adapter, units: list[Unit], source_root: str) -> dict[str, str]:
    declared = adapter.areas(root)
    areas = {}
    for unit in units:
        areas[unit.path] = declared.get(unit.path) or area_of(unit.path, source_root)
    return areas


def _edge_pairs(edges: list[Edge], paths: set[str], kinds: tuple[str, ...]) -> list[dict]:
    return [
        {"from": edge.source, "to": edge.target}
        for edge in edges
        if edge.kind in kinds and edge.source in paths and edge.target in paths
    ]


def package_graph(units, edges, areas, kinds=DEFAULT_KINDS) -> dict:
    """Areas as nodes, area-to-area dependencies as edges."""
    counts: dict[str, int] = {}
    for unit in units:
        if not visible(unit, kinds):
            continue
        area = areas.get(unit.path, "")
        if area:
            counts[area] = counts.get(area, 0) + 1

    pairs = set()
    for edge in edges:
        if edge.kind not in kinds:
            continue
        source = areas.get(edge.source, "")
        target = areas.get(edge.target, "")
        if source and target and source != target:
            pairs.add((source, target))

    return {
        "title": "area dependency graph",
        "kinds": list(kinds),
        "nodes": [
            {"id": area, "label": "%s (%d)" % (area, counts[area])}
            for area in sorted(counts)
        ],
        "edges": [{"from": source, "to": target} for source, target in sorted(pairs)],
    }


def area_graph(units, edges, areas, area: str, kinds=DEFAULT_KINDS) -> dict:
    """File-level dependencies inside one area."""
    paths = {unit.path for unit in units if areas.get(unit.path) == area and visible(unit, kinds)}
    if not paths:
        return {"title": "unknown area: %s" % area, "kinds": list(kinds), "nodes": [], "edges": []}
    prefix = area + "/"
    nodes = []
    for unit in sorted(units, key=lambda item: item.path):
        if unit.path not in paths:
            continue
        index = unit.path.find(prefix)
        label = unit.path[index + len(prefix):] if index >= 0 else unit.path
        nodes.append({"id": unit.path, "label": label})
    return {
        "title": "%s module dependency graph" % area,
        "kinds": list(kinds),
        "nodes": nodes,
        "edges": _edge_pairs(edges, paths, kinds),
    }


def task_graph(context: dict, edges, kinds=DEFAULT_KINDS) -> dict:
    """File-level dependencies among the files a query surfaced."""
    paths = {unit["path"] for unit in context.get("units", []) if unit.get("kind") == "source"}
    return {
        "title": "task graph: %s" % context.get("query", ""),
        "kinds": list(kinds),
        "nodes": [{"id": path, "label": path} for path in sorted(paths)],
        "edges": _edge_pairs(edges, paths, kinds),
    }


def hub_graph(units, edges, areas, autoloads: dict, kinds=("uses",)) -> dict:
    """Singletons as hubs, with the areas that reach them."""
    script_to_name = {path: name for name, path in autoloads.items()}
    users: dict[str, set] = {name: set() for name in autoloads}
    pairs = set()
    for edge in edges:
        if edge.kind not in kinds:
            continue
        name = script_to_name.get(edge.target)
        if not name:
            continue
        users[name].add(edge.source)
        area = areas.get(edge.source, "")
        if area:
            pairs.add((area, name))

    nodes = [
        {"id": "autoload:%s" % name, "label": "%s (%d)" % (name, len(users[name]))}
        for name in sorted(autoloads)
    ]
    nodes.extend({"id": "area:%s" % area, "label": area} for area in sorted({a for a, _ in pairs}))
    nodes.sort(key=lambda node: node["id"])
    return {
        "title": "autoload dependency graph",
        "kinds": list(kinds),
        "nodes": nodes,
        "edges": [
            {"from": "area:%s" % area, "to": "autoload:%s" % name}
            for area, name in sorted(pairs)
        ],
    }


def mermaid(graph: dict) -> str:
    nodes = sorted(graph.get("nodes", []), key=lambda node: node["id"])
    edges = sorted({(edge["from"], edge["to"]) for edge in graph.get("edges", [])})
    ids = {node["id"]: "n%d" % position for position, node in enumerate(nodes)}
    kinds = graph.get("kinds") or []
    suffix = " (%s)" % "/".join(kinds) if kinds else ""
    lines = ["%% " + str(graph.get("title", "dependency graph")) + suffix, "flowchart LR"]
    for node in nodes:
        label = str(node.get("label", node["id"])).replace('"', "'")
        lines.append('  %s["%s"]' % (ids[node["id"]], label))
    for source, target in edges:
        if source in ids and target in ids:
            lines.append("  %s --> %s" % (ids[source], ids[target]))
    return "\n".join(lines)


def build(root: Path, adapter: Adapter, units: list[Unit], source_root: str, autoloads: dict | None = None):
    """Edges and areas, with adapter declarations taking precedence."""
    edges = infer_edges(units, adapter.edges(root), autoloads)
    areas = build_areas(root, adapter, units, source_root)
    return edges, areas
