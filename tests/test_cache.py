"""The parse cache: hits are exact, edits miss, and the tree is never cached."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from repobrief import parsers, render  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.cache import CACHE_FILE, LOCAL_FILES, ParseCache  # noqa: E402
from repobrief.detect import STATE_DIR  # noqa: E402
from repobrief.parsers import PARSERS  # noqa: E402


def build(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-cache-"))
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def read_all(root: Path):
    """One pass over every parseable file, the way the adapter reads them."""
    cache = ParseCache(root)
    units = {}
    for path in sorted(root.rglob("*")):
        reader = PARSERS.get(path.suffix)
        if reader and STATE_DIR not in path.parts:
            rel = path.relative_to(root).as_posix()
            units[rel] = cache.read(reader, path, rel)
    cache.save()
    return cache, units


def package_imports(source: str) -> set[str]:
    """Every repobrief module a top-level module imports, at any depth, however spelled."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name("." * node.level + (node.module or ""), "repobrief")
            # `from repobrief import kernel` names a module, not an attribute.
            names = ["%s.%s" % (base, alias.name) for alias in node.names] if base == "repobrief" else [base]
        else:
            continue
        found.update(name for name in names if name.partition(".")[0] == "repobrief")
    return found


class ParseCacheTest(unittest.TestCase):
    def setUp(self):
        self.root = fixture.build_java()

    def test_a_second_pass_is_all_hits_and_identical(self):
        first, cold = read_all(self.root)
        second, warm = read_all(self.root)
        self.assertEqual((first.hits, first.misses), (0, len(cold)))
        self.assertEqual((second.hits, second.misses), (len(warm), 0))
        self.assertEqual(cold, warm)

    def test_an_edited_file_is_parsed_again(self):
        read_all(self.root)
        path = self.root / fixture.JAVA_MAIN / "stands/StandUser.java"
        path.write_text(path.read_text(encoding="utf-8") + "class Extra {}\n", encoding="utf-8")
        cache, units = read_all(self.root)
        self.assertEqual(cache.misses, 1)
        self.assertIn("Extra", {symbol["name"] for symbol in units[fixture.JAVA_MAIN + "stands/StandUser.java"].symbols})

    def test_a_different_parser_starts_over(self):
        read_all(self.root)
        path = self.root / STATE_DIR / CACHE_FILE
        data = json.loads(path.read_text(encoding="utf-8"))
        data["parser"] = "an older parser"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(ParseCache(self.root).previous, {})

    def test_a_corrupt_cache_is_ignored(self):
        (self.root / STATE_DIR).mkdir()
        (self.root / STATE_DIR / CACHE_FILE).write_text("{not json", encoding="utf-8")
        cache, units = read_all(self.root)
        self.assertEqual(cache.misses, len(units))


class ParserStampTest(unittest.TestCase):
    """`parser_version()` hashes parsers.py alone, so the parsing code must all live there."""

    def test_the_parsers_import_nothing_the_stamp_misses(self):
        # `contract` is allowed: it only defines the Unit the readers return.
        imported = package_imports(Path(parsers.__file__).read_text(encoding="utf-8"))
        self.assertEqual(
            imported - {"repobrief.contract"},
            set(),
            "parser_version() hashes only parsers.py; hash these modules too, "
            "or a change to them leaves stale parses in every cache",
        )


class TreeIsNotCachedTest(unittest.TestCase):
    """What a file says about other files is resolved again on every pass."""

    def test_a_new_module_takes_over_an_import(self):
        root = build({
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "from pkg import utils\n",
        })
        units = {unit.path: unit for unit in GenericAdapter().units(root)}
        self.assertEqual(units["src/pkg/a.py"].fields["loads"], ["src/pkg/__init__.py"])

        (root / "src/pkg/utils.py").write_text("", encoding="utf-8")
        units = {unit.path: unit for unit in GenericAdapter().units(root)}
        self.assertEqual(units["src/pkg/a.py"].fields["loads"], ["src/pkg/utils.py"])

    def test_a_new_note_becomes_a_link(self):
        root = build({"docs/index.md": "See [the notes](notes.md).\n"})
        units = {unit.path: unit for unit in GenericAdapter().units(root)}
        self.assertEqual(units["docs/index.md"].fields["links"], [])

        (root / "docs/notes.md").write_text("# Notes\n", encoding="utf-8")
        units = {unit.path: unit for unit in GenericAdapter().units(root)}
        self.assertEqual(units["docs/index.md"].fields["links"], ["docs/notes.md"])


class LocalFilesTest(unittest.TestCase):
    def test_the_cache_keeps_itself_out_of_git(self):
        root = fixture.build()
        GenericAdapter().units(root)
        ignored = (root / STATE_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertTrue((root / STATE_DIR / CACHE_FILE).is_file())
        for name in LOCAL_FILES:
            self.assertIn(name, ignored)
        self.assertNotIn("map.md", ignored)
        self.assertNotIn("profile.json", ignored)

    def test_an_existing_gitignore_is_extended_not_replaced(self):
        root = fixture.build()
        (root / STATE_DIR).mkdir()
        (root / STATE_DIR / ".gitignore").write_text("scratch.txt", encoding="utf-8")
        GenericAdapter().units(root)
        ignored = (root / STATE_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(ignored[0], "scratch.txt")
        self.assertIn(CACHE_FILE, ignored)

    def test_a_tree_that_cannot_be_written_still_answers(self):
        root = fixture.build()
        (root / STATE_DIR).write_text("in the way", encoding="utf-8")
        self.assertTrue(GenericAdapter().units(root))

    def test_a_repo_with_nothing_to_parse_gets_no_state_directory(self):
        root = build({"notes.txt": "plain\n"})
        GenericAdapter().units(root)
        self.assertFalse((root / STATE_DIR).exists())


class WarmMapTest(unittest.TestCase):
    def test_the_map_is_the_same_cold_or_warm(self):
        root = fixture.build_java()

        def render_map():
            adapter = GenericAdapter()
            return render.render_map(adapter.detected(root), adapter.units(root), [], [], adapter.name)

        self.assertEqual(render_map(), render_map())


if __name__ == "__main__":
    unittest.main()
