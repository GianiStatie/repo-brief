"""Work out what a repository is made of, from the tree alone.

Everything here is recomputed on every run. None of it belongs in a profile,
because caching facts is what creates the staleness problem in the first place.
A profile holds judgments; this holds evidence.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .parsers import walk_files

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".godot", ".cache", ".repo-brief", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", "build", "dist", "target", "vendor", ".idea", ".vscode", "coverage",
    # Godot's convention for third-party plugins, and friends.
    "addons", "third_party", "third-party", "deps", "libs", "3rdparty",
}

EXT_LANG = {
    ".gd": "gdscript", ".tscn": "godot-scene", ".tres": "godot-resource",
    ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".rs": "rust", ".go": "go", ".rb": "ruby",
    ".java": "java", ".kt": "kotlin", ".swift": "swift", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".m": "objc",
    ".md": "markdown", ".mdx": "markdown", ".rst": "rst",
    ".ex": "elixir", ".exs": "elixir", ".php": "php", ".lua": "lua",
    ".sh": "shell", ".css": "css", ".html": "html", ".sql": "sql",
}

MANIFESTS = [
    ("godot/project.godot", "godot"),
    ("project.godot", "godot"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("package.json", "node"),
    ("mix.exs", "elixir"),
    ("Gemfile", "ruby"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
]

# Directories that hold other people's code. Still indexed, but never allowed
# to become the "product" root that usage hits point at.
VENDOR_DIRS = {
    "addons", "vendor", "third_party", "third-party", "external", "deps",
    "node_modules", "site-packages", "libs", "vendored", "3rdparty",
}

DECISION_DIRS = ["docs/decisions", "docs/adr", "docs/decisions/active", "adr", "decisions"]
DECISION_FILES = [
    "docs/decisions.md",
    "docs/project/decisions.md",
    "docs/architecture/decisions.md",
    "DECISIONS.md",
]
STATUS_FILES = ["docs/status.md", "STATUS.md", "docs/status/index.md"]
CHANGELOG_FILES = ["docs/changelog.md", "CHANGELOG.md", "CHANGELOG"]
OPEN_WORK_FILES = [
    "docs/design/bugs_and_improvements.md",
    "docs/project/active-backlog.md",
    "docs/design/open-design-questions.md",
    "docs/backlog.md",
    "BACKLOG.md",
    "TODO.md",
]

# A filename is a weak hint on its own. Paired with the presence of unchecked
# boxes it is reliable enough to find a backlog the explicit list doesn't know.
OPEN_WORK_NAME_RE = re.compile(
    r"(backlog|bugs?|todo|issues|roadmap|open[-_]?work|known[-_]?issues)", re.I
)
INDEX_FILES = ["docs/index.md", "docs/README.md", "README.md"]

# Godot exposes singletons through `[autoload]` in the project file. Newer
# projects reference them by `uid://` and keep the mapping in `*.gd.uid`
# sidecars, so the path has to be resolved through that index.
GODOT_PROJECTS = ["godot/project.godot", "project.godot"]
AUTOLOAD_SECTION_RE = re.compile(r"^\[autoload\]\s*\n(.*?)(?=^\[|\Z)", re.M | re.S)
AUTOLOAD_LINE_RE = re.compile(r'^(\w+)="\*?([^"]+)"', re.M)

# Directories whose contents are history. Matched by name at any depth, but
# never inside a skipped or vendored tree. `old` is deliberately absent: it is
# too often a real folder name.
ARCHIVE_DIRS = {"archive", "archived", "attic", "deprecated", "superseded"}
DEFAULT_ARCHIVE = "docs/archive"
# Where repo-brief keeps what it derives: the committed map and profile, and
# local caches that `.repo-brief/.gitignore` keeps out of commits.
STATE_DIR = ".repo-brief"
# Where the owner's words land. Evidence, not guidance: quoted, never ranked.
CAPTURE_DIR = "docs/meta/capture"
NOTES_DOC = "docs/meta/repo-brief-notes.md"
# The conventions the document skill reads before it writes anything.
WRITING_DOC = "docs/meta/writing.md"

PARSED_EXTS = {".gd", ".tscn", ".tres", ".py", ".md", ".java"}

# Maven and Gradle keep each module's code in `src/main/java` and its tests in
# `src/test/java`. A multi-module build has one of each per module.
JVM_MAIN = ("src", "main", "java")
JVM_TEST = ("src", "test", "java")

# Extensions that are code we might want to point at, as opposed to prose or data.
CODE_EXTS = {
    ".gd", ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".rb", ".java",
    ".kt", ".swift", ".cs", ".c", ".h", ".cpp", ".hpp", ".ex", ".exs", ".php",
    ".lua", ".sh",
}


@dataclass
class Detected:
    root: Path
    extensions: Counter = field(default_factory=Counter)
    manifests: list = field(default_factory=list)
    decisions_dir: str = ""
    decisions_file: str = ""
    status: str = ""
    changelog: str = ""
    open_work: list = field(default_factory=list)
    index: str = ""
    test_dirs: list = field(default_factory=list)
    ci: list = field(default_factory=list)
    code_dirs: Counter = field(default_factory=Counter)
    main_roots: list = field(default_factory=list)
    # Every walked file, relative to the root, so the adapter need not walk again.
    files: list = field(default_factory=list)
    autoloads: dict = field(default_factory=dict)
    archived: list = field(default_factory=list)
    total_files: int = 0

    @property
    def source_root(self) -> str:
        """The directory that holds the product code, not its tooling.

        Usage hits should point at the thing being built. A line in `tools/`
        that implements the query is not a usage site.
        """
        excluded = set(self.test_dirs)
        ranked = [
            (key, count)
            for key, count in self.code_dirs.items()
            if not any(key == d or key.startswith(d + "/") for d in excluded)
        ]
        if not ranked:
            return ""
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))
        return ranked[0][0]

    @property
    def source_roots(self) -> list:
        """Every directory that holds product code.

        Most repos have one, `source_root`. A Maven or Gradle build keeps each
        module's code under its own `src/main/java`, and all of them count.
        """
        if self.main_roots:
            return list(self.main_roots)
        return [self.source_root] if self.source_root else []

    @property
    def languages(self) -> list:
        return [
            (EXT_LANG.get(ext, ext.lstrip(".")), count)
            for ext, count in self.extensions.most_common()
            if ext in EXT_LANG
        ]

    @property
    def parsers(self) -> list:
        return [ext for ext, _ in self.extensions.most_common() if ext in PARSED_EXTS]

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "total_files": self.total_files,
            "languages": [{"language": lang, "files": count} for lang, count in self.languages],
            "parsers": self.parsers,
            "manifests": self.manifests,
            "decisions_dir": self.decisions_dir,
            "decisions_file": self.decisions_file,
            "status": self.status,
            "changelog": self.changelog,
            "open_work": self.open_work,
            "index": self.index,
            "test_dirs": self.test_dirs,
            "ci": self.ci,
            "source_root": self.source_root,
            "source_roots": self.source_roots,
            "autoloads": self.autoloads,
            "archived": self.archived,
        }


def _first_existing(root: Path, candidates) -> str:
    for rel in candidates:
        if (root / rel).exists():
            return rel
    return ""


def _layout_root(parts: tuple, layout: tuple) -> str:
    """`app/src/main/java/com/x` with (src, main, java) -> `app/src/main/java`."""
    size = len(layout)
    for index in range(len(parts) - size + 1):
        if parts[index:index + size] == layout:
            return "/".join(parts[:index + size])
    return ""


def archive_prefix(relative: Path) -> str:
    """`docs/archive/x.md` -> `docs/archive`. Empty for anything live."""
    parts = relative.parts
    for index, part in enumerate(parts[:-1]):
        if part.lower() in ARCHIVE_DIRS:
            return "/".join(parts[: index + 1])
    return ""


def is_archived(path: str, dirs) -> bool:
    return any(path == directory or path.startswith(directory + "/") for directory in dirs)


def _autoloads(root: Path, uid_index: dict) -> dict:
    """Singleton name -> script path, from the Godot project file."""
    project = next((root / rel for rel in GODOT_PROJECTS if (root / rel).is_file()), None)
    if project is None:
        return {}
    try:
        text = project.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    section = AUTOLOAD_SECTION_RE.search(text)
    if not section:
        return {}

    prefix = "godot/" if project.parent.name == "godot" else ""
    found = {}
    for name, value in AUTOLOAD_LINE_RE.findall(section.group(1)):
        if value.startswith("uid://"):
            rel = uid_index.get(value, "")
        elif value.startswith("res://"):
            rel = prefix + value[len("res://"):]
        else:
            rel = prefix + value
        if rel:
            found[name] = rel
    return found


def _discover_open_work(root: Path, known) -> list[str]:
    """Find backlog-shaped files the explicit list doesn't name."""
    found = []
    for pattern in ("docs/**/*.md", "*.md"):
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if rel in known or not OPEN_WORK_NAME_RE.search(path.stem):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "- [ ]" in text:
                found.append(rel)
    return found


def detect(root: Path, skip_dirs=None) -> Detected:
    """Walk the tree once. `skip_dirs` lets an adapter name its generated trees.

    The default is the set every repo shares; a lab repo may also have a
    multi-gigabyte `runs/` that must never be walked.
    """
    root = Path(root).resolve()
    skip = SKIP_DIRS if skip_dirs is None else skip_dirs
    found = Detected(root=root)
    uid_index = {}
    archived = set()
    main_roots, test_roots = set(), set()

    for path in walk_files(root, skip):
        relative = path.relative_to(root)
        found.files.append(relative)
        prefix = archive_prefix(relative)
        if prefix:
            archived.add(prefix)
        if path.suffix == ".uid":
            try:
                uid = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                uid = ""
            if uid:
                uid_index[uid] = path.relative_to(root).as_posix()[:-4]
            continue
        found.total_files += 1
        found.extensions[path.suffix.lower()] += 1
        if path.suffix.lower() in CODE_EXTS and not (set(path.relative_to(root).parts) & VENDOR_DIRS):
            parts = path.relative_to(root).parts[:-1]
            key = "/".join(parts[:2]) if len(parts) > 2 else "/".join(parts) or "."
            found.code_dirs[key] += 1
        if path.suffix.lower() == ".java":
            main_roots.add(_layout_root(relative.parts[:-1], JVM_MAIN))
            test_roots.add(_layout_root(relative.parts[:-1], JVM_TEST))

    found.main_roots = sorted(rel for rel in main_roots if rel)
    found.manifests = [rel for rel, _ in MANIFESTS if (root / rel).is_file()]
    found.autoloads = _autoloads(root, uid_index)
    found.archived = sorted(archived)

    for rel in DECISION_DIRS:
        if (root / rel).is_dir() and any((root / rel).glob("*.md")):
            found.decisions_dir = rel
            break
    found.decisions_file = "" if found.decisions_dir else _first_existing(root, DECISION_FILES)

    found.status = _first_existing(root, STATUS_FILES)
    found.changelog = _first_existing(root, CHANGELOG_FILES)
    found.index = _first_existing(root, INDEX_FILES)
    found.open_work = [rel for rel in OPEN_WORK_FILES if (root / rel).is_file()]
    found.open_work.extend(_discover_open_work(root, found.open_work))

    for rel in ("tests", "test", "godot/tests", "src/tests", "spec", "__tests__"):
        if (root / rel).is_dir():
            found.test_dirs.append(rel)
    found.test_dirs.extend(sorted(rel for rel in test_roots if rel and rel not in found.test_dirs))

    for folder in (".github/workflows", ".gitlab"):
        base = root / folder
        if base.is_dir():
            found.ci.extend(
                p.relative_to(root).as_posix()
                for p in sorted(base.rglob("*"))
                if p.is_file() and p.suffix in {".yml", ".yaml"}
            )

    return found


# The facts a profile records that change what an adapter should do. Counts are
# deliberately excluded: a profile that goes stale because someone added a file
# would cry wolf on every commit.
PROFILE_FACTS = (
    "manifests",
    "decisions_dir",
    "decisions_file",
    "status",
    "changelog",
    "open_work",
    "index",
    "test_dirs",
    "archived",
)


def profile_drift(stored: dict, fresh: dict) -> list[str]:
    """Which recorded facts no longer match the tree.

    The profile is a snapshot, not a cache: everything here is recomputed every
    run. This compares the two so `preflight` can say the snapshot has aged out
    instead of trusting it.
    """
    drift = [key for key in PROFILE_FACTS if stored.get(key) != fresh.get(key)]
    stored_languages = {item["language"] for item in stored.get("languages", [])}
    fresh_languages = {item["language"] for item in fresh.get("languages", [])}
    if stored_languages != fresh_languages:
        drift.append("languages")
    return drift
