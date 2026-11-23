"""Facts a repository of a known shape has, read without an adapter of its own.

The generic adapter's `facts()` is this module. It reads five shapes:

- `route`: an HTTP endpoint a Spring controller declares, with the handler,
  the other annotations on it (where a guard usually is) and the collaborators
  the handler calls.
- `setting`: a configuration key, with its value in each file that sets it
  and each place the code reads it.
- `table`: a database table a JPA entity maps, with the files that name the
  entity and the lines that name the table as text.
- `job`: a scheduled method.
- `call`: an HTTP request a TypeScript or JavaScript client makes, with the
  files that call the method making it.

Everything is read with regular expressions from the walked files: no build,
no compiler, no dependency. So a path built at run time, a constant used as a
path and a call through a variable are written as they appear, not resolved.
"""
from __future__ import annotations

import re
from pathlib import Path

from .parsers import read_text

# Bumped when what this module extracts changes, so a cached set is rebuilt.
VERSION = "1"

MAX_PLACES = 60
MAX_VALUE = 80
MAX_FILE_BYTES = 1024 * 1024

NOTES = {
    "route": [
        "Read from the mapping annotations: a path built from a constant is shown as written, "
        "and a filter or interceptor that guards a whole prefix is not on the route.",
        "Calls are the handler's own calls on the fields of its class, one step deep: a call "
        "made through a base class or a local variable is not listed.",
    ],
    "setting": [
        "A value is what a file in the repository sets; the running system may override it "
        "from outside. Values of keys that look like secrets are hidden.",
    ],
    "table": [
        "Named as data lists lines that contain the table's name as a word, wherever they are.",
    ],
    "call": [
        "The path is the literal in the request, without the base URL; `{name}` stands for a "
        "value put in at run time.",
    ],
}

JAVA_COMMENT_OR_STRING_RE = re.compile(
    r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?\*/', re.S
)
JAVA_STRING_RE = re.compile(r'"((?:\\.|[^"\\\n])*)"')
JAVA_ANNOTATION_RE = re.compile(r"@([A-Z][\w.]*)")
JAVA_TYPE_DECL_RE = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)")
JAVA_IMPLEMENTS_RE = re.compile(
    r"\bclass\s+([A-Za-z_$][\w$]*)[^{;]*?\bimplements\s+([^{]+)\{"
)
# A field of the instance: a static one is a constant or a logger, not a collaborator.
JAVA_FIELD_RE = re.compile(
    r"^[ \t]+(?:(?:private|protected|public|final|transient|volatile)\s+)*"
    r"([A-Z][\w$]*)(?:<[^;=(){}]*>)?\s+([a-z_][\w$]*)\s*[;=]",
    re.M,
)
JAVA_LOGGER_RE = re.compile(r"^log(ger)?$", re.I)
JAVA_CALL_RE = re.compile(r"\b(?:this\.)?([a-z_][\w$]*)\s*\.\s*([a-z_][\w$]*)\s*\(")
JAVA_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*")
MAPPING_VERBS = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": "",
}
HTTP_VERB_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b")
# Annotations that say nothing about who may call a route or what it does.
PLAIN_ANNOTATIONS = set(MAPPING_VERBS) | {
    "Override", "ResponseBody", "Controller", "RestController", "Api", "ApiOperation",
    "ApiResponses", "ApiResponse", "Operation", "Tag", "SuppressWarnings", "Autowired",
    "Component", "Service", "Repository", "Slf4j", "RequiredArgsConstructor", "Validated",
}
DESCRIBING_ANNOTATIONS = {"ApiOperation": "value", "Operation": "summary"}

PLACEHOLDER_RE = re.compile(r"\$\{([\w.\-]+)(?::([^}]*))?\}")
GET_PROPERTY_RE = re.compile(r"\bgetProperty\(\s*\"([\w.\-]+)\"")
PROPERTY_LINE_RE = re.compile(r"^\s*([\w.\-\[\]/]+)\s*[=:]\s*(.*)$")
YAML_LINE_RE = re.compile(r"^(\s*)(?:\"([^\"]+)\"|'([^']+)'|([\w.\-/\[\]]+))\s*:(?:\s+(.*)|\s*)$")
SECRET_KEY_RE = re.compile(
    r"pass(word|wd)?\b|pwd|secret|token|credential|key$", re.I
)
BUNDLE_NAME_RE = re.compile(r"^(messages|i18n|locale|lang|labels|translations)|_[a-z]{2}(_[A-Z]{2})?$")
CONFIG_YAML_RE = re.compile(r"^(application|bootstrap)\b")
SETTING_USE_SUFFIXES = {".java", ".kt", ".xml"}
WORD_RE = re.compile(r"\w+")
TABLE_TEXT_SUFFIXES = {".sql", ".xml", ".properties"}

CLIENT_SUFFIXES = {".ts", ".js", ".tsx", ".jsx", ".mjs"}
CLIENT_CALL_RE = re.compile(
    r"\b(?:this\s*\.\s*)?(\w*[hH]ttp\w*|axios)\s*\.\s*(get|post|put|delete|patch)\b"
    r"\s*(?:<[^()]*?>)?\s*\(\s*"
)
CLIENT_LITERAL_RE = re.compile(r"`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"")
CLIENT_METHOD_RE = re.compile(
    r"^[ \t]+(?:(?:public|private|protected|static|async|override)\s+)*"
    r"([A-Za-z_$][\w$]*)\s*(?:<[^>()\n]*>)?\s*\((?:[^;\n]*\{)?[ \t]*$",
    re.M,
)
CLIENT_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")
CLIENT_INTERPOLATION_RE = re.compile(r"\$\{([^}]*)\}")
CLIENT_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "constructor"}
# A method name this short is shared by too many classes to say who calls it.
MIN_CALLER_NAME = 8
MAX_CALLER_FILES = 15


def _place(path: str, line: int, text: str, where: str = "", how: str = "") -> dict:
    return {"path": path, "line": line, "text": " ".join(text.split())[:200], "where": where, "how": how}


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_text(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return text[start:end if end >= 0 else len(text)]


def _clip(value: str) -> str:
    value = value.strip()
    return value if len(value) <= MAX_VALUE else value[: MAX_VALUE - 1] + "…"


def _is_test(path: str) -> bool:
    marked = "/" + path
    return "/src/test/" in marked or "/tests/" in marked or "/test/" in marked


# -- Java -------------------------------------------------------------------

def _blank(match: re.Match, keep_strings: bool) -> str:
    found = match.group(0)
    if keep_strings and found[0] in "\"'":
        return found
    if found[0] in "\"'":
        return found[0] + " " * (len(found) - 2) + found[-1] if len(found) >= 2 else found
    return re.sub(r"[^\n]", " ", found)


class JavaFile:
    """One Java file, with the two copies the scans need.

    `code` has its comments blanked and `bare` its string contents too, both
    without moving an offset: structure is read from `bare`, where a brace or
    an `@` inside a string cannot mislead, and values from `code`.
    """

    def __init__(self, path: str, text: str) -> None:
        self.path = path
        self.stem = Path(path).stem
        self.code = JAVA_COMMENT_OR_STRING_RE.sub(lambda match: _blank(match, True), text)
        self.bare = JAVA_COMMENT_OR_STRING_RE.sub(lambda match: _blank(match, False), text)
        self.types = [(match.start(), match.group(1)) for match in JAVA_TYPE_DECL_RE.finditer(self.bare)]
        self._fields = None

    def type_at(self, offset: int) -> str:
        found = self.stem
        for start, name in self.types:
            if start > offset:
                break
            found = name
        return found

    def closing(self, opening: int) -> int:
        """The offset of the bracket that closes the one at `opening`, or -1."""
        pairs = {"(": ")", "{": "}"}
        open_char = self.bare[opening]
        close_char = pairs[open_char]
        depth = 0
        for offset in range(opening, len(self.bare)):
            char = self.bare[offset]
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return offset
        return -1

    def fields(self) -> dict:
        """Field name -> type, for the fields a handler can call."""
        if self._fields is None:
            self._fields = {
                name: kind for kind, name in JAVA_FIELD_RE.findall(self.bare)
                if not JAVA_LOGGER_RE.match(name)
            }
        return self._fields

    def declarations(self) -> list:
        """Each annotated declaration: its annotations, what it is and its name.

        A declaration is `{kind, name, offset, annotations, body}`, with kind
        `type`, `method` or `field` and each annotation `(name, arguments,
        offset)`. Annotated parameters are left out.
        """
        found, offset, bare = [], 0, self.bare
        while True:
            match = JAVA_ANNOTATION_RE.search(bare, offset)
            if match is None:
                return found
            annotations, cursor = [], match.start()
            while True:
                match = JAVA_ANNOTATION_RE.match(bare, cursor)
                if match is None:
                    break
                end = match.end()
                arguments = ""
                after = end
                while after < len(bare) and bare[after] in " \t":
                    after += 1
                if after < len(bare) and bare[after] == "(":
                    close = self.closing(after)
                    if close < 0:
                        break
                    arguments = self.code[after + 1:close]
                    end = close + 1
                annotations.append((match.group(1).rsplit(".", 1)[-1], arguments, match.start()))
                cursor = end
                while cursor < len(bare) and bare[cursor].isspace():
                    cursor += 1
            if not annotations:
                offset = cursor + 1
                continue
            # A comma ends an annotated parameter, but not inside `Map<A, B>`.
            stop, angles = cursor, 0
            while stop < len(bare) and bare[stop] not in "{;=(" and (angles or bare[stop] not in ",)"):
                angles += {"<": 1, ">": -1}.get(bare[stop], 0)
                stop += 1
            header = bare[cursor:stop]
            offset = max(stop, cursor + 1)
            if stop >= len(bare):
                continue
            declared = JAVA_TYPE_DECL_RE.search(header)
            names = JAVA_IDENTIFIER_RE.findall(header)
            if declared:
                found.append({
                    "kind": "type", "name": declared.group(1), "offset": cursor,
                    "annotations": annotations, "body": (),
                })
            elif bare[stop] == "(" and names:
                close = self.closing(stop)
                body = ()
                if close > 0:
                    brace = close + 1
                    while brace < len(bare) and bare[brace] not in "{;":
                        brace += 1
                    if brace < len(bare) and bare[brace] == "{":
                        end = self.closing(brace)
                        body = (stop, brace, end if end > 0 else brace)
                found.append({
                    "kind": "method", "name": names[-1], "offset": cursor,
                    "annotations": annotations, "body": body,
                })
            elif bare[stop] in ";=" and names:
                found.append({
                    "kind": "field", "name": names[-1], "offset": cursor,
                    "annotations": annotations, "body": (),
                })


def _argument(arguments: str, names) -> str:
    """The value written for one of `names`, or the lone positional value."""
    for name in names:
        match = re.search(r"\b%s\s*=\s*(\{[^}]*\}|\"(?:\\.|[^\"\\])*\"|[\w.$]+)" % name, arguments)
        if match:
            return match.group(1)
    stripped = arguments.strip()
    if stripped and not re.match(r"[A-Za-z_]\w*\s*=", stripped):
        return stripped
    return ""


def _mapping_paths(arguments: str) -> list:
    written = _argument(arguments, ("value", "path"))
    if not written:
        return [""]
    strings = JAVA_STRING_RE.findall(written)
    if strings:
        return strings
    names = re.findall(r"[\w.$]+", written)
    return ["{%s}" % name for name in names] or [""]


def _mapping_verbs(name: str, arguments: str) -> list:
    if MAPPING_VERBS[name]:
        return [MAPPING_VERBS[name]]
    match = re.search(r"\bmethod\s*=\s*(\{[^}]*\}|[\w.$]+)", arguments)
    return HTTP_VERB_RE.findall(match.group(1)) if match else []


def _join(prefix: str, path: str) -> str:
    joined = "/".join(part.strip("/") for part in (prefix, path) if part.strip("/"))
    return "/" + joined


def _annotation_text(name: str, arguments: str) -> str:
    arguments = " ".join(arguments.split())[:240]
    return "@%s(%s)" % (name, arguments) if arguments else "@%s" % name


def _describing(annotations) -> str:
    for name, arguments, _offset in annotations:
        key = DESCRIBING_ANNOTATIONS.get(name)
        if key:
            strings = JAVA_STRING_RE.findall(_argument(arguments, (key,)))
            if strings:
                return strings[0]
    return ""


class JavaTypes:
    """Where each type is declared and which classes implement an interface."""

    def __init__(self, files) -> None:
        self.paths, self.implementers = {}, {}
        for java in files:
            self.paths.setdefault(java.stem, []).append(java.path)
            for name, implemented in JAVA_IMPLEMENTS_RE.findall(java.bare):
                if name != java.stem:
                    continue
                for interface in re.findall(r"[A-Za-z_$][\w$]*", re.sub(r"<[^<>]*>", "", implemented)):
                    self.implementers.setdefault(interface, []).append(java.path)

    def where(self, name: str) -> str:
        """`in <file>`, and the class that implements it when there is just one."""
        paths = self.paths.get(name) or []
        if len(paths) != 1:
            return ""
        implementers = self.implementers.get(name) or []
        if len(implementers) == 1:
            return "in %s, implemented by %s" % (paths[0], implementers[0])
        return "in %s" % paths[0]


def _handler_calls(java: JavaFile, body, types: JavaTypes) -> list:
    if not body:
        return []
    _opening, brace, end = body
    receivers = java.fields()
    found, seen = [], set()
    for match in JAVA_CALL_RE.finditer(java.bare, brace, end):
        kind = receivers.get(match.group(1))
        if kind is None or kind not in types.paths:
            continue
        where = "%s.%s" % (kind, match.group(2))
        if where in seen:
            continue
        seen.add(where)
        found.append(_place(
            java.path, _line_at(java.bare, match.start()), _line_text(java.code, match.start()),
            where, types.where(kind),
        ))
    return found[:MAX_PLACES]


def _routes(java: JavaFile, declarations, types: JavaTypes) -> list:
    prefixes, guards = {}, {}
    for declaration in declarations:
        if declaration["kind"] != "type":
            continue
        for name, arguments, _offset in declaration["annotations"]:
            if name == "RequestMapping":
                prefixes[declaration["name"]] = _mapping_paths(arguments)
        guards[declaration["name"]] = [
            _annotation_text(name, arguments)
            for name, arguments, _offset in declaration["annotations"]
            if name not in PLAIN_ANNOTATIONS
        ]
    found = []
    for declaration in declarations:
        if declaration["kind"] != "method":
            continue
        mappings = [item for item in declaration["annotations"] if item[0] in MAPPING_VERBS]
        if not mappings:
            continue
        owner = java.type_at(declaration["offset"])
        where = "%s.%s" % (owner, declaration["name"])
        tags = [
            _annotation_text(name, arguments)
            for name, arguments, _offset in declaration["annotations"]
            if name not in PLAIN_ANNOTATIONS
        ] + guards.get(owner, [])
        described = _describing(declaration["annotations"])
        calls = _handler_calls(java, declaration["body"], types)
        for name, arguments, offset in mappings:
            verbs = _mapping_verbs(name, arguments) or ["ANY"]
            for prefix in prefixes.get(owner) or [""]:
                for path in _mapping_paths(arguments):
                    for verb in verbs:
                        route = "%s %s" % (verb, _join(prefix, path))
                        found.append({
                            "id": "route:%s" % route,
                            "kind": "route",
                            "name": route,
                            "summary": "%s%s" % (where, ": %s" % described if described else ""),
                            "tags": tags,
                            "declared_at": _place(
                                java.path, _line_at(java.bare, offset), _line_text(java.code, offset),
                                where, "handles it",
                            ),
                            "calls": calls,
                        })
    return found


def _jobs(java: JavaFile, declarations) -> list:
    found = []
    for declaration in declarations:
        if declaration["kind"] != "method":
            continue
        for name, arguments, offset in declaration["annotations"]:
            if name != "Scheduled":
                continue
            where = "%s.%s" % (java.type_at(declaration["offset"]), declaration["name"])
            found.append({
                "id": "job:%s" % where,
                "kind": "job",
                "name": where,
                "summary": "runs on %s" % (" ".join(arguments.split()) or "a schedule"),
                "declared_at": _place(
                    java.path, _line_at(java.bare, offset), _line_text(java.code, offset), where, "scheduled",
                ),
            })
    return found


def _tables(java: JavaFile, declarations) -> list:
    found = []
    for declaration in declarations:
        if declaration["kind"] != "type":
            continue
        for name, arguments, offset in declaration["annotations"]:
            if name != "Table":
                continue
            strings = JAVA_STRING_RE.findall(_argument(arguments, ("name",)))
            if not strings or not strings[0].strip():
                continue
            table = strings[0].strip()
            schema = JAVA_STRING_RE.findall(_argument(arguments, ("schema",))) if "schema" in arguments else []
            found.append({
                "id": "table:%s" % table,
                "kind": "table",
                "name": table,
                "summary": "mapped by the entity %s" % declaration["name"],
                "tags": ["schema %s" % schema[0]] if schema else [],
                "declared_at": _place(
                    java.path, _line_at(java.bare, offset), _line_text(java.code, offset),
                    declaration["name"], "maps the table",
                ),
                "used_at": [],
                "mentioned_at": [],
                "entity": declaration["name"],
            })
    return found


def _link_tables(tables: list, javas: list, texts: dict) -> None:
    """Fill in who names each table's entity and which lines name the table."""
    if not tables:
        return
    by_entity, by_name, totals = {}, {}, {}
    for table in tables:
        by_entity.setdefault(table["entity"], []).append(table)
        if len(table["name"]) >= 3:
            by_name.setdefault(table["name"].lower(), []).append(table)

    def add(table: dict, key: str, place: dict) -> None:
        totals[(table["id"], key)] = totals.get((table["id"], key), 0) + 1
        if len(table[key]) < MAX_PLACES:
            table[key].append(place)

    for java in javas:
        named = set()
        for match in JAVA_IDENTIFIER_RE.finditer(java.bare):
            word = match.group(0)
            if word in by_entity and word not in named and word != java.stem:
                named.add(word)
                for table in by_entity[word]:
                    if table["declared_at"]["path"] != java.path:
                        add(table, "used_at", _place(
                            java.path, _line_at(java.bare, match.start()),
                            _line_text(java.code, match.start()), java.stem, "names the entity",
                        ))
        for match in JAVA_STRING_RE.finditer(java.code):
            for word in WORD_RE.finditer(match.group(1)):
                for table in by_name.get(word.group(0).lower(), ()):
                    if table["declared_at"]["path"] == java.path:
                        continue
                    add(table, "mentioned_at", _place(
                        java.path, _line_at(java.code, match.start()),
                        _line_text(java.code, match.start()), java.stem, "names the table in a string",
                    ))
    for path, text in texts.items():
        for number, line in enumerate(text.splitlines(), 1):
            for word in set(found.lower() for found in WORD_RE.findall(line)):
                for table in by_name.get(word, ()):
                    add(table, "mentioned_at", _place(path, number, line, Path(path).name, "names the table"))
    for table in tables:
        used = totals.get((table["id"], "used_at"), 0)
        named = totals.get((table["id"], "mentioned_at"), 0)
        table["summary"] += "; %d file(s) name the entity, %d line(s) name the table" % (used, named)
        del table["entity"]


# -- settings ---------------------------------------------------------------

def _properties(text: str) -> list:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip()[0] in "#!":
            continue
        match = PROPERTY_LINE_RE.match(line)
        if match:
            found.append((match.group(1), match.group(2).strip(), number))
    return found


def _yaml(text: str) -> list:
    """The leaf keys of a YAML mapping, dotted, with the line each is on.

    Lists and block scalars are skipped: a setting is a key with one value.
    """
    found, stack, skip_below = [], [], None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            stack, skip_below = [], None
            continue
        indent = len(line) - len(line.lstrip(" "))
        if skip_below is not None:
            if indent > skip_below:
                continue
            skip_below = None
        match = YAML_LINE_RE.match(line)
        if match is None or stripped.startswith("- "):
            continue
        key = match.group(2) or match.group(3) or match.group(4)
        value = (match.group(5) or "").split(" #")[0].strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if value[:1] in ("|", ">"):
            skip_below = indent
            continue
        if not value:
            stack.append((indent, key))
            continue
        if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        found.append((".".join([part for _indent, part in stack] + [key]), value, number))
    return found


def _is_bundle(path: str, entries: list, used) -> bool:
    """Whether a properties file holds texts for people rather than settings.

    By its name, or by values that are mostly sentences while the code reads
    few of the keys: a file of long values the code does read (queries, say)
    is configuration.
    """
    if BUNDLE_NAME_RE.search(Path(path).stem):
        return True
    if len(entries) < 20:
        return False
    prose = sum(1 for _key, value, _line in entries if value.count(" ") >= 2)
    read = sum(1 for key, _value, _line in entries if key in used)
    return prose * 2 > len(entries) and read * 5 < len(entries)


def _setting_uses(path: str, text: str, where: str) -> list:
    found = []
    for match in PLACEHOLDER_RE.finditer(text):
        default = match.group(2)
        how = "read as a placeholder" + (", default `%s`" % _clip(default) if default is not None else "")
        found.append((match.group(1), _place(path, _line_at(text, match.start()), _line_text(text, match.start()), where, how)))
    for match in GET_PROPERTY_RE.finditer(text):
        found.append((match.group(1), _place(
            path, _line_at(text, match.start()), _line_text(text, match.start()), where, "read with getProperty",
        )))
    return found


def _settings(declared: dict, uses: list) -> list:
    """`declared` is path -> [(key, value, line)]; `uses` is [(key, place)]."""
    used = {}
    for key, place in uses:
        used.setdefault(key, []).append(place)
    # A YAML file is configuration when it is named like one or the code reads a key of
    # it; a properties file is, unless it holds texts for people.
    kept = {}
    for path, entries in declared.items():
        suffix = Path(path).suffix.lower()
        if suffix == ".properties":
            if _is_bundle(path, entries, used):
                continue
        elif not CONFIG_YAML_RE.match(Path(path).name) and not any(
            key in used for key, _value, _line in entries
        ):
            continue
        kept[path] = entries
    places = {}
    for path in sorted(kept, key=lambda item: (_is_test(item), item)):
        for key, value, line in kept[path]:
            shown = "(hidden)" if SECRET_KEY_RE.search(key) and value else _clip(value)
            places.setdefault(key, []).append((shown, _place(path, line, "%s = %s" % (key, shown), Path(path).name, "sets it")))
    found = []
    for key in sorted(set(places) | set(used)):
        if key not in places and key not in used:
            continue
        set_at = [place for _value, place in places.get(key, [])]
        values = {}
        for value, place in places.get(key, []):
            values.setdefault(value, []).append(Path(place["path"]).name)
        if values:
            shown = "; ".join(
                "`%s` in %s" % (value, ", ".join(names[:3]) + (" and %d more" % (len(names) - 3) if len(names) > 3 else ""))
                for value, names in list(values.items())[:4]
            )
            if len(values) > 4:
                shown += "; %d more value(s)" % (len(values) - 4)
        else:
            shown = "not set in the repository"
        readers = used.get(key, [])
        fact = {
            "id": "setting:%s" % key,
            "kind": "setting",
            "name": key,
            "summary": "%s; read in %d place(s)" % (shown, len(readers)),
            "set_at": set_at[:MAX_PLACES],
            "used_at": readers[:MAX_PLACES],
        }
        found.append(fact)
    return found


# -- client calls -----------------------------------------------------------

def _client_literal(literal: str, names: dict):
    """The path a string or template literal stands for; None when it is a mock.

    `${base}/v1/stands/${id}?x=1` in backticks is `/v1/stands/{id}`: a leading
    interpolation is the base URL, or a field of the file that holds a path.
    """
    body, prefix = literal[1:-1], ""
    if literal[0] == "`":
        base = re.match(r"\$\{([^}]*)\}", body)
        if base:
            if "mock" in base.group(1).lower():
                return None
            name = (JAVA_IDENTIFIER_RE.findall(base.group(1)) or [""])[-1]
            prefix = names.get(name, "")
            body = body[base.end():]
        body = CLIENT_INTERPOLATION_RE.sub(
            lambda match: "{%s}" % (JAVA_IDENTIFIER_RE.findall(match.group(1)) or ["value"])[-1], body
        )
    if body.startswith("assets/") or "/mock" in body.lower():
        return None
    body = re.sub(r"^https?://[^/]+", "", body.split("?")[0])
    return prefix + body


def _client_expression(expression: str, names: dict) -> list:
    """The paths an expression can stand for: one, or one per branch of a choice."""
    parts, cursor, branches = [], 0, [[]]
    for match in CLIENT_LITERAL_RE.finditer(expression):
        parts.append(("code", expression[cursor:match.start()]))
        parts.append(("literal", match.group(0)))
        cursor = match.end()
    parts.append(("code", expression[cursor:]))
    for kind, text in parts:
        if kind == "literal":
            branches[-1].append(_client_literal(text, names))
            continue
        for token in re.findall(r"[?:]|[A-Za-z_$][\w$.]*", text):
            if token in "?:":
                branches.append([])
                continue
            name = token.rsplit(".", 1)[-1]
            if name in names:
                branches[-1].append(names[name])
    found = []
    for branch in branches:
        if not branch or None in branch:
            continue
        path = "".join(branch)
        if "/" in path:
            found.append(path)
    return found


def _client_names(text: str) -> dict:
    """Fields and constants of the file that hold a path, by name."""
    names = {}
    for match in re.finditer(
        r"^(?:(?:export\s+)?(?:const|let|var)\s+|[ \t]+(?:(?:private|public|protected|static|readonly)\s+)+)"
        r"([A-Za-z_$][\w$]*)\s*(?::\s*[\w<>\[\]]+)?\s*=\s*([^;\n]*)",
        text, re.M,
    ):
        if "?" in match.group(2):
            continue
        paths = _client_expression(match.group(2), names)
        if paths and CLIENT_LITERAL_RE.search(match.group(2)):
            names[match.group(1)] = paths[0]
    return names


def _client_urls(text: str, start: int, names: dict, method_start: int) -> list:
    """The paths of the request whose arguments start at `start`.

    More than one when the method chooses between them before it asks.
    """
    depth, cursor = 0, start
    while cursor < len(text) and cursor < start + 600:
        literal = CLIENT_LITERAL_RE.match(text, cursor)
        if literal:
            cursor = literal.end()
            continue
        char = text[cursor]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            break
        cursor += 1
    argument = text[start:cursor].strip()
    paths = []
    if re.fullmatch(r"[A-Za-z_$][\w$]*", argument):
        # The URL is a local variable: take every value the method gives it.
        window = text[max(method_start, start - 4000):start]
        for assigned in re.finditer(r"\b%s\s*(?::\s*\w+\s*)?=(?!=)\s*([^;]*)" % re.escape(argument), window):
            paths.extend(_client_expression(assigned.group(1)[:600], names))
    paths = paths or _client_expression(argument, names)
    paths = [path for path in paths if path.startswith("/")] or paths
    real = [path for path in paths if not path.endswith(".json")] or paths
    return list(dict.fromkeys(real))


def _client_calls(path: str, text: str) -> list:
    found = []
    classes = [(match.start(), match.group(1)) for match in CLIENT_CLASS_RE.finditer(text)]
    methods = [
        (match.start(1), match.group(1), len(match.group(0)) - len(match.group(0).lstrip()))
        for match in CLIENT_METHOD_RE.finditer(text)
        if match.group(1) not in CLIENT_KEYWORDS
    ]
    # A class's methods sit at one depth; a deeper match is a call that opens a block.
    depth = min((indent for _start, _name, indent in methods), default=0)
    methods = [(start, name) for start, name, indent in methods if indent == depth]
    names = _client_names(text) if CLIENT_CALL_RE.search(text) else {}
    for match in CLIENT_CALL_RE.finditer(text):
        owner = next((name for start, name in reversed(classes) if start < match.start()), Path(path).stem)
        method_start, method = next(
            ((start, name) for start, name in reversed(methods) if start < match.start()), (0, "")
        )
        where = "%s.%s" % (owner, method) if method else owner
        for url in _client_urls(text, match.end(), names, method_start):
            route = "%s %s" % (match.group(2).upper(), url if url.startswith("/") else "/" + url)
            found.append({
                "id": "call:%s" % route,
                "kind": "call",
                "name": route,
                "summary": "requested by %s" % where,
                "declared_at": _place(
                    path, _line_at(text, match.start()), _line_text(text, match.start()), where,
                    "makes the request",
                ),
                "used_at": [],
                "method": method,
            })
    return found


def _link_callers(calls: list, texts: dict) -> None:
    """Fill in the files that call the method making each request."""
    wanted = {}
    for call in calls:
        method = call.pop("method")
        if len(method) >= MIN_CALLER_NAME:
            wanted.setdefault(method, []).append(call)
    if not wanted:
        return
    found = {}
    for path, text in texts.items():
        for match in re.finditer(r"\.\s*([A-Za-z_$][\w$]*)\s*\(", text):
            name = match.group(1)
            if name in wanted and (name, path) not in found:
                found[(name, path)] = _place(
                    path, _line_at(text, match.start()), _line_text(text, match.start()),
                    Path(path).stem, "calls %s" % name,
                )
    by_method = {}
    for (name, _path), place in sorted(found.items()):
        by_method.setdefault(name, []).append(place)
    for name, places in by_method.items():
        for call in wanted[name]:
            callers = [place for place in places if place["path"] != call["declared_at"]["path"]]
            if 0 < len(callers) <= MAX_CALLER_FILES:
                call["used_at"] = callers
                call["summary"] += "; called from %s" % ", ".join(
                    sorted({place["where"] for place in callers})[:4]
                )


# -- the set ----------------------------------------------------------------

def _unique(facts: list) -> list:
    """Two facts may share an id (one route in two classes); number the later ones."""
    seen, found, same = {}, [], set()
    for fact in facts:
        declared = fact.get("declared_at") or {}
        key = (fact["id"], declared.get("path"), declared.get("where"))
        if declared and key in same:
            continue  # one method asking for the same thing twice
        same.add(key)
        count = seen.get(fact["id"], 0) + 1
        seen[fact["id"]] = count
        if count > 1:
            fact = dict(fact, id="%s #%d" % (fact["id"], count))
        found.append(fact)
    return found


def facts(root: Path, files) -> list:
    """Every fact of a known shape in `files`, paths relative to `root`."""
    root = Path(root)
    javas, declared, uses, found, tables, calls = [], {}, [], [], [], []
    table_texts, client_texts = {}, {}
    for relative in files:
        path = Path(relative).as_posix()
        suffix = Path(path).suffix.lower()
        if suffix not in SETTING_USE_SUFFIXES | TABLE_TEXT_SUFFIXES | CLIENT_SUFFIXES | {".yml", ".yaml"}:
            continue
        target = root / path
        try:
            if target.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        text = read_text(target)
        if suffix == ".java":
            java = JavaFile(path, text)
            javas.append(java)
            if not _is_test(path):
                uses.extend(_setting_uses(path, java.code, java.stem))
        elif suffix in (".kt", ".xml"):
            # A build file's placeholders are the build's own properties, not settings.
            if not _is_test(path) and Path(path).name != "pom.xml":
                uses.extend(_setting_uses(path, text, Path(path).name))
            if suffix == ".xml":
                table_texts[path] = text
        elif suffix == ".properties":
            declared[path] = _properties(text)
            table_texts[path] = text
        elif suffix in (".yml", ".yaml"):
            declared[path] = _yaml(text)
        elif suffix == ".sql":
            table_texts[path] = text
        elif suffix in CLIENT_SUFFIXES:
            if ".spec." in path or ".test." in path or _is_test(path):
                continue
            client_texts[path] = text
            calls.extend(_client_calls(path, text))
    types = JavaTypes(javas)
    for java in javas:
        if "@" not in java.bare or _is_test(java.path):
            continue
        if not any(mark in java.bare for mark in ("Mapping", "@Table", "@Scheduled")):
            continue
        declarations = java.declarations()
        found.extend(_routes(java, declarations, types))
        found.extend(_jobs(java, declarations))
        tables.extend(_tables(java, declarations))
    _link_tables(tables, javas, table_texts)
    _link_callers(calls, client_texts)
    found.extend(tables)
    found.extend(_settings(declared, uses))
    found.extend(calls)
    return _unique(found)


def notes(found: list) -> dict:
    kinds = {fact.get("kind") for fact in found}
    return {kind: lines for kind, lines in NOTES.items() if kind in kinds}
