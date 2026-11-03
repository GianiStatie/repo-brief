"""The brief is a read plan, not just a list of files."""
from __future__ import annotations

import sys
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
        context, plan = plan_for(fixture.build(), "trap root")
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
        _context, plan = plan_for(fixture.build(), "beartrap spring")
        item = next(item for item in plan if item["path"].endswith("beartrap.gd"))
        self.assertEqual((item["start"], item["end"]), (4, 6))
        self.assertEqual(item["label"], "spring")

    def test_a_small_file_with_only_a_stem_match_is_not_sliced(self):
        _context, plan = plan_for(fixture.build(), "beartrap root")
        item = next(item for item in plan if item["path"].endswith("beartrap.gd"))
        self.assertEqual((item["start"], item["end"]), (1, 6))

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
        context, plan = plan_for(fixture.build(), "trap root")
        text = render.render_brief(context)
        self.assertIn("Read (", text)
        self.assertIn("B)", text)
        for item in plan:
            self.assertIn("%s:%d-%d" % (item["path"], item["start"], item["end"]), text)


if __name__ == "__main__":
    unittest.main()
