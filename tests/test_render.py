"""The brief is a read plan, not just a list of files."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from repobrief import kernel, render  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402


def plan_for(root, query):
    context = kernel.build_context(root, GenericAdapter(), query)
    return context, context["read_plan"]


class ReadPlanTest(unittest.TestCase):
    def test_plan_entries_are_slices_with_a_cost(self):
        context, plan = plan_for(fixture.build(), "barrier freeze")
        self.assertTrue(plan)
        for item in plan:
            self.assertGreaterEqual(item["start"], 1)
            self.assertGreaterEqual(item["end"], item["start"])
            self.assertGreaterEqual(item["line"], item["start"])
            self.assertLessEqual(item["line"], item["end"])
            self.assertGreater(item["bytes"], 0)
        self.assertEqual(context["budget"]["files"], len(plan))
        self.assertEqual(
            context["budget"]["bytes"], sum(item["bytes"] for item in plan)
        )

    def test_a_named_function_gets_its_doc_comment_and_body(self):
        _context, plan = plan_for(fixture.build(), "emeraldbarrier trigger")
        item = next(item for item in plan if item["path"].endswith("emeraldbarrier.gd"))
        self.assertEqual((item["start"], item["end"]), (4, 6))
        self.assertEqual(item["label"], "trigger")

    def test_a_small_file_with_only_a_stem_match_is_not_sliced(self):
        _context, plan = plan_for(fixture.build(), "emeraldbarrier freeze")
        item = next(item for item in plan if item["path"].endswith("emeraldbarrier.gd"))
        self.assertEqual((item["start"], item["end"]), (1, 6))

    def test_a_decorated_python_function_keeps_its_decorator_and_comment(self):
        root = Path(tempfile.mkdtemp(prefix="repo-brief-render-"))
        (root / "src").mkdir()
        (root / "src" / "model.py").write_text(
            '"""Model."""\n'
            "\n"
            "\n"
            "def first():\n"
            "    return 1\n"
            "\n"
            "\n"
            "# Cache the expensive lookup.\n"
            "@functools.cache\n"
            "def lookup_table():\n"
            "    return {}\n",
            encoding="utf-8",
        )
        _context, plan = plan_for(root, "lookup table")
        item = next(item for item in plan if item["path"] == "src/model.py")
        self.assertEqual((item["start"], item["end"], item["label"]), (8, 11, "lookup_table"))

    def test_a_long_section_is_capped(self):
        root = fixture.build_dated()
        status = root / "docs/status.md"
        body = "zebra\n" * 3000
        status.write_text(
            "# Status\n\n**Updated:** 2026-10-02\n\n## Long\n\n%s\n## Next\n\nother\n" % body,
            encoding="utf-8",
        )
        _context, plan = plan_for(root, "zebra")
        item = next(item for item in plan if item["path"] == "docs/status.md")
        self.assertLessEqual(item["end"] - item["start"] + 1, kernel.MAX_SECTION_LINES)

    def test_brief_renders_the_plan_and_its_budget(self):
        context, plan = plan_for(fixture.build(), "barrier freeze")
        text = render.render_brief(context)
        self.assertIn("Read (", text)
        self.assertIn("B)", text)
        for item in plan:
            self.assertIn("%s:%d-%d" % (item["path"], item["start"], item["end"]), text)

    def test_the_brief_names_the_files_after_the_plan(self):
        text = render.render_brief(
            {
                "query": "barrier",
                "read_plan": [{"path": "a.gd", "start": 1, "end": 3, "bytes": 10}],
                "also_matched": ["b.gd", "c.gd"],
            }
        )
        self.assertIn("Also matched: b.gd, c.gd", text)

    def test_a_brief_that_matches_nothing_says_what_it_searched(self):
        context, plan = plan_for(fixture.build(), "zebra")
        self.assertEqual(plan, [])
        self.assertIn("No match for: zebra", render.render_brief(context))


if __name__ == "__main__":
    unittest.main()
