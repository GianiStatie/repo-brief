"""Detection and end-to-end behaviour on a known tree."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from repobrief import kernel, render  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.cli import write_profile  # noqa: E402
from repobrief.detect import SKIP_DIRS, detect, profile_drift  # noqa: E402


class DetectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build()
        cls.found = detect(cls.root)

    def test_finds_the_godot_manifest(self):
        self.assertIn("project.godot", self.found.manifests)

    def test_finds_the_decision_directory(self):
        self.assertEqual(self.found.decisions_dir, "docs/decisions")

    def test_finds_the_open_work_file(self):
        self.assertIn("docs/design/bugs.md", self.found.open_work)

    def test_source_root_is_product_code_not_tooling(self):
        self.assertEqual(self.found.source_root, "godot/src")

    def test_parsers_include_the_dominant_language(self):
        self.assertIn(".gd", self.found.parsers)
        self.assertIn(".md", self.found.parsers)


class GenericAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build()
        cls.adapter = GenericAdapter()

    def test_parses_decisions_from_the_directory(self):
        decisions = self.adapter.decisions(self.root)
        self.assertEqual([item.text for item in decisions], [
            "Barriers freeze instead of slowing",
            "The icon is not the mechanic",
        ])

    def test_parses_open_work_with_its_section(self):
        items = self.adapter.open_work(self.root)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].group, "Bugs")
        self.assertIn("triggers twice", items[0].text)
        self.assertEqual(items[1].group, "Improvements")

    def test_decision_files_are_not_also_units(self):
        paths = {unit.path for unit in self.adapter.units(self.root)}
        self.assertNotIn("docs/decisions/D-001.md", paths)

    def test_canonical_lists_the_front_doors(self):
        self.assertIn("docs/status.md", self.adapter.canonical(self.root))


class SkipDirsTest(unittest.TestCase):
    def test_an_adapter_can_prune_a_generated_tree(self):
        root = Path(tempfile.mkdtemp(prefix="repo-brief-skip-"))
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "runs" / "big").mkdir(parents=True)
        (root / "runs" / "big" / "note.md").write_text("# run\n", encoding="utf-8")

        class Lab(GenericAdapter):
            skip_dirs = SKIP_DIRS | {"runs"}

        adapter = Lab()
        self.assertNotIn("runs/big/note.md", {unit.path for unit in adapter.units(root)})
        self.assertEqual(adapter.detected(root).total_files, 1)


class ContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build()
        cls.adapter = GenericAdapter()
        cls.context = kernel.build_context(cls.root, cls.adapter, "barrier freeze")

    def test_ranks_the_barrier_source_first(self):
        self.assertEqual(self.context["units"][0]["path"], "godot/src/abilities/emeraldbarrier.gd")

    def test_surfaces_the_matching_decision(self):
        self.assertIn("Barriers freeze instead of slowing", [d["text"] for d in self.context["decisions"]])

    def test_surfaces_the_matching_open_loop(self):
        context = kernel.build_context(self.root, self.adapter, "triggers twice")
        texts = [item["text"] for item in context["open_work"]]
        self.assertTrue(any("triggers twice" in text for text in texts))

    def test_suppresses_a_weak_open_loop_match(self):
        # "barrier freeze" matches the decision on both words and the bug on one.
        # The threshold is what keeps the brief short.
        texts = [item["text"] for item in self.context["open_work"]]
        self.assertFalse(any("triggers twice" in text for text in texts))

    def test_maps_the_test_that_covers_the_source(self):
        self.assertIn("godot/tests/abilities/test_emeraldbarrier.gd", self.context["tests"])

    def test_reports_a_usage_line_inside_product_code(self):
        paths = [item["path"] for item in self.context["usage"]]
        self.assertTrue(paths)
        self.assertTrue(all(path.startswith("godot/src") for path in paths))


class DatedLogTest(unittest.TestCase):
    """The second shape: one decision log, one table backlog."""

    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_dated()
        cls.adapter = GenericAdapter()

    def test_reads_every_dated_entry(self):
        decisions = self.adapter.decisions(self.root)
        self.assertEqual(
            [item.text for item in decisions],
            ["Rush scales with hold time", "Barriers freeze instead of slowing"],
        )

    def test_labels_each_entry_with_its_date(self):
        self.assertEqual(
            [item.label for item in self.adapter.decisions(self.root)],
            ["2026-10-01", "2026-10-02"],
        )

    def test_reads_backlog_table_rows(self):
        items = self.adapter.open_work(self.root)
        self.assertEqual([item.text for item in items], ["A1 Rush hold tuning"])

    def test_drops_rows_whose_notes_say_done(self):
        texts = [item.text for item in self.adapter.open_work(self.root)]
        self.assertFalse(any("Barrier freeze" in text for text in texts))

    def test_reads_the_title_cell_not_the_whole_row(self):
        item = self.adapter.open_work(self.root)[0]
        self.assertIn("rematch", item.body)
        self.assertNotIn("rematch", item.text)

    def test_maps_a_test_that_preloads_the_source_by_resource_path(self):
        units = self.adapter.units(self.root)
        coverage = kernel.tests_for(units)
        self.assertEqual(
            coverage.get("godot/tests/player/test_rush.gd"),
            ["godot/src/player/rush.gd"],
        )

    def test_excluded_paths_are_not_units(self):
        class Narrow(GenericAdapter):
            def excluded(self, root):
                return {"godot/src/player/rush.gd"}

        paths = {unit.path for unit in Narrow().units(self.root)}
        self.assertNotIn("godot/src/player/rush.gd", paths)

    def test_a_struck_through_row_is_closed(self):
        texts = [item.text for item in self.adapter.open_work(self.root)]
        self.assertFalse(any("Old thing" in text for text in texts))


class ProfileDriftTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_dated()
        cls.fresh = detect(cls.root).to_dict()

    def test_a_matching_profile_has_no_drift(self):
        self.assertEqual(profile_drift(self.fresh, self.fresh), [])

    def test_the_written_profile_does_not_record_the_machine(self):
        root = fixture.build_dated()
        stored = json.loads(
            write_profile(root, detect(root)).read_text(encoding="utf-8")
        )["detected"]
        self.assertNotIn("root", stored)
        self.assertEqual(profile_drift(stored, detect(root).to_dict()), [])

    def test_a_moved_decision_log_is_drift(self):
        stored = dict(self.fresh, decisions_file="docs/decisions.md")
        self.assertIn("decisions_file", profile_drift(stored, self.fresh))

    def test_a_new_language_is_drift(self):
        stored = dict(self.fresh, languages=[{"language": "cobol", "files": 1}])
        self.assertIn("languages", profile_drift(stored, self.fresh))

    def test_a_new_archive_directory_is_drift(self):
        stored = dict(self.fresh, archived=["docs/archive"])
        self.assertIn("archived", profile_drift(stored, self.fresh))

    def test_changing_file_counts_is_not_drift(self):
        stored = dict(
            self.fresh,
            languages=[
                {"language": item["language"], "files": item["files"] + 100}
                for item in self.fresh["languages"]
            ],
            total_files=self.fresh["total_files"] + 100,
        )
        self.assertEqual(profile_drift(stored, self.fresh), [])


class StructureDigestTest(unittest.TestCase):
    """A changed comment must not make the committed map stale."""

    def setUp(self):
        self.root = fixture.build()
        self.helper = self.root / "godot/src/abilities/helper.gd"
        self.helper.write_text("extends Node\n\n## Helper for things.\nfunc go():\n\tpass\n")

    def _map(self, descriptions: bool) -> str:
        adapter = GenericAdapter()
        detected = adapter.detected(self.root)
        return render.render_map(
            detected,
            adapter.units(self.root),
            adapter.decisions(self.root),
            adapter.open_work(self.root),
            adapter.name,
            descriptions=descriptions,
        )

    def test_structure_map_omits_descriptions(self):
        self.assertNotIn("Helper for things.", self._map(False))

    def test_full_map_keeps_descriptions(self):
        self.assertIn("Helper for things.", self._map(True))

    def test_a_description_change_does_not_change_the_digest(self):
        before = render.structure_digest(self._map(False))
        self.helper.write_text("extends Node\n\n## Helper for other things.\nfunc go():\n\tpass\n")
        self.assertEqual(before, render.structure_digest(self._map(False)))

    def test_a_new_file_changes_the_digest(self):
        before = render.structure_digest(self._map(False))
        (self.root / "godot/src/abilities/other.gd").write_text("extends Node\n")
        self.assertNotEqual(before, render.structure_digest(self._map(False)))


if __name__ == "__main__":
    unittest.main()
