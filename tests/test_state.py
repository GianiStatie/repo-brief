"""The structural commands: init, update, trust, the catalog, the overview, the index."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief import catalog, cli, index, log, overview, trust  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.contract import Unit  # noqa: E402

FILES = {
    "project.godot": '[application]\nconfig/name="demo"\nrun/main_scene="res://src/main.tscn"\n',
    "godot/src/player.gd": (
        "class_name Player\n"
        "extends Node\n"
        "\n"
        "## Move the player.\n"
        "func move() -> void:\n"
        "\tpass\n"
    ),
    "tools/check_docs.py": '"""Check the docs front doors."""\nimport argparse\n'
    "p = argparse.ArgumentParser()\np.add_argument('--strict', action='store_true')\n",
    "tools/generate_icons.py": '"""Generate the button icons."""\n',
    "tools/bench.py": '"""Benchmark the player on the levels."""\nimport argparse\n'
    "p = argparse.ArgumentParser()\np.add_argument('--level')\n",
    "Makefile": "test: ## Run the tests\n\ttrue\nrelease: ## Publish the build\n\ttrue\n",
    "docs/index.md": "# Demo\n\nA small game.\n",
    "docs/meta/decisions.md": "# Decisions\n\n## 2026-10-01 — Move with WASD\n\n**Status:** Open.\n",
}


def build() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-state-"))
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    return root


class TrustTest(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.mkdtemp(prefix="repo-brief-config-")
        self.addCleanup(lambda: os.environ.pop("REPO_BRIEF_CONFIG_DIR", None))
        os.environ["REPO_BRIEF_CONFIG_DIR"] = self.config
        self.root = build()

    def test_trust_round_trip(self):
        self.assertFalse(trust.is_trusted(self.root))
        trust.trust(self.root)
        self.assertTrue(trust.is_trusted(self.root))
        trust.untrust(self.root)
        self.assertFalse(trust.is_trusted(self.root))

    def test_trust_is_machine_local_not_in_the_repo(self):
        trust.trust(self.root)
        self.assertTrue((Path(self.config) / "trust.json").is_file())
        self.assertFalse((self.root / ".repo-brief" / "trust.json").exists())


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.root = build()
        self.detected = GenericAdapter().detected(self.root)

    def test_groups_follow_what_a_script_does(self):
        groups = {
            item["id"]: item["group"] for item in catalog.scan(self.root, self.detected)
        }
        self.assertEqual(groups["script:tools/check_docs.py"], "inspect")
        self.assertEqual(groups["script:tools/bench.py"], "debug")
        self.assertEqual(groups["script:tools/generate_icons.py"], "manual")
        self.assertEqual(groups["make:test"], "dev")
        self.assertEqual(groups["make:release"], "manual")

    def test_args_are_read_from_argparse(self):
        found = {item["id"]: item for item in catalog.scan(self.root, self.detected)}
        self.assertIn("--strict", found["script:tools/check_docs.py"]["args"])

    def test_a_judgement_survives_a_rescan(self):
        path, data, _ = catalog.write(self.root, self.detected)
        self.assertTrue(path.is_file())
        catalog.set_group(self.root, "script:tools/check_docs.py", "manual")
        _path, data, _ = catalog.write(self.root, self.detected)
        found = {item["id"]: item for item in data["commands"]}
        self.assertEqual(found["script:tools/check_docs.py"]["group"], "manual")


class OverviewTest(unittest.TestCase):
    def setUp(self):
        self.root = build()
        self.adapter = GenericAdapter()
        self.detected = self.adapter.detected(self.root)
        self.units = self.adapter.units(self.root)

    def test_it_names_the_facts_and_keeps_the_written_half(self):
        path, _ = overview.write(self.root, self.adapter, self.detected, self.units)
        text = path.read_text(encoding="utf-8")
        self.assertIn("## Summary", text)
        self.assertIn("## Stack", text)
        self.assertIn("## Areas", text)
        self.assertIn("docs/meta/decisions.md", text)
        written = text.replace(
            "Not written yet. An agent with a runner fills this section",
            "This game teaches timing.",
        )
        path.write_text(written, encoding="utf-8")
        overview.write(self.root, self.adapter, self.detected, self.units)
        self.assertIn("This game teaches timing.", path.read_text(encoding="utf-8"))


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.root = build()
        self.adapter = GenericAdapter()
        self.addCleanup(lambda: index.Index(self.root).close())

    def test_units_match_the_unindexed_walk(self):
        units, state, stats = index.ensure(self.root, self.adapter)
        try:
            full = {unit.path: unit.kind for unit in self.adapter.units(self.root)}
            fast = {unit.path: unit.kind for unit in units}
            self.assertEqual(fast, full)
            self.assertGreater(stats["parsed"], 0)
        finally:
            state.close()

    def test_only_a_changed_file_is_parsed_again(self):
        units, state, _ = index.ensure(self.root, self.adapter)
        state.close()
        target = self.root / "godot/src/player.gd"
        target.write_text(target.read_text(encoding="utf-8") + "\n# note\n", encoding="utf-8")
        _units, state, stats = index.ensure(self.root, self.adapter)
        try:
            self.assertEqual(stats["parsed"], 1)
            self.assertGreater(stats["kept"], 0)
        finally:
            state.close()

    def test_camel_case_words_are_indexed(self):
        self.assertIn("hermit", index.words_for("HermitPurple"))
        self.assertIn("purple", index.words_for("HermitPurple"))

    def test_candidate_lookup_is_word_or_prefix(self):
        _units, state, _ = index.ensure(self.root, self.adapter)
        try:
            self.assertIn("godot/src/player.gd", state.candidate_paths(["play"]))
            self.assertNotIn("godot/src/player.gd", state.candidate_paths(["xyzzy"]))
        finally:
            state.close()


class LogTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="repo-brief-data-")
        self.addCleanup(lambda: os.environ.pop("REPO_BRIEF_DATA_DIR", None))
        os.environ["REPO_BRIEF_DATA_DIR"] = self.data
        self.root = build()

    def test_an_ask_is_recorded_outside_the_repo(self):
        log.record(self.root, "what moves the player?", "a script", session_id="ses_1",
                   model="fake", duration_ms=1200, cost_usd=0.001)
        recent = log.recent(self.root)
        self.assertEqual(recent[0]["question"], "what moves the player?")
        self.assertEqual(recent[0]["session_id"], "ses_1")
        self.assertFalse(str(log.path_for(self.root)).startswith(str(self.root)))
        summary = log.summary(self.root)
        self.assertEqual(summary["asks"], 1)
        self.assertAlmostEqual(summary["cost_usd"], 0.001)


class InitTest(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.mkdtemp(prefix="repo-brief-config-")
        self.addCleanup(lambda: os.environ.pop("REPO_BRIEF_CONFIG_DIR", None))
        os.environ["REPO_BRIEF_CONFIG_DIR"] = self.config
        self.root = build()

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--root", str(self.root), *argv])
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    def test_local_init_writes_nothing_into_git(self):
        self.run_cli("init", "--local", "--trust")
        self.assertTrue((self.root / ".repo-brief" / "adapter.py").is_file())
        self.assertTrue((self.root / ".repo-brief" / "map.md").is_file())
        self.assertTrue((self.root / ".repo-brief" / "overview.md").is_file())
        self.assertTrue((self.root / ".repo-brief" / "tools.json").is_file())
        marker = json.loads((self.root / ".repo-brief" / "local.json").read_text())
        self.assertIn("docs/meta", marker["include"])
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertNotIn(".repo-brief/", status)
        self.assertNotIn("docs/meta/", status)

    def test_a_local_decision_log_is_visible(self):
        self.run_cli("init", "--local", "--trust")
        writing = (self.root / "docs/meta/writing.md").read_text(encoding="utf-8")
        self.assertIn("docs/meta/decisions.md", writing)
        output = self.run_cli("preflight")
        self.assertNotIn("no decision log detected", output)

    def test_update_refreshes_and_reports(self):
        self.run_cli("init", "--local", "--trust")
        output = self.run_cli("update")
        self.assertIn("index:", output)
        self.assertIn("overview: unchanged", output)


if __name__ == "__main__":
    unittest.main()
