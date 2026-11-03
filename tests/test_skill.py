"""The generated skill and the doc conventions it reads."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from repobrief import archive as archive_mod  # noqa: E402
from repobrief import cli  # noqa: E402
from repobrief import graph as graph_mod  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.detect import CAPTURE_DIR, detect  # noqa: E402


def run(argv):
    return cli.main(argv)


class SkillTest(unittest.TestCase):
    def setUp(self):
        self.root = fixture.build_dated()

    def test_install_writes_the_shipped_skill(self):
        self.assertEqual(run(["--root", str(self.root), "skill"]), 0)
        installed = self.root / cli.skill_path("document")
        self.assertTrue(installed.is_file())
        self.assertEqual(
            installed.read_text(encoding="utf-8"),
            cli.skill_source("document").read_text(encoding="utf-8"),
        )

    def test_check_passes_on_a_fresh_copy(self):
        run(["--root", str(self.root), "skill"])
        self.assertEqual(run(["--root", str(self.root), "skill", "--check"]), 0)

    def test_check_fails_when_the_copy_was_edited(self):
        run(["--root", str(self.root), "skill"])
        (self.root / cli.skill_path("document")).write_text("edited\n", encoding="utf-8")
        self.assertEqual(run(["--root", str(self.root), "skill", "--check"]), 1)

    def test_preflight_fails_when_the_copy_drifted(self):
        run(["--root", str(self.root), "scaffold"])
        run(["--root", str(self.root), "index"])
        (self.root / cli.skill_path("document")).write_text("edited\n", encoding="utf-8")
        self.assertEqual(run(["--root", str(self.root), "preflight"]), 1)

    def test_preflight_passes_with_the_shipped_copy(self):
        run(["--root", str(self.root), "scaffold"])
        run(["--root", str(self.root), "index"])
        self.assertEqual(run(["--root", str(self.root), "preflight"]), 0)

    def test_install_writes_both_skills(self):
        self.assertEqual(run(["--root", str(self.root), "skill"]), 0)
        for name in cli.SKILL_NAMES:
            self.assertTrue((self.root / cli.skill_path(name)).is_file())

    def test_check_fails_when_the_retrieve_copy_was_edited(self):
        run(["--root", str(self.root), "skill"])
        (self.root / cli.skill_path("retrieve")).write_text("edited\n", encoding="utf-8")
        self.assertEqual(run(["--root", str(self.root), "skill", "--check"]), 1)

    def test_preflight_warns_but_does_not_fail_when_retrieve_is_missing(self):
        run(["--root", str(self.root), "scaffold"])
        run(["--root", str(self.root), "index"])
        (self.root / cli.skill_path("retrieve")).unlink()
        self.assertEqual(run(["--root", str(self.root), "preflight"]), 0)

    def test_preflight_fails_when_the_retrieve_copy_drifted(self):
        run(["--root", str(self.root), "scaffold"])
        run(["--root", str(self.root), "index"])
        (self.root / cli.skill_path("retrieve")).write_text("edited\n", encoding="utf-8")
        self.assertEqual(run(["--root", str(self.root), "preflight"]), 1)


class WritingTest(unittest.TestCase):
    def setUp(self):
        self.root = fixture.build_dated()

    def test_writing_names_the_detected_layout(self):
        self.assertEqual(run(["--root", str(self.root), "writing"]), 0)
        text = (self.root / cli.WRITING_DOC).read_text(encoding="utf-8")
        detected = detect(self.root)
        self.assertIn(detected.decisions_file, text)
        self.assertIn(CAPTURE_DIR, text)

    def test_writing_is_not_overwritten_without_force(self):
        run(["--root", str(self.root), "writing"])
        path = self.root / cli.WRITING_DOC
        path.write_text("mine\n", encoding="utf-8")
        self.assertEqual(run(["--root", str(self.root), "writing"]), 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "mine\n")
        self.assertEqual(run(["--root", str(self.root), "writing", "--force"]), 0)
        self.assertNotEqual(path.read_text(encoding="utf-8"), "mine\n")

    def test_scaffold_writes_both(self):
        self.assertEqual(run(["--root", str(self.root), "scaffold"]), 0)
        self.assertTrue((self.root / cli.WRITING_DOC).is_file())
        self.assertTrue((self.root / cli.skill_path("document")).is_file())


    def test_writing_is_not_offered_as_archivable(self):
        run(["--root", str(self.root), "scaffold"])
        adapter = GenericAdapter()
        units = adapter.units(self.root)
        edges, _areas = graph_mod.build(
            self.root, adapter, units, adapter.detected(self.root).source_root, {}
        )
        found = {
            candidate.path
            for candidate in archive_mod.candidates(
                self.root, adapter, units, edges, canonical=[]
            )
        }
        self.assertNotIn(cli.WRITING_DOC, found)

    def test_preflight_requires_conventions_when_the_skill_is_installed(self):
        run(["--root", str(self.root), "scaffold"])
        (self.root / cli.WRITING_DOC).unlink()
        run(["--root", str(self.root), "index"])
        self.assertEqual(run(["--root", str(self.root), "preflight"]), 1)


class CaptureTierTest(unittest.TestCase):
    def setUp(self):
        self.root = fixture.build_dated()
        (self.root / CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
        (self.root / CAPTURE_DIR / "2026-11-02.md").write_text(
            "# Capture\n\n## C-2026-11-02-01 — Thing\n\n> \"do the thing\"\n",
            encoding="utf-8",
        )

    def test_capture_is_not_a_unit(self):
        paths = {unit.path for unit in GenericAdapter().units(self.root)}
        self.assertNotIn("%s/2026-11-02.md" % CAPTURE_DIR, paths)

    def test_capture_is_not_a_source(self):
        paths = {source.path for source in GenericAdapter().sources(self.root)}
        self.assertNotIn("%s/2026-11-02.md" % CAPTURE_DIR, paths)


if __name__ == "__main__":
    unittest.main()
