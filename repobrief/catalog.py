"""The scripts a repo ships, grouped by what they do.

A mature repo has dozens of scripts. Exposing each as its own tool would drown
the orchestrator and expose everything a script can do. Instead every script is
placed in one group, and each group is exposed as one tool:

    brief    repo-brief itself (context, graph, overview)
    inspect  scripts that only read and report
    dev      build, test, run, benchmark, train
    debug    look inside a running game or model
    manual   writes on its own, publishes, network, secrets, or unclear; never run

A script is placed by reading it, never by running it. For Python, what it
imports and calls decides; words in its name and docstring can only make a guess
safer, or choose `debug` over a group of the same reach. A shell or make target
cannot be read that way, so its name decides, and anything unclear is `manual`.
SPEC.md ("Tool groups") states the rules this file implements.

`.repo-brief/tools.json` keeps two halves: `detected`, rewritten by every scan,
and your choices (`folders` and `choices`), which a scan never touches.
"""
from __future__ import annotations

import ast
import builtins
import json
import re
from pathlib import Path

STATE_FILE = "tools.json"
GROUPS = ("brief", "inspect", "dev", "debug", "manual")
# Where a repo's scripts live, unless tools.json names other folders. A
# local-only repo adds `.repo-brief/tools/`, the one script folder that is never
# committed.
SCRIPT_FOLDERS = ("tools", "scripts")
LOCAL_FOLDER = ".repo-brief/tools"
DEVSH_PATHS = ("tools/dev.sh", "dev.sh")
MAKEFILES = ("Makefile", "makefile")
# Who may use each group. These are rules, not settings: no flag can expose
# `manual` to anyone, or the other script groups in an untrusted repo.
ORCHESTRATOR = {"brief": "always", "inspect": "trusted", "dev": "trusted", "debug": "trusted", "manual": "never"}
ABOUT = (
    "Written by repo-brief. `detected` is rewritten by every scan. `folders` and "
    "`choices` are yours, and a scan keeps them: `repo-brief tools --set ID GROUP`."
)

MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_-]+):.*?##\s*(.*)$", re.M)
DEVSH_TARGET_RE = re.compile(r"^\s{2,}([a-z][a-z0-9_-]*)\)\s*$", re.M)
WORD_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")
MODE_RE = re.compile(r"^[rwxabt+U]{1,4}$")

# Word stems, matched against the start of each word in a name or docstring.
MANUAL_STEMS = (
    "generat", "publish", "releas", "upload", "deploy", "push", "refresh",
    "install", "login", "secret", "credential",
)
DEBUG_STEMS = ("debug", "diagnos", "trace", "profil", "watch")
SHELL_DEV_STEMS = ("test", "build", "run", "export", "bench", "check", "lint")

# Clients that reach other machines. A bare `socket` is not on the list: in a
# tools folder it is almost always the local channel to a game or process the
# script itself started.
NETWORK_MODULES = (
    "requests", "urllib.request", "urllib3", "http.client", "httpx", "aiohttp",
    "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "xmlrpc", "paramiko",
    "boto3", "botocore", "websocket", "websockets", "grpc", "googleapiclient",
    "github", "selenium", "playwright",
)
# A local server runs until stopped: `dev`, never a read-only `inspect`.
SERVER_MODULES = ("http.server", "socketserver", "uvicorn", "flask", "fastapi", "tornado")
NETWORK_CALLS = {"urlopen", "urlretrieve"}
SECRET_MODULES = ("keyring", "getpass")
SECRET_NAMES = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY", "CREDENTIAL", "AUTH")
PUBLISH_PHRASES = (
    "git push", "gh release", "gh secret", "gh workflow run", "butler push",
    "steamcmd", "twine upload", "npm publish", "docker push", "scp ", "rsync ",
)
SUBPROCESS_MODULES = ("subprocess", "pexpect", "plumbum")
OS_SUBPROCESS = {"system", "popen", "spawnl", "spawnlp", "spawnv", "spawnvp", "execv", "execvp", "execl", "execlp", "startfile"}
TRAINING_MODULES = (
    "torch", "tensorflow", "keras", "jax", "flax", "stable_baselines3", "sb3_contrib",
    "ray", "lightning", "pytorch_lightning", "xgboost", "lightgbm",
)
OS_WRITES = {"remove", "unlink", "rename", "renames", "replace", "makedirs", "mkdir", "rmdir", "removedirs", "symlink", "link", "truncate"}
SHUTIL_WRITES = {"copy", "copy2", "copyfile", "copytree", "move", "rmtree", "make_archive", "unpack_archive"}
# Methods specific enough to mean a write whatever their receiver is.
WRITE_METHODS = {
    "write_text", "write_bytes", "mkdir", "touch", "unlink", "rmdir", "symlink_to",
    "hardlink_to", "savefig", "to_csv", "to_parquet", "to_excel", "to_pickle",
}
SAVE_MODULES = {"np", "numpy", "torch", "joblib"}
DUMP_MODULES = {"json", "pickle", "yaml", "toml", "marshal", "plistlib"}
# Modules whose `open` takes a path first and the mode second, like the builtin.
PATH_FIRST_OPENERS = {"io", "gzip", "bz2", "lzma", "codecs", "tarfile", "zipfile", "os"}
EVIDENCE = {
    "unreadable": "could not parse it",
    "unknown": "calls %s, which it never defines or imports",
    "network": "uses the network (%s)",
    "secrets": "reads a secret (%s)",
    "publishes": "publishes (%s)",
    "subprocess": "runs a command (%s)",
    "serves": "runs a server (%s)",
    "training": "uses a training library (%s)",
    "writes": "writes files (%s)",
}


def _words(*texts: str) -> list[str]:
    found = []
    for text in texts:
        found.extend(piece.lower() for piece in WORD_SPLIT_RE.split(text or "") if piece)
    return found


def _first(words, stems) -> str:
    return next((word for word in words if word.startswith(stems)), "")


# -- reading a Python script ----------------------------------------------


def _module_matches(name: str, modules) -> str:
    for module in modules:
        if name == module or name.startswith(module + "."):
            return module
    return ""


class _Effects(ast.NodeVisitor):
    """What a script does, from its imports and calls. Nothing is executed."""

    def __init__(self):
        self.found: dict[str, str] = {}

    def note(self, effect: str, detail: str) -> None:
        self.found.setdefault(effect, detail)

    def _imported(self, name: str) -> None:
        if hit := _module_matches(name, NETWORK_MODULES):
            self.note("network", hit)
        if hit := _module_matches(name, SECRET_MODULES):
            self.note("secrets", hit)
        if hit := _module_matches(name, SUBPROCESS_MODULES):
            self.note("subprocess", hit)
        if hit := _module_matches(name, SERVER_MODULES):
            self.note("serves", hit)
        if hit := _module_matches(name, TRAINING_MODULES):
            self.note("training", hit)

    def visit_Import(self, node):
        for alias in node.names:
            self._imported(alias.name)

    def visit_ImportFrom(self, node):
        if node.module:
            self._imported(node.module)
            for alias in node.names:
                self._imported("%s.%s" % (node.module, alias.name))

    def visit_Call(self, node):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        # The last link before the call: `os` in `os.remove`, `environ` in
        # `os.environ.get`, `foo` in `self.foo.save`.
        owner = ""
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                owner = func.value.id
            elif isinstance(func.value, ast.Attribute):
                owner = func.value.attr
        if name in NETWORK_CALLS:
            self.note("network", name)
        if owner == "os" and name in OS_SUBPROCESS:
            self.note("subprocess", "os.%s" % name)
        if owner == "asyncio" and name.startswith("create_subprocess"):
            self.note("subprocess", "asyncio.%s" % name)
        if owner == "os" and name in OS_WRITES:
            self.note("writes", "os.%s" % name)
        if owner == "shutil" and name in SHUTIL_WRITES:
            self.note("writes", "shutil.%s" % name)
        if owner in DUMP_MODULES and name == "dump":
            self.note("writes", "%s.dump" % owner)
        if owner in SAVE_MODULES and name.startswith("save"):
            self.note("writes", "%s.%s" % (owner, name))
        if name in WRITE_METHODS:
            self.note("writes", name)
        if name in ("open", "ZipFile"):
            # `open(path, mode)` and `gzip.open(path, mode)` take the mode
            # second; `Path.open(mode)` takes it first.
            position = 1 if not isinstance(func, ast.Attribute) or owner in PATH_FIRST_OPENERS else 0
            mode = self._mode(node, position)
            if mode is None or any(flag in mode for flag in "wax+"):
                self.note("writes", "%s(%s)" % (name, repr(mode) if mode is not None else "a computed mode"))
        if owner in ("environ", "os") and name in ("get", "getenv", "pop"):
            self._secret_name(node.args[0] if node.args else None)
        self.generic_visit(node)

    def _mode(self, node, position: int):
        """The mode a call opens with: a string, None if computed, "r" if absent."""
        for keyword in node.keywords:
            if keyword.arg == "mode":
                value = keyword.value
                return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None
        if len(node.args) <= position:
            return "r"
        value = node.args[position]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value if MODE_RE.match(value.value) else "r"
        return None

    def _secret_name(self, arg) -> None:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            upper = arg.value.upper()
            if any(word in upper for word in SECRET_NAMES):
                self.note("secrets", arg.value)

    def visit_Subscript(self, node):
        value = node.value
        is_environ = (isinstance(value, ast.Attribute) and value.attr == "environ") or (
            isinstance(value, ast.Name) and value.id == "environ"
        )
        if is_environ:
            self._secret_name(node.slice)
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            lowered = node.value.lower()
            if hit := next((phrase for phrase in PUBLISH_PHRASES if phrase in lowered), ""):
                self.note("publishes", hit.strip())
            if "godot" in lowered:
                self.note("godot", "godot")

    def _sequence(self, node):
        parts = [
            item.value.lower()
            for item in node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        joined = " ".join(parts) + " "
        if hit := next((phrase for phrase in PUBLISH_PHRASES if phrase in joined), ""):
            self.note("publishes", hit.strip())
        self.generic_visit(node)

    visit_List = _sequence
    visit_Tuple = _sequence


BUILTIN_NAMES = set(dir(builtins))


def _unbound_calls(tree: ast.Module) -> list[str]:
    """Names the script calls but never defines, imports or gets from Python.

    A script written for another runtime calls names that runtime injects, such
    as `pierce_with_arrow` and `summon_stand`; nothing in the file says what they
    do, so the script's effects cannot be read from it.
    """
    bound = set(BUILTIN_NAMES)
    called = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = node.args
            for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs:
                bound.add(arg.arg)
            for arg in (arguments.vararg, arguments.kwarg):
                if arg is not None:
                    bound.add(arg.arg)
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif type(node).__name__ in ("MatchAs", "MatchStar") and getattr(node, "name", None):
            bound.add(node.name)
        elif type(node).__name__ == "MatchMapping" and getattr(node, "rest", None):
            bound.add(node.rest)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.append(node.func.id)
    return sorted({name for name in called if name not in bound})


def _is_script(tree: ast.Module) -> bool:
    """A file meant to be run: a `__main__` guard, or code at the top level.

    A module of only imports, definitions and constants is a helper the scripts
    import, not a command.
    """
    for node in tree.body:
        if isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
            ):
                return True
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Try) and all(
            isinstance(child, (ast.Import, ast.ImportFrom)) for child in node.body
        ):
            continue  # an optional import
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the docstring
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and not isinstance(
            getattr(node, "value", None), ast.Call
        ):
            continue  # a constant
        return True
    return False


def _evidence(effects: dict) -> list[str]:
    found = []
    for effect, template in EVIDENCE.items():
        if effect not in effects:
            continue
        if effect == "subprocess" and "godot" in effects:
            found.append("runs Godot")
        else:
            found.append(template % effects[effect] if "%s" in template else template)
    return found


def _place(effects: dict, words: list[str]) -> tuple[str, list[str]]:
    """The group for a Python script, and the evidence, in SPEC.md's order."""
    evidence = _evidence(effects)
    debug_word = _first(words, DEBUG_STEMS)
    if "unreadable" in effects or "unknown" in effects:
        return "manual", evidence
    manual_word = _first(words, MANUAL_STEMS)
    if manual_word:
        return "manual", evidence + ["its name or docstring says %r" % manual_word]
    if {"network", "secrets", "publishes"} & effects.keys():
        return "manual", evidence
    if {"subprocess", "serves", "training"} & effects.keys():
        return ("debug" if debug_word else "dev"), evidence
    if "writes" in effects:
        return "manual", evidence
    return ("debug" if debug_word else "inspect"), evidence or ["reads only"]


def _script_summary(tree: ast.AST) -> str:
    doc = (ast.get_docstring(tree) or "").strip()
    return doc.splitlines()[0].strip() if doc else ""


def _script_args(tree: ast.AST) -> list[str]:
    """The long and short flags a script accepts, in the order written.

    Reading argparse is enough to tell an agent what can be varied; the exact
    spelling and help come from `--help` when the tool finally runs.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else ""
        if name != "add_argument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
                if value.startswith("-") and value not in found:
                    found.append(value)
    return found


class _Reader:
    """Reads each of the repo's Python modules once per scan.

    A script often hands its work to the repo's own package: a training repo's
    `train.py` is three lines that call `package.train.main`. Reading the
    script alone would call it read-only, so its effects are those of every
    module of this repo it imports, directly or not. That over-approximates
    (a whole module counts, not just the function called), which is the safe
    direction: a script is never placed lower than its imports allow.
    """

    MAX_MODULES = 400

    def __init__(self, root: Path, paths):
        self.root = root
        self.trees: dict[str, object] = {}
        self.direct: dict[str, tuple] = {}
        self.by_module: dict[str, list[str]] = {}
        for rel in paths:
            if not rel.endswith(".py"):
                continue
            parts = rel[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for start in range(len(parts)):
                self.by_module.setdefault("/".join(parts[start:]), []).append(rel)

    def tree(self, rel: str):
        if rel not in self.trees:
            try:
                self.trees[rel] = ast.parse((self.root / rel).read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError):
                self.trees[rel] = None
        return self.trees[rel]

    def _resolve(self, dotted: str) -> list[str]:
        return list(self.by_module.get(dotted.replace(".", "/"), []))

    def _imports(self, rel: str, tree) -> list[str]:
        found = []
        here = rel.split("/")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found += self._resolve(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    anchor = here[: len(here) - (node.level - 1)] if node.level > 1 else here
                    base = "/".join(anchor + ([node.module.replace(".", "/")] if node.module else []))
                    names = [base] + ["%s/%s" % (base, alias.name) for alias in node.names]
                    for name in names:
                        for candidate in (name + ".py", name + "/__init__.py"):
                            if candidate in self.trees or (self.root / candidate).is_file():
                                found.append(candidate)
                elif node.module:
                    found += self._resolve(node.module)
                    for alias in node.names:
                        found += self._resolve("%s.%s" % (node.module, alias.name))
        return [item for item in dict.fromkeys(found) if item != rel]

    def _direct(self, rel: str) -> tuple:
        if rel not in self.direct:
            tree = self.tree(rel)
            if tree is None:
                self.direct[rel] = ({"unreadable": "could not parse it"}, [])
            else:
                visitor = _Effects()
                visitor.visit(tree)
                self.direct[rel] = (visitor.found, self._imports(rel, tree))
        return self.direct[rel]

    def effects(self, rel: str) -> dict:
        """The script's effects, then those of each repo module it reaches."""
        found, queue = dict(self._direct(rel)[0]), list(self._direct(rel)[1])
        seen = {rel}
        while queue and len(seen) < self.MAX_MODULES:
            module = queue.pop(0)
            if module in seen:
                continue
            seen.add(module)
            effects, imports = self._direct(module)
            for effect, detail in effects.items():
                if effect == "unreadable":
                    continue  # a module we cannot parse says nothing either way
                found.setdefault(effect, "%s, in %s" % (detail, module))
            queue.extend(item for item in imports if item not in seen)
        return found


def _python_command(reader: _Reader, rel: str) -> dict | None:
    name = Path(rel).stem
    tree = reader.tree(rel)
    if tree is None:
        group, evidence = _place({"unreadable": "could not parse it"}, _words(name))
        summary, args = "", []
    else:
        if not _is_script(tree):
            return None
        summary = _script_summary(tree)
        args = _script_args(tree)
        effects = reader.effects(rel)
        unknown = _unbound_calls(tree)
        if unknown:
            effects["unknown"] = ", ".join(unknown[:3]) + (" and more" if len(unknown) > 3 else "")
        group, evidence = _place(effects, _words(name, summary))
    return {
        "id": "script:%s" % rel,
        "kind": "script",
        "path": rel,
        "name": name,
        "summary": summary,
        "args": args,
        "guess": group,
        "evidence": evidence,
    }


def _shell_group(name: str, summary: str) -> tuple[str, list[str]]:
    words = _words(name, summary)
    if word := _first(words, MANUAL_STEMS):
        return "manual", ["its name or summary says %r" % word]
    if word := _first(words, DEBUG_STEMS):
        return "debug", ["its name or summary says %r" % word]
    if word := _first(words, SHELL_DEV_STEMS):
        return "dev", ["its name or summary says %r" % word]
    return "manual", ["a shell target whose name does not say what it does"]


# -- scanning -------------------------------------------------------------


def _clean_folders(value) -> list[str]:
    folders = []
    for item in value if isinstance(value, list) else []:
        text = str(item).strip().strip("/")
        if text and not text.startswith(("/", "..")) and ".." not in Path(text).parts:
            folders.append(text)
    return folders


def script_folders(root: Path, data: dict | None = None) -> list[str]:
    """Where scripts may live: tools.json's choice, plus the local folder."""
    data = load(root) if data is None else data
    folders = _clean_folders(data.get("folders")) if "folders" in data else list(SCRIPT_FOLDERS)
    if (Path(root) / ".repo-brief" / "local.json").is_file() and LOCAL_FOLDER not in folders:
        folders.append(LOCAL_FOLDER)
    return folders


def _script_paths(root: Path, detected, folders) -> list[str]:
    paths = set()
    for folder in folders:
        prefix = folder + "/"
        if folder == LOCAL_FOLDER:
            # Git ignores it, so the walk never saw it.
            base = root / LOCAL_FOLDER
            if base.is_dir():
                paths.update(path.relative_to(root).as_posix() for path in base.rglob("*.py"))
            continue
        paths.update(rel for rel in detected.paths if rel.startswith(prefix) and rel.endswith(".py"))
    return sorted(
        rel for rel in paths
        if not rel.endswith(("brief_adapter.py", "/__init__.py")) and "/__pycache__/" not in rel
    )


def _devsh_commands(root: Path, paths) -> list[dict]:
    rel = next((candidate for candidate in DEVSH_PATHS if candidate in paths), "")
    if not rel:
        return []
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for name in dict.fromkeys(DEVSH_TARGET_RE.findall(text)):
        if name in {"usage", "help"}:
            continue
        group, evidence = _shell_group(name, "")
        found.append(
            {
                "id": "devsh:%s" % name,
                "kind": "devsh",
                "path": rel,
                "name": name,
                "summary": "",
                "args": [],
                "guess": group,
                "evidence": evidence,
            }
        )
    return found


def _make_commands(root: Path, paths) -> list[dict]:
    rel = next((candidate for candidate in MAKEFILES if candidate in paths), "")
    if not rel:
        return []
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for name, summary in MAKE_TARGET_RE.findall(text):
        group, evidence = _shell_group(name, summary)
        found.append(
            {
                "id": "make:%s" % name,
                "kind": "make",
                "path": rel,
                "name": name,
                "summary": summary.strip(),
                "args": [],
                "guess": group,
                "evidence": evidence,
            }
        )
    return found


def scan(root: Path, detected, folders=None) -> list[dict]:
    """Every script and target the repo has, each with a guessed group."""
    root = Path(root)
    folders = script_folders(root) if folders is None else folders
    scripts = _script_paths(root, detected, folders)
    reader = _Reader(root, set(detected.paths) | set(scripts))
    commands = [
        item
        for item in (_python_command(reader, rel) for rel in scripts)
        if item is not None
    ]
    commands += _devsh_commands(root, detected.paths) + _make_commands(root, detected.paths)
    for item in commands:
        item["group"] = item["guess"]
    commands.sort(key=lambda item: (GROUPS.index(item["group"]), item["id"]))
    return commands


# -- tools.json -----------------------------------------------------------


def path_for(root: Path) -> Path:
    return Path(root) / ".repo-brief" / STATE_FILE


def load(root: Path) -> dict:
    try:
        data = json.loads(path_for(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def commands(data: dict) -> list[dict]:
    """The detected scripts, each with the group it is in now."""
    items = data.get("detected")
    if items is None:
        items = data.get("commands")  # the first layout of this file
    return [item for item in items or [] if isinstance(item, dict)]


def _choices(data: dict) -> dict:
    found = data.get("choices")
    if found is None:
        found = data.get("judgments")  # the first layout of this file
    return {
        str(key): value
        for key, value in (found or {}).items()
        if value in GROUPS and value != "brief"
    }


def _save(root: Path, data: dict) -> Path:
    target = path_for(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def write(root: Path, detected) -> tuple[Path, dict, list[dict]]:
    """Rescan, keep every choice, and return the scripts that are new."""
    root = Path(root)
    previous = load(root)
    folders = _clean_folders(previous.get("folders")) if "folders" in previous else list(SCRIPT_FOLDERS)
    choices = _choices(previous)
    found = scan(root, detected, script_folders(root, {"folders": folders}))
    for item in found:
        if item["id"] in choices:
            item["group"] = choices[item["id"]]
    found.sort(key=lambda item: (GROUPS.index(item["group"]), item["id"]))
    known = {item.get("id") for item in commands(previous)}
    added = [item for item in found if item["id"] not in known] if previous else list(found)
    data = {"about": ABOUT, "folders": folders, "choices": choices, "detected": found}
    return _save(root, data), data, added


def set_group(root: Path, command_id: str, group: str) -> dict:
    if group not in GROUPS or group == "brief":
        raise ValueError(
            "unknown group %r (use %s)" % (group, ", ".join(item for item in GROUPS if item != "brief"))
        )
    data = load(root)
    items = commands(data)
    if command_id not in {item.get("id") for item in items}:
        raise ValueError("unknown command %r; run `repo-brief tools` for ids" % command_id)
    choices = _choices(data)
    choices[command_id] = group
    for item in items:
        if item.get("id") == command_id:
            item["group"] = group
    data["choices"] = choices
    data["detected"] = items
    data.pop("commands", None)
    data.pop("judgments", None)
    _save(root, data)
    return data


# -- who may use what -----------------------------------------------------


def allowed_groups(trusted: bool, narrowed=None) -> set:
    """The groups a caller may use: the fixed rules, then a client's narrowing."""
    allowed = {
        group
        for group, reach in ORCHESTRATOR.items()
        if reach == "always" or (reach == "trusted" and trusted)
    }
    if narrowed is not None:
        allowed &= set(narrowed)
    return allowed


def runnable(root: Path, item: dict) -> str:
    """Why this catalog entry may not run, or "" when it may.

    `tools.json` is a file in the repo, so its paths are checked again here
    rather than trusted: a script must sit inside a script folder of this
    repository, and a shell or make target must name the repo's own file.
    """
    rel = str(item.get("path") or "")
    kind = item.get("kind")
    top = Path(root).resolve()
    target = (top / rel).resolve()
    try:
        inside = target.relative_to(top).as_posix()
    except ValueError:
        return "%s is outside the repository" % rel
    if not target.is_file():
        return "%s does not exist" % rel
    if kind == "script":
        folders = script_folders(top)
        if not any(inside.startswith(folder + "/") for folder in folders):
            return "%s is not in a script folder (%s)" % (rel, ", ".join(folders))
        return ""
    if kind == "devsh":
        return "" if inside in DEVSH_PATHS else "%s is not the repo's dev.sh" % rel
    if kind == "make":
        return "" if inside in MAKEFILES else "%s is not the repo's Makefile" % rel
    return "unknown kind %r" % kind
