"""Archive: hide it by default, and move things into it safely."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from repobrief import archive, graph, kernel  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.detect import detect  # noqa: E402


class ArchiveDetectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_archive()
        cls.detected = detect(cls.root)
        cls.adapter = GenericAdapter()

    def test_finds_the_archive_directory(self):
        self.assertEqual(self.detected.archived, ["docs/archive"])

    def test_units_in_the_archive_are_tagged(self):
        units = {unit.path: unit for unit in self.adapter.units(self.root)}
        self.assertEqual(units["docs/archive/old.md"].kind, "archive")
        self.assertEqual(units["docs/keep.md"].kind, "note")

    def test_archived_docs_are_not_sources(self):
        paths = {source.path for source in self.adapter.sources(self.root)}
        self.assertNotIn("docs/archive/old.md", paths)


class ArchiveExclusionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_archive()
        cls.adapter = GenericAdapter()

    def test_context_skips_the_archive_by_default(self):
        context = kernel.build_context(self.root, self.adapter, "old", archived=False)
        self.assertNotIn("docs/archive/old.md", [unit["path"] for unit in context["units"]])

    def test_context_includes_it_when_asked(self):
        context = kernel.build_context(self.root, self.adapter, "old", archived=True)
        self.assertTrue(context["archived"])
        self.assertIn("docs/archive/old.md", [unit["path"] for unit in context["units"]])

    def test_archived_units_are_flagged_for_the_renderer(self):
        context = kernel.build_context(self.root, self.adapter, "old", archived=True)
        item = next(unit for unit in context["units"] if unit["path"] == "docs/archive/old.md")
        self.assertTrue(item["archived"])


class CandidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_archive()
        cls.adapter = GenericAdapter()
        cls.detected = detect(cls.root)
        cls.units = cls.adapter.units(cls.root)
        cls.edges = graph.infer_edges(cls.units, None, cls.detected.autoloads)
        cls.found = {
            item.path: item
            for item in archive.candidates(
                cls.root, cls.adapter, cls.units, cls.edges, cls.adapter.canonical(cls.root)
            )
        }

    def test_orphan_is_found(self):
        self.assertEqual(self.found["docs/orphan.md"].reason, "orphaned")

    def test_page_linked_only_from_the_archive_is_found(self):
        self.assertEqual(self.found["docs/only-old.md"].reason, "only archived links")

    def test_duplicate_titles_are_found(self):
        self.assertEqual(self.found["docs/dup-a.md"].reason, "duplicate title")
        self.assertEqual(self.found["docs/dup-b.md"].reason, "duplicate title")

    def test_root_scratch_is_found(self):
        self.assertEqual(self.found["PLAN.md"].reason, "root scratch")

    def test_linked_and_canonical_docs_are_not_candidates(self):
        self.assertNotIn("docs/keep.md", self.found)
        self.assertNotIn("docs/index.md", self.found)
        self.assertNotIn("docs/status.md", self.found)

    def test_archived_docs_are_never_candidates(self):
        self.assertNotIn("docs/archive/old.md", self.found)


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.root = fixture.build_archive()
        self.adapter = GenericAdapter()

    def test_dry_run_changes_nothing(self):
        before = (self.root / "docs" / "orphan.md").read_text()
        archive.apply(self.root, self.adapter, ["docs/orphan.md"], dry_run=True, emit=lambda *_: None)
        self.assertTrue((self.root / "docs" / "orphan.md").is_file())
        self.assertEqual((self.root / "docs" / "orphan.md").read_text(), before)

    def test_moves_the_file(self):
        archive.apply(self.root, self.adapter, ["docs/orphan.md"], emit=lambda *_: None)
        self.assertFalse((self.root / "docs" / "orphan.md").exists())
        self.assertTrue((self.root / "docs" / "archive" / "orphan.md").is_file())

    def test_rewrites_a_bare_link(self):
        archive.apply(self.root, self.adapter, ["docs/only-old.md"], emit=lambda *_: None)
        text = (self.root / "docs" / "archive" / "old.md").read_text()
        self.assertIn("[[archive/only-old]]", text)

    def test_rewrites_a_labelled_link_and_keeps_the_label(self):
        archive.apply(self.root, self.adapter, ["docs/keep.md"], emit=lambda *_: None)
        text = (self.root / "docs" / "index.md").read_text()
        self.assertIn("[[archive/keep|Keep]]", text)

    def test_creates_the_archive_and_its_readme(self):
        root = fixture.build()
        adapter = GenericAdapter()
        (root / "docs" / "spare.md").write_text("# Spare\n", encoding="utf-8")
        archive.apply(root, adapter, ["docs/spare.md"], emit=lambda *_: None)
        self.assertTrue((root / "docs" / "archive" / "README.md").is_file())

    def test_refuses_a_canonical_doc(self):
        result = archive.apply(self.root, self.adapter, ["docs/status.md"], emit=lambda *_: None)
        self.assertEqual(result, 1)
        self.assertTrue((self.root / "docs" / "status.md").is_file())

    def test_refuses_a_missing_file(self):
        result = archive.apply(self.root, self.adapter, ["docs/nope.md"], emit=lambda *_: None)
        self.assertEqual(result, 1)

    def test_refuses_two_paths_that_share_a_basename(self):
        for folder in ("a", "b"):
            path = self.root / "docs" / folder / "same.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Same\n", encoding="utf-8")
        result = archive.apply(
            self.root,
            self.adapter,
            ["docs/a/same.md", "docs/b/same.md"],
            emit=lambda *_: None,
        )
        self.assertEqual(result, 1)
        self.assertTrue((self.root / "docs" / "a" / "same.md").is_file())

    def test_reports_a_plain_text_mention_it_cannot_rewrite(self):
        (self.root / "docs" / "mentions.md").write_text(
            "# Mentions\n\nSee docs/orphan.md for the details.\n", encoding="utf-8"
        )
        messages = []
        archive.apply(self.root, self.adapter, ["docs/orphan.md"], emit=messages.append)
        self.assertTrue(any("still mentions" in message for message in messages))


class LinkBodyTest(unittest.TestCase):
    def test_docs_relative_path(self):
        self.assertEqual(archive.link_body("docs/archive/foo.md"), "archive/foo")

    def test_path_outside_docs(self):
        self.assertEqual(archive.link_body("notes/foo.md"), "notes/foo")


if __name__ == "__main__":
    unittest.main()
