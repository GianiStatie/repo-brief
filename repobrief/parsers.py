"""Per-language file readers.

A parser turns one file into a `Unit`: what it is, what it declares, and the
text the kernel will search. Parsers are shared across every repo using that
language -- unlike adapters, which are per repo.
"""
from __future__ import annotations

import ast
import bisect
import os
import re
from dataclasses import replace
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

# Java. Declarations are matched on a copy of the source with comments and
# literals blanked, so a `class` in a comment or a `(` in a string cannot turn
# into structure. Blanking keeps every newline, so offsets still map to lines.
JAVA_NOISE_RE = re.compile(
    r'//[^\n]*|/\*.*?\*/|"""(?:\\.|[^\\])*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'',
    re.S,
)
# Annotations may carry arguments over several lines; one level of nested
# parentheses covers `@Size(max = Limits.max())` without a real parser.
JAVA_ANNOTATIONS = r"(?:@(?!interface\b)[\w.$]+(?:\s*\((?:[^()]|\([^()]*\))*\))?\s*)*"
JAVA_MODIFIERS = (
    r"(?:(?:public|protected|private|static|final|abstract|synchronized|native|"
    r"default|strictfp|transient|volatile|sealed|non-sealed)\s+)*"
)
JAVA_TYPE = r"[\w$.]+(?:\s*<[^;{}()=]*?>)?(?:\s*\[\s*\])*"
# A line's indentation, matched atomically: the lookahead captures it and the
# backreference consumes exactly that, so a blanked comment line is not retried
# once per leading space. Python 3.9 has no atomic groups.
JAVA_LINE_START = r"^(?=(?P<indent>[ \t]*))(?P=indent)"
JAVA_PACKAGE_RE = re.compile(r"^[ \t]*package\s+([\w.]+)\s*;", re.M)
JAVA_IMPORT_RE = re.compile(r"^[ \t]*import\s+(static\s+)?([\w$.]+?)(\.\*)?\s*;", re.M)
# Every declaration starts at its first annotation, so a read slice anchored on
# it keeps `@BattleCry(...)` and the Javadoc above it.
JAVA_TYPE_RE = re.compile(
    JAVA_LINE_START + JAVA_ANNOTATIONS + JAVA_MODIFIERS
    + r"(?P<kind>class|interface|enum|record|@interface)\s+(?P<name>[A-Za-z_$][\w$]*)(?P<rest>[^{;]*)",
    re.M,
)
JAVA_METHOD_RE = re.compile(
    JAVA_LINE_START + JAVA_ANNOTATIONS + JAVA_MODIFIERS + r"(?:<[^;{}()=]*?>\s*)?"
    + r"(?:(?P<type>" + JAVA_TYPE + r")\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\(",
    re.M,
)
# What makes `name(...)` a declaration rather than a call: the parameter list is
# followed by a body, a `;` (abstract or interface), `throws`, or `default`.
JAVA_DECLARATION_TAIL_RE = re.compile(
    r"\((?:[^();]|\((?:[^();]|\([^();]*\))*\))*\)"
    r"\s*(?:\[\s*\]\s*)*(?:throws\s+[\w$.,\s<>]+?)?\s*(?:\{|;|default\b)"
)
# Constants are found name first, then checked for a declaration in front of
# the name on the same line: four times faster than trying every line start.
JAVA_CONST_NAME_RE = re.compile(r"\b[A-Z][A-Z0-9_$]+(?=\s*[=;])")
JAVA_CONST_HEAD_RE = re.compile(r"[ \t]*" + JAVA_ANNOTATIONS + JAVA_MODIFIERS + r"(?P<type>" + JAVA_TYPE + r")\s+")
JAVA_EXTENDS_RE = re.compile(r"\bextends\s+(.+?)(?=\bimplements\b|\bpermits\b|$)", re.S)
JAVA_IMPLEMENTS_RE = re.compile(r"\bimplements\s+(.+?)(?=\bpermits\b|$)", re.S)
JAVA_GENERIC_RE = re.compile(r"<[^<>]*>")
JAVADOC_RE = re.compile(r"/\*\*(.*?)\*/", re.S)
# Words that start a statement, so `return foo(` or `else if (` is not a method.
JAVA_STATEMENT_WORDS = {
    "if", "for", "while", "switch", "catch", "synchronized", "return", "new", "throw",
    "else", "case", "do", "try", "finally", "assert", "yield", "super", "this",
    "instanceof", "record", "package", "import",
}

MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.M)
MD_FRONT_TAGS_RE = re.compile(r"^tags:\s*\[([^\]]*)\]", re.M)

GD_LOAD_RE = re.compile(r'\b(?:load|preload)\s*\(\s*"res://([^"\n]+)"')
WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
# A relative markdown link. Images (`![...]`) and external URLs are not edges.
MD_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

SCENE_SCRIPT_RE = re.compile(r'\[ext_resource[^\]]*path="(res://[^"]+\.(?:gd|tscn|tres))"')
SCENE_NODE_RE = re.compile(r'^\[node name="([^"]+)"', re.M)


def read_text(path: Path) -> str:
    """The text every parser reads. Empty for a file too large to be worth it."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def walk_files(root: Path, skip_dirs):
    """Every file, without descending into the skipped trees.

    `rglob` enumerates first and filters second, which is the wrong order for a
    repo with a multi-gigabyte generated directory: pruning at the directory
    level is the difference between a second and half a minute.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in skip_dirs)
        for name in sorted(filenames):
            yield Path(dirpath) / name


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


def parse_gdscript(root: Path, path: Path, text: str | None = None) -> Unit:
    text = read_text(path) if text is None else text
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


def parse_godot_scene(root: Path, path: Path, text: str | None = None) -> Unit:
    text = read_text(path) if text is None else text
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


def _python_module(rel: str) -> str:
    """`src/mypackage/stands/star_platinum.py` -> `mypackage.stands.star_platinum`.

    The `src/` prefix is a layout convention, not a package name, and stripping
    it is what makes `from mypackage...` resolve in a src-layout repo.
    """
    parts = rel.split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _module_path(root: Path, module: str) -> str:
    """Repo path for a dotted module, or `""` when it is not in this repo."""
    base = module.replace(".", "/")
    for candidate in (
        "src/%s.py" % base,
        "%s.py" % base,
        "src/%s/__init__.py" % base,
        "%s/__init__.py" % base,
    ):
        if (root / candidate).is_file():
            return candidate
    return ""


def _python_modules(rel: str, tree: ast.AST) -> list[list[str]]:
    """Every import as the dotted modules to try, in order.

    `from pkg.mod import name` is `pkg.mod.name` when that is a module and
    `pkg.mod` when `name` is defined inside it, so it carries both. Which one
    exists is a question about the tree, answered by `resolve_python`.
    """
    found: dict[tuple, None] = {}
    module = _python_module(rel)
    package = module.rsplit(".", 1)[0] if module else ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found[(alias.name,)] = None
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                keep = max(0, len(parts) - (node.level - 1))
                base = ".".join(parts[:keep] + ([base] if base else []))
            if not base:
                continue
            for alias in node.names:
                found[(base + "." + alias.name, base)] = None
    return [list(candidates) for candidates in found]


def resolve_python(root: Path, unit: Unit) -> Unit:
    """Intra-repo imports, as repo paths, against the tree as it is now.

    External packages simply fail to resolve and are dropped, which is the
    point: the graph should show edges it can follow.
    """
    loads = set()
    for candidates in unit.fields.get("modules") or []:
        for module in candidates:
            target = _module_path(root, module)
            if target:
                loads.add(target)
                break
    return replace(unit, fields={**unit.fields, "loads": sorted(loads)})


def _first_line(node: ast.AST) -> int:
    """Where a definition starts: its first decorator, not its `def`.

    `lineno` points at `def` or `class`, so a read slice anchored there would
    cut off the decorators that change what the code does.
    """
    return min([decorator.lineno for decorator in node.decorator_list] + [node.lineno])


def _dotted(node: ast.AST) -> str:
    """`nn.Module` from an attribute chain, `Generic` from `Generic[T]`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted(node.value)
        return owner + "." + node.attr if owner else ""
    if isinstance(node, ast.Subscript):
        return _dotted(node.value)
    return ""


def _python_base(node: ast.ClassDef) -> str:
    """The first base class as written. `object` is not a parent worth naming."""
    for base in node.bases:
        name = _dotted(base)
        if name and name != "object":
            return name
    return ""


def _python_symbols(tree: ast.AST) -> list[dict]:
    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append({"kind": "class", "name": node.name, "line": _first_line(node)})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({"kind": "function", "name": node.name, "line": _first_line(node)})
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    symbols.append({"kind": "const", "name": target.id, "line": node.lineno})
    return sorted(symbols, key=lambda symbol: (symbol["line"], symbol["name"]))


def parse_python(root: Path, path: Path, text: str | None = None) -> Unit:
    return resolve_python(root, read_python(root, path, text))


def read_python(root: Path, path: Path, text: str | None = None) -> Unit:
    """What one Python file says, without asking whether its imports exist."""
    text = read_text(path) if text is None else text
    rel = path.relative_to(root).as_posix()
    doc = ""
    fields = {"class_name": "", "extends": "", "description": doc, "language": "python", "modules": []}
    symbols = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    if tree is not None:
        doc = " ".join((ast.get_docstring(tree, clean=True) or "").split())[:170]
        fields["description"] = doc
        fields["modules"] = _python_modules(rel, tree)
        symbols = _python_symbols(tree)
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        if classes:
            fields["class_name"] = classes[0].name
            fields["extends"] = _python_base(classes[0])
    else:
        # A file that does not parse still gets a name and a place in the map.
        match = PY_CLASS_RE.search(text)
        if match:
            fields["class_name"] = match.group(1)
        for regex, kind in ((PY_DEF_RE, "function"), (PY_CLASS_RE, "class"), (PY_CONST_RE, "const")):
            for match in regex.finditer(text):
                symbols.append({"kind": kind, "name": match.group(1), "line": _line(text, match.start())})
    return Unit(
        path=rel,
        kind="test" if "/tests/" in rel or rel.startswith("tests/") or path.name.startswith("test_") else "source",
        fields=fields,
        symbols=symbols,
        text=text,
    )


def _blank(match: re.Match) -> str:
    found = match.group(0)
    if "\n" not in found:
        return " " * len(found)
    return re.sub(r"[^\n]", " ", found)


def java_code(text: str) -> str:
    """`text` with comments and literals blanked; offsets and newlines unchanged."""
    return JAVA_NOISE_RE.sub(_blank, text)


def _line_numbers(text: str):
    """Offset -> line, without rescanning a large class for every symbol."""
    starts = [0] + [match.end() for match in re.finditer("\n", text)]
    return lambda offset: bisect.bisect_right(starts, offset)


def _java_names(text: str) -> list[str]:
    """`A, b.C` -> [`A`, `b.C`]. Anything that is not a plain type name is dropped."""
    names = []
    for part in text.split(","):
        words = part.split()
        if words and re.fullmatch(r"[\w$.]+", words[-1]):
            names.append(words[-1])
    return names


def _java_supertypes(rest: str) -> tuple[str, list[str]]:
    """`extends` and `implements` as written, without generics or record components.

    A class extends one type. An interface may extend several; the rest sit
    beside the implemented interfaces.
    """
    while True:
        stripped = JAVA_GENERIC_RE.sub("", rest)
        if stripped == rest:
            break
        rest = stripped
    rest = re.sub(r"\([^()]*\)", " ", rest)
    match = JAVA_EXTENDS_RE.search(rest)
    extended = _java_names(match.group(1)) if match else []
    match = JAVA_IMPLEMENTS_RE.search(rest)
    implemented = _java_names(match.group(1)) if match else []
    return (extended[0] if extended else ""), extended[1:] + implemented


def _java_doc(text: str, start: int) -> str:
    """The prose of the Javadoc that ends right before `start`, if there is one."""
    doc = None
    for match in JAVADOC_RE.finditer(text, 0, start):
        doc = match if not text[match.end():start].strip() else None
    if doc is None:
        return ""
    lines = []
    for line in doc.group(1).splitlines():
        line = line.strip().lstrip("*").strip()
        if line.startswith("@"):
            break  # block tags (`@author`, `@param`) end the description
        lines.append(line)
    prose = re.sub(r"\{@\w+\s*([^}]*)\}", r"\1", " ".join(lines))
    prose = re.sub(r"<[^>]+>", " ", prose)
    return " ".join(prose.split())[:170]


def parse_java(root: Path, path: Path, text: str | None = None) -> Unit:
    raw = read_text(path) if text is None else text
    rel = path.relative_to(root).as_posix()
    # `\r\n` is one break to `splitlines()` too, so lines counted on the
    # normalised copy still match the slices the kernel cuts from `raw`.
    text = raw.replace("\r\n", "\n")
    code = java_code(text)
    line_of = _line_numbers(code)

    match = JAVA_PACKAGE_RE.search(code)
    package = match.group(1) if match else ""
    imports = set()
    for static, name, wildcard in JAVA_IMPORT_RE.findall(code):
        if static:
            # `import static a.C.member` and `import static a.C.*` both depend on `a.C`.
            imports.add(name if wildcard else name.rsplit(".", 1)[0])
        else:
            imports.add(name + ".*" if wildcard else name)

    types = list(JAVA_TYPE_RE.finditer(code))
    type_names = {match.group("name") for match in types}
    symbols = [
        {"kind": "class", "name": match.group("name"), "line": line_of(match.start())}
        for match in types
    ]
    for match in JAVA_METHOD_RE.finditer(code):
        name, returns = match.group("name"), match.group("type")
        if name in JAVA_STATEMENT_WORDS or returns in JAVA_STATEMENT_WORDS:
            continue
        if returns is None and name not in type_names:
            continue  # no return type and not a constructor: a call, not a declaration
        if not JAVA_DECLARATION_TAIL_RE.match(code, match.end() - 1):
            continue
        symbols.append({"kind": "function", "name": name, "line": line_of(match.start())})
    for match in JAVA_CONST_NAME_RE.finditer(code):
        start = code.rfind("\n", 0, match.start()) + 1
        head = JAVA_CONST_HEAD_RE.fullmatch(code, start, match.start())
        if head and head.group("type") not in JAVA_STATEMENT_WORDS:
            symbols.append({"kind": "const", "name": match.group(0), "line": line_of(start)})

    # The public class must match the file name; anything else is a guess.
    primary = next((match for match in types if match.group("name") == path.stem), types[0] if types else None)
    extends, implements = _java_supertypes(primary.group("rest")) if primary else ("", [])
    fields = {
        "class_name": primary.group("name") if primary else "",
        "extends": extends,
        "implements": implements,
        "package": package,
        "imports": sorted(imports),
        "description": _java_doc(text, primary.start()) if primary else "",
        "language": "java",
    }
    marked = "/" + rel
    test = "/src/test/" in marked or "/tests/" in marked or path.stem.endswith(("Test", "Tests"))
    return Unit(
        path=rel,
        kind="test" if test else "source",
        fields=fields,
        symbols=sorted(symbols, key=lambda symbol: (symbol["line"], symbol["name"])),
        text=raw,
    )


def _link_targets(text: str) -> list[str]:
    """Relative markdown link targets as written: no anchors, no external URLs."""
    found = set()
    for target in MD_LINK_RE.findall(text):
        target = target.split("#", 1)[0].strip().strip("<>")
        if not target or "://" in target or target.startswith(("mailto:", "#")):
            continue
        found.add(target)
    return sorted(found)


def _resolve_links(root: Path, base: Path, targets) -> set[str]:
    """The targets that are markdown files in this repo, as repo paths."""
    found = set()
    top = root.resolve()
    for target in targets:
        candidate = base / target
        try:
            candidate = candidate.resolve()
            rel = candidate.relative_to(top).as_posix()
        except (OSError, ValueError):
            continue
        if candidate.is_file() and rel.endswith(".md"):
            found.add(rel)
    return found


def markdown_links(root: Path, path: Path, text: str) -> list[str]:
    """Wikilinks plus relative markdown links, as repo paths or raw links.

    Repos that are not Obsidian vaults link with `[text](path.md)`. Treating
    those as edges too means the doc graph works in either convention.
    """
    found = set(WIKILINK_RE.findall(text))
    return sorted(found | _resolve_links(root, path.parent, _link_targets(text)))


def resolve_markdown(root: Path, unit: Unit) -> Unit:
    """Add the relative links that point at a markdown file that exists now."""
    base = (root / unit.path).parent
    resolved = _resolve_links(root, base, unit.fields.get("link_targets") or [])
    links = sorted(set(unit.fields.get("links") or []) | resolved)
    return replace(unit, fields={**unit.fields, "links": links})


def parse_markdown(root: Path, path: Path, text: str | None = None) -> Unit:
    return resolve_markdown(root, read_markdown(root, path, text))


def read_markdown(root: Path, path: Path, text: str | None = None) -> Unit:
    """What one note says. Relative links stay as written until `resolve_markdown`."""
    text = read_text(path) if text is None else text
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
            "link_targets": _link_targets(text),
        },
        symbols=symbols,
        text=text,
    )


def parse_generic(root: Path, path: Path) -> Unit:
    """Path-only unit. Cheap: lets a query match a filename without reading it."""
    return Unit(path=path.relative_to(root).as_posix(), kind="file", text="")


# Suffix -> reader. A reader sees one file's text and nothing else, which is
# what makes its output safe to cache by content.
PARSERS = {
    ".gd": parse_gdscript,
    ".tscn": parse_godot_scene,
    ".tres": parse_godot_scene,
    ".py": read_python,
    ".java": parse_java,
    ".md": read_markdown,
}

# Language -> the step that turns what a reader found into repo paths. It asks
# which other files exist, so it runs on every pass and is never cached.
RESOLVERS = {
    "python": resolve_python,
    "markdown": resolve_markdown,
}


def resolve(root: Path, unit: Unit) -> Unit:
    resolver = RESOLVERS.get(unit.fields.get("language"))
    return resolver(root, unit) if resolver else unit


def collect(root: Path, skip_dirs) -> list[Unit]:
    """Parse every file with a registered parser. Unparsed files are handled by the adapter."""
    units = []
    for path in walk_files(root, skip_dirs):
        reader = PARSERS.get(path.suffix.lower())
        if reader is None:
            continue
        units.append(resolve(root, reader(root, path)))
    return units
