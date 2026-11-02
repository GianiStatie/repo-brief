"""Per-language file readers.

A parser turns one file into a `Unit`: what it is, what it declares, and the
text the kernel will search. Parsers are shared across every repo using that
language -- unlike adapters, which are per repo.
"""
from __future__ import annotations

import re
from pathlib import Path

from .contract import Unit

MAX_BYTES = 512 * 1024

CLASS_RE = re.compile(r"^class_name\s+([A-Za-z_]\w*)(?:\s+extends\s+([A-Za-z_][\w./:\"]*))?", re.M)
EXTENDS_RE = re.compile(r"^extends\s+([A-Za-z_][\w./:\"]*)", re.M)
FUNC_RE = re.compile(r"^(?:static\s+)?func\s+([A-Za-z_]\w*)\s*\(", re.M)
SIGNAL_RE = re.compile(r"^signal\s+([A-Za-z_]\w*)", re.M)
CONST_RE = re.compile(r"^const\s+([A-Za-z_]\w*)", re.M)
VAR_RE = re.compile(r"^(?:@export[^\n]*\b|@onready\s+)?var\s+([A-Za-z_]\w*)", re.M)

PY_CLASS_RE = re.compile(r"^class\s+([A-Za-z_]\w*)", re.M)
PY_DEF_RE = re.compile(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", re.M)
PY_CONST_RE = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*=", re.M)

MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.M)
MD_FRONT_TAGS_RE = re.compile(r"^tags:\s*\[([^\]]*)\]", re.M)
MD_DOCSTRING_RE = re.compile(r'^"""(.*?)"""', re.M | re.S)

GD_LOAD_RE = re.compile(r'\b(?:load|preload)\s*\(\s*"res://([^"\n]+)"')
WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

SCENE_SCRIPT_RE = re.compile(r'\[ext_resource[^\]]*path="(res://[^"]+\.(?:gd|tscn|tres))"')
SCENE_NODE_RE = re.compile(r'^\[node name="([^"]+)"', re.M)


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def leading_doc(text: str, limit: int = 30) -> str:
    """The `##` block at the top of a script.

    In a Godot repo this is the file's own one-line answer to "what is this",
    and it is often the only place a tool script is described.
    """
    parts = []
    started = False
    for line in text.splitlines()[:limit]:
        stripped = line.strip()
        if stripped.startswith("#"):
            started = True
            parts.append(stripped.lstrip("#").strip())
        elif started:
            break
    return " ".join(part for part in parts if part)[:240]


def parse_gdscript(root: Path, path: Path) -> Unit:
    text = _read(path)
    rel = path.relative_to(root).as_posix()
    fields = {
        "class_name": "",
        "extends": "",
        "language": "gdscript",
        "description": leading_doc(text),
        "loads": sorted({ref_to_repo("res://" + ref) for ref in GD_LOAD_RE.findall(text)}),
        "links": sorted(set(WIKILINK_RE.findall(text))),
    }
    symbols = []

    match = CLASS_RE.search(text)
    if match:
        fields["class_name"] = match.group(1)
        if match.group(2):
            fields["extends"] = match.group(2).strip('"')
    if not fields["extends"]:
        match = EXTENDS_RE.search(text)
        if match:
            fields["extends"] = match.group(1).strip('"')

    for regex, kind in (
        (FUNC_RE, "function"),
        (SIGNAL_RE, "signal"),
        (CONST_RE, "const"),
        (VAR_RE, "var"),
    ):
        for match in regex.finditer(text):
            symbols.append({"kind": kind, "name": match.group(1), "line": _line(text, match.start())})

    return Unit(
        path=rel,
        kind="test" if "/tests/" in rel or rel.startswith("tests/") else "source",
        fields=fields,
        symbols=symbols,
        text=text,
    )


def parse_godot_scene(root: Path, path: Path) -> Unit:
    text = _read(path)
    rel = path.relative_to(root).as_posix()
    scripts = [ref_to_repo(ref) for ref in SCENE_SCRIPT_RE.findall(text)]
    nodes = SCENE_NODE_RE.findall(text)
    return Unit(
        path=rel,
        kind="scene" if path.suffix == ".tscn" else "resource",
        fields={"scripts": scripts, "nodes": nodes, "language": "godot"},
        text=text,
    )


def ref_to_repo(ref: str) -> str:
    if ref.startswith("res://"):
        inner = ref[len("res://"):]
        return "godot/" + inner if not inner.startswith("godot/") else inner
    return ref


def parse_python(root: Path, path: Path) -> Unit:
    text = _read(path)
    rel = path.relative_to(root).as_posix()
    doc = ""
    match = MD_DOCSTRING_RE.search(text)
    if match:
        doc = " ".join(match.group(1).split())[:170]
    fields = {"class_name": "", "extends": "", "description": doc, "language": "python"}
    symbols = []
    match = PY_CLASS_RE.search(text)
    if match:
        fields["class_name"] = match.group(1)
    for regex, kind in ((PY_DEF_RE, "function"), (PY_CLASS_RE, "class"), (PY_CONST_RE, "const")):
        for match in regex.finditer(text):
            symbols.append({"kind": kind, "name": match.group(1), "line": _line(text, match.start())})
    return Unit(
        path=rel,
        kind="test" if "/tests/" in rel or path.name.startswith("test_") else "source",
        fields=fields,
        symbols=symbols,
        text=text,
    )


def parse_markdown(root: Path, path: Path) -> Unit:
    text = _read(path)
    rel = path.relative_to(root).as_posix()
    headings = [m.group(2).strip() for m in MD_HEADING_RE.finditer(text)]
    symbols = [
        {"kind": "heading", "name": m.group(2).strip(), "line": _line(text, m.start())}
        for m in MD_HEADING_RE.finditer(text)
    ][:40]
    tags = []
    match = MD_FRONT_TAGS_RE.search(text)
    if match:
        tags = [t.strip() for t in match.group(1).split(",") if t.strip()]
    return Unit(
        path=rel,
        kind="note",
        fields={
            "headings": headings,
            "tags": tags,
            "language": "markdown",
            "links": sorted(set(WIKILINK_RE.findall(text))),
        },
        symbols=symbols,
        text=text,
    )


def parse_generic(root: Path, path: Path) -> Unit:
    """Path-only unit. Cheap: lets a query match a filename without reading it."""
    return Unit(path=path.relative_to(root).as_posix(), kind="file", text="")


PARSERS = {
    ".gd": parse_gdscript,
    ".tscn": parse_godot_scene,
    ".tres": parse_godot_scene,
    ".py": parse_python,
    ".md": parse_markdown,
}


def collect(root: Path, skip_dirs) -> list[Unit]:
    """Parse every file with a registered parser. Unparsed files are handled by the adapter."""
    units = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if set(path.relative_to(root).parts) & skip_dirs:
            continue
        parser = PARSERS.get(path.suffix.lower())
        if parser is None:
            continue
        units.append(parser(root, path))
    return units
