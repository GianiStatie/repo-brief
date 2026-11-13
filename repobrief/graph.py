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
from collections import Counter
from pathlib import Path

from .contract import Adapter, Edge, Unit

DEFAULT_KINDS = ("extends", "loads", "uses")
ALL_KINDS = DEFAULT_KINDS + ("references", "doc")

# A capitalised identifier: the only shape a Java type reference takes.
JAVA_TYPE_WORD_RE = re.compile(r"\b[A-Z][\w$]*")
# `com.example.jojo.Dio` written inline, the way legacy code dodges a name clash.
JAVA_QUALIFIED_RE = re.compile(r"\b(?:[a-z_][\w$]*\.)+[A-Z][\w$]*")
# The share of the classes below a package prefix that one sub-package must hold
# for the walk to descend into it. Stubs in a corner must not stop it at `com`.
JAVA_BASE_SHARE = 0.8


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


def is_java(unit: Unit) -> bool:
    return unit.fields.get("language") == "java"


def class_table(units: list[Unit]) -> dict[str, str]:
    """Class name -> file, for languages whose class names are global.

    Live code is read before the archive, so an archived copy never takes a
    name from the file that replaced it. A name two files declare is ambiguous
    and resolves nowhere: a guessed edge is worse than a missing one. Java is
    left out; its names are scoped by package (see `java_edges`).
    """
    table: dict[str, str] = {}
    claimed: set[str] = set()
    for archived in (False, True):
        seen: dict[str, str] = {}
        ambiguous: set[str] = set()
        for unit in units:
            name = unit.fields.get("class_name")
            if not name or is_java(unit) or (unit.kind == "archive") != archived:
                continue
            if seen.setdefault(name, unit.path) != unit.path:
                ambiguous.add(name)
        for name, path in seen.items():
            if name not in ambiguous and name not in claimed:
                table[name] = path
        claimed.update(seen)
    return table


def java_index(units: list[Unit]) -> dict[str, str]:
    """Fully qualified class name -> file. Live code wins over an archived copy."""
    index: dict[str, str] = {}
    for unit in sorted(units, key=lambda unit: unit.kind == "archive"):
        name = unit.fields.get("class_name")
        if name and is_java(unit):
            package = unit.fields.get("package") or ""
            index.setdefault(package + "." + name if package else name, unit.path)
    return index


def _java_lookup(name: str, index: dict[str, str]) -> str:
    """A qualified name's file. `a.Outer.Inner` lives in `Outer`'s file."""
    parts = name.split(".")
    while parts:
        found = index.get(".".join(parts))
        if found:
            return found
        if len(parts) < 2 or not parts[-2][:1].isupper():
            return ""
        parts.pop()
    return ""


def java_resolver(unit: Unit, index: dict[str, str]):
    """How one file names other classes: its imports, its package, its wildcards.

    A simple name that is neither imported nor in the file's own package cannot
    be a class of this repo, so it resolves to nothing rather than to a guess.
    """
    package = unit.fields.get("package") or ""
    explicit: dict[str, str] = {}
    wildcards: list[str] = []
    for name in unit.fields.get("imports") or []:
        if name.endswith(".*"):
            wildcards.append(name[:-2])
        else:
            explicit.setdefault(name.rsplit(".", 1)[-1], name)

    def resolve(name: str) -> str:
        head, _, rest = name.partition(".")
        if head[:1].islower():
            return _java_lookup(name, index)
        if head in explicit:
            return _java_lookup(explicit[head] + ("." + rest if rest else ""), index)
        for base in [package] + wildcards:
            found = _java_lookup(base + "." + name if base else name, index)
            if found:
                return found
        return ""

    return resolve


def java_edges(units: list[Unit]) -> list[Edge]:
    """Inheritance and references between Java files, resolved per package."""
    index = java_index(units)
    if not index:
        return []
    known = {name.rsplit(".", 1)[-1] for name in index}
    found = []
    for unit in units:
        if not is_java(unit):
            continue
        resolve = java_resolver(unit, index)
        imported = {name.rsplit(".", 1)[-1] for name in unit.fields.get("imports") or []}
        for parent in [unit.fields.get("extends")] + list(unit.fields.get("implements") or []):
            if parent:
                found.append(Edge(unit.path, resolve(parent), "extends"))
        words = set(unit.words) if unit.words else set(JAVA_TYPE_WORD_RE.findall(unit.text))
        for word in words:
            # A word no class or import is named after cannot resolve.
            if word in known or word in imported:
                found.append(Edge(unit.path, resolve(word), "uses"))
        names = (
            {word for word in unit.words if "." in word}
            if unit.words
            else set(JAVA_QUALIFIED_RE.findall(unit.text))
        )
        for name in names:
            found.append(Edge(unit.path, resolve(name), "uses"))
    return found


def infer_edges(units: list[Unit], extra: list[Edge] | None = None, autoloads: dict | None = None) -> list[Edge]:
    """Everything the kernel can see without being told.

    Class names and autoload names both act as symbols a file can reference.
    An autoload is a name the project file declares, not a `class_name`, so it
    has to be passed in.
    """
    paths = {unit.path for unit in units}
    symbols = dict(autoloads or {})
    symbols.update(class_table(units))

    found: set[Edge] = set(edge for edge in (extra or []))

    def add(source: str, target: str, kind: str) -> None:
        if source and target and source != target and target in paths:
            found.add(Edge(source=source, target=target, kind=kind))

    for unit in units:
        extends = str(unit.fields.get("extends") or "").strip('"')
        if extends and not is_java(unit):
            add(unit.path, symbols.get(extends, ""), "extends")
        for ref in unit.fields.get("loads", []) or []:
            add(unit.path, ref, "loads")
        if unit.path.endswith(".gd"):
            # Any word-boundary mention of a declared symbol is a dependency:
            # type annotations and `Name.member` both count.
            words = set(unit.words) if unit.words else set(re.findall(r"[A-Za-z_]\w*", unit.text))
            for name in words & symbols.keys():
                add(unit.path, symbols[name], "uses")
        for script in unit.fields.get("scripts", []) or []:
            add(unit.path, script, "references")
        for link in unit.fields.get("links", []) or []:
            add(unit.path, resolve_doc(link, paths), "doc")
    for edge in java_edges(units):
        add(edge.source, edge.target, edge.kind)

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


def java_base_package(units: list[Unit]) -> str:
    """The package most product classes live under: `com.example.jojo`.

    Walk down while one sub-package holds at least `JAVA_BASE_SHARE` of the
    classes below the current prefix. Generated stubs and vendored packages in
    a corner of the tree do not drag the base back up to `com`.
    """
    packages = [
        unit.fields["package"].split(".")
        for unit in units
        if is_java(unit) and unit.kind == "source" and unit.fields.get("package")
    ]
    base: list[str] = []
    while True:
        under = [parts for parts in packages if parts[: len(base)] == base]
        deeper = Counter(parts[len(base)] for parts in under if len(parts) > len(base))
        if not deeper:
            break
        segment, count = deeper.most_common(1)[0]
        if count < JAVA_BASE_SHARE * len(under):
            break
        base.append(segment)
    return ".".join(base)


def java_area(package: str, base: str) -> str:
    """`com.example.jojo.stands.model` under `com.example.jojo` -> `stands`.

    Empty outside the base, like a file outside `source_root`, and in the base
    package itself, like a file at the product root.
    """
    if not base:
        return package.split(".")[0] if package else ""
    if not package.startswith(base + "."):
        return ""
    return package[len(base) + 1:].split(".")[0]


def build_areas(root: Path, adapter: Adapter, units: list[Unit], source_root: str) -> dict[str, str]:
    declared = adapter.areas(root)
    base = java_base_package(units)
    areas = {}
    for unit in units:
        if is_java(unit):
            inferred = java_area(unit.fields.get("package") or "", base)
        else:
            inferred = area_of(unit.path, source_root)
        areas[unit.path] = declared.get(unit.path) or inferred
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
    # A whole path component: area `rush` must not match inside `ora-rush/`.
    prefix = "/" + area + "/"
    nodes = []
    for unit in sorted(units, key=lambda item: item.path):
        if unit.path not in paths:
            continue
        path = "/" + unit.path
        index = path.find(prefix)
        label = path[index + len(prefix):] if index >= 0 else unit.path
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


def dependents(units, edges, name: str, kinds=DEFAULT_KINDS) -> dict:
    """The files that use a class or file: what a change to it reaches.

    `name` is a repo path, a file name, a class name or a qualified Java name; a
    name several files declare names them all. Only links count, so a template,
    a config file or a method call that names the class is not a user.
    """
    name = name.strip()
    targets = set()
    for unit in units:
        declared = unit.fields.get("class_name") or ""
        package = unit.fields.get("package") or ""
        qualified = package + "." + declared if declared and package else declared
        if name and unit.kind != "archive" and name in (unit.path, Path(unit.path).name, declared, qualified):
            targets.add(unit.path)
    sources = {unit.path for unit in units if visible(unit, kinds)} - targets
    users: dict[str, str] = {}
    for edge in edges:
        if edge.target in targets and edge.kind in kinds and edge.source in sources:
            # A subclass that also names its parent is a subclass first.
            if users.get(edge.source) != "extends":
                users[edge.source] = edge.kind
    return {
        "name": name,
        "targets": sorted(targets),
        "users": [{"path": path, "kind": kind} for path, kind in sorted(users.items())],
    }


def render_dependents(found: dict) -> str:
    """The users by directory, subclasses marked: a Java directory is a package."""
    if not found["targets"]:
        return "No class or file is named %r." % found["name"]
    lines = ["%s: %s" % (found["name"], ", ".join(found["targets"]))]
    unlinked = "Templates, config and method calls are not links: Grep for those."
    if not found["users"]:
        return "\n".join(lines + ["Nothing links to it. " + unlinked])
    folders: dict[str, list[str]] = {}
    for user in found["users"]:
        folder, _, file = user["path"].rpartition("/")
        folders.setdefault(folder, []).append(file + ("*" if user["kind"] == "extends" else ""))
    lines.append(
        "Used by %d files in %d directories (* extends or implements it):"
        % (len(found["users"]), len(folders))
    )
    for folder, files in sorted(folders.items()):
        lines.append("- %s%s" % (folder + "/: " if folder else "", ", ".join(files)))
    return "\n".join(lines + [unlinked])


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
