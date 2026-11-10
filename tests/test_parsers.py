"""Shared parser facts: Python imports and markdown links become edges."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief.parsers import parse_markdown, parse_python  # noqa: E402


def build(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-parsers-"))
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class PythonImportTest(unittest.TestCase):
    def test_absolute_import_resolves_to_the_module_file(self):
        root = build(
            {
                "src/pkg/__init__.py": "",
                "src/pkg/a.py": "from pkg.b import Thing\n",
                "src/pkg/b.py": "class Thing:\n    pass\n",
            }
        )
        unit = parse_python(root, root / "src/pkg/a.py")
        self.assertEqual(unit.fields["loads"], ["src/pkg/b.py"])

    def test_relative_import_resolves_inside_the_package(self):
        root = build(
            {
                "src/pkg/__init__.py": "",
                "src/pkg/a.py": "from . import c\n",
                "src/pkg/c.py": "",
            }
        )
        unit = parse_python(root, root / "src/pkg/a.py")
        self.assertEqual(unit.fields["loads"], ["src/pkg/c.py"])

    def test_parent_relative_import_walks_up(self):
        root = build(
            {
                "src/pkg/__init__.py": "",
                "src/pkg/util.py": "",
                "src/pkg/sub/__init__.py": "",
                "src/pkg/sub/m.py": "from .. import util\n",
            }
        )
        unit = parse_python(root, root / "src/pkg/sub/m.py")
        self.assertEqual(unit.fields["loads"], ["src/pkg/util.py"])

    def test_external_imports_are_dropped(self):
        root = build({"src/pkg/a.py": "import numpy as np\nfrom pathlib import Path\n"})
        unit = parse_python(root, root / "src/pkg/a.py")
        self.assertEqual(unit.fields["loads"], [])

    def test_symbols_come_from_the_syntax_tree(self):
        root = build(
            {
                "src/pkg/a.py": (
                    '"""One line about a."""\n'
                    "\n"
                    "LIMIT = 3\n"
                    "\n"
                    "class Thing:\n"
                    "    def method(self):\n"
                    "        pass\n"
                    "\n"
                    "def helper():\n"
                    "    pass\n"
                )
            }
        )
        unit = parse_python(root, root / "src/pkg/a.py")
        names = {symbol["name"] for symbol in unit.symbols}
        self.assertEqual(names, {"LIMIT", "Thing", "method", "helper"})
        self.assertEqual(unit.fields["description"], "One line about a.")

    def test_a_file_that_does_not_parse_still_has_symbols(self):
        root = build({"src/pkg/broken.py": "def half(\n"})
        unit = parse_python(root, root / "src/pkg/broken.py")
        self.assertEqual([symbol["name"] for symbol in unit.symbols], ["half"])

    def test_a_decorated_function_starts_at_its_first_decorator(self):
        root = build(
            {
                "src/pkg/a.py": (
                    "class Thing:\n"
                    "    @property\n"
                    "    @cached\n"
                    "    def value(self):\n"
                    "        return 1\n"
                )
            }
        )
        unit = parse_python(root, root / "src/pkg/a.py")
        lines = {symbol["name"]: symbol["line"] for symbol in unit.symbols}
        self.assertEqual(lines["value"], 2)

    def test_extends_is_the_first_base_class_as_written(self):
        root = build({"src/pkg/a.py": "class Net(nn.Module, Mixin):\n    pass\n"})
        unit = parse_python(root, root / "src/pkg/a.py")
        self.assertEqual(unit.fields["extends"], "nn.Module")

    def test_object_is_not_a_parent(self):
        root = build({"src/pkg/a.py": "class Plain(object):\n    pass\n"})
        unit = parse_python(root, root / "src/pkg/a.py")
        self.assertEqual(unit.fields["extends"], "")


class MarkdownLinkTest(unittest.TestCase):
    def test_relative_links_resolve_against_the_file(self):
        root = build(
            {
                "docs/index.md": "[now](now.md) [deep](sub/deep.md#part)\n",
                "docs/now.md": "",
                "docs/sub/deep.md": "",
            }
        )
        unit = parse_markdown(root, root / "docs/index.md")
        self.assertEqual(unit.fields["links"], ["docs/now.md", "docs/sub/deep.md"])

    def test_external_and_image_links_are_not_edges(self):
        root = build(
            {
                "docs/index.md": "[x](https://example.com/a.md) ![i](img.png) [y](missing.md)\n",
            }
        )
        unit = parse_markdown(root, root / "docs/index.md")
        self.assertEqual(unit.fields["links"], [])

    def test_wikilinks_still_work(self):
        root = build({"docs/index.md": "[[project/decisions]]\n"})
        unit = parse_markdown(root, root / "docs/index.md")
        self.assertEqual(unit.fields["links"], ["project/decisions"])


if __name__ == "__main__":
    unittest.main()
