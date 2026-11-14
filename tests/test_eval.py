"""The replay: a question is answered against the tree it was asked on."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief import evaluate, log  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402

FIRST = {
    "project.godot": '[application]\nconfig/name="replay"\n',
    "godot/src/stand.gd": (
        "class_name Stand\nextends Node\n\n## Rush the target.\nfunc rush() -> void:\n\tpass\n"
    ),
    "docs/index.md": "# Index\n\nStart here.\n",
}
LATER = {
    "godot/src/requiem.gd": "class_name Requiem\nextends Node\n\nfunc awaken() -> void:\n\tpass\n",
}


def _commit(root: Path, message: str, at: str) -> str:
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at})
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", message],
        cwd=root,
        check=True,
        env=env,
    )
    found = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    )
    return found.stdout.strip()


def build() -> tuple[Path, str]:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-eval-"))
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for rel, text in FIRST.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    # No offset, as the log writes `at`: git reads both in the machine's own zone.
    first = _commit(root, "first", "2026-01-01T10:00:00")
    for rel, text in LATER.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _commit(root, "second", "2026-01-01T12:00:00")
    return root, first


class QueryTest(unittest.TestCase):
    def test_reads_a_query_from_a_shell_brief(self):
        calls = [
            {"tool": "Bash", "input": '{"command": "./tools/dev.sh context --brief \\"stand rush\\""}'},
            {"tool": "Edit", "input": '{"file_path": "godot/src/stand.gd"}'},
        ]
        self.assertEqual(
            evaluate.queries_for(calls, "how does the stand rush work"),
            {"agent": "stand rush", "user": "how does the stand rush work"},
        )

    def test_reads_a_query_from_the_mcp_tool(self):
        calls = [{"tool": "mcp__repo-brief__repo_context", "input": '{"query": "requiem awaken"}'}]
        self.assertEqual(evaluate.queries_for(calls, "told")["agent"], "requiem awaken")

    def test_the_question_is_the_fallback_when_no_brief_was_called(self):
        self.assertEqual(evaluate.queries_for([], "why")["agent"], "")

    def test_reads_a_query_from_the_make_target(self):
        for command, query in (
            ('make context ARGS="--brief" q="stand rush"', "stand rush"),
            ("cd godot && make context q='za warudo'", "za warudo"),
            ("make context q=requiem", "requiem"),
        ):
            calls = [{"tool": "Bash", "input": {"command": command}}]
            self.assertEqual(evaluate.queries_for(calls, "typed")["agent"], query, command)

    def test_edit_and_read_paths_are_kept_apart(self):
        calls = [
            {"tool": "Edit", "input": '{"file_path": "godot/src/stand.gd"}'},
            {"tool": "Read", "input": '{"file_path": "docs/index.md"}'},
            {"tool": "Grep", "input": '{"pattern": "stand"}'},
        ]
        self.assertEqual(evaluate._paths(calls, evaluate.EDIT_TOOLS), ["godot/src/stand.gd"])
        self.assertEqual(evaluate._paths(calls, evaluate.READ_TOOLS), ["docs/index.md"])

    def test_a_file_outside_the_repository_is_never_graded(self):
        tree = Path(tempfile.mkdtemp(prefix="repo-brief-eval-tree-"))
        elsewhere = Path(tempfile.mkdtemp(prefix="repo-brief-eval-elsewhere-"))
        (tree / "godot/src").mkdir(parents=True)
        (tree / "godot/src/stand.gd").write_text("class_name Stand\n", encoding="utf-8")
        outside = elsewhere / "speedwagon.md"
        outside.write_text("# Speedwagon Foundation\n", encoding="utf-8")
        # It exists on disk, but no brief of this tree could have named it.
        paths = [str(outside), "../speedwagon.md", "godot/src/stand.gd"]
        self.assertEqual(evaluate._existing(tree, paths), ["godot/src/stand.gd"])


class SummaryTest(unittest.TestCase):
    def test_the_read_guard_scores_the_plan_against_what_was_read(self):
        row = {
            "style": "agent",
            "plan": ["godot/src/stand.gd"],
            "plan_items": [{"path": "godot/src/stand.gd", "bytes": 120}],
            "named": ["godot/src/stand.gd"],
            "bytes": 120,
            "edited": ["godot/src/stand.gd"],
            "read": ["docs/index.md"],
        }
        missed = evaluate.summarize([row])["agent"]
        self.assertEqual((missed["read_hits"], missed["read"]), (0, 1))
        found = evaluate.summarize([dict(row, read=["godot/src/stand.gd"])])["agent"]
        self.assertEqual((found["read_hits"], found["read"]), (1, 1))


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="repo-brief-eval-log-"))
        self.previous = os.environ.get("REPO_BRIEF_DATA_DIR")
        os.environ["REPO_BRIEF_DATA_DIR"] = str(self.data)
        self.root, self.first = build()

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("REPO_BRIEF_DATA_DIR", None)
        else:
            os.environ["REPO_BRIEF_DATA_DIR"] = self.previous
        import shutil

        shutil.rmtree(self.data, ignore_errors=True)

    def _record(self, question: str, at: str, calls: list, external_id: str = "question-one") -> None:
        log.enable(self.root)
        log.record_ask(
            self.root,
            question,
            "",
            session_id="session-one",
            status="imported",
            client="claude",
            source="claude",
            external_id=external_id,
            at=at,
            calls=calls,
        )

    def test_a_question_is_replayed_on_the_commit_of_the_moment(self):
        self._record(
            "how does the stand rush work",
            "2026-01-01T11:00:00",
            [
                {
                    "tool": "Bash",
                    "input": {"command": './tools/dev.sh context --brief "stand rush"'},
                },
                {"tool": "Edit", "input": {"file_path": "godot/src/stand.gd"}},
            ],
        )
        result = evaluate.run(self.root, GenericAdapter())
        agent = [row for row in result["rows"] if row["style"] == "agent"]
        self.assertEqual(len(agent), 1)
        self.assertEqual(agent[0]["commit"], self.first)
        self.assertIn("godot/src/stand.gd", agent[0]["plan"])
        self.assertEqual(agent[0]["edited"], ["godot/src/stand.gd"])
        # The file added by the second commit did not exist when the question ran.
        self.assertNotIn("godot/src/requiem.gd", agent[0]["plan"])

    def test_each_commit_is_ranked_against_its_own_tree(self):
        # A third commit drops the Stand, so the first and the last tree each
        # hold a file the other lacks: whichever is replayed first, a shared
        # adapter would rank the other against the wrong file list.
        (self.root / "godot/src/stand.gd").unlink()
        last = _commit(self.root, "third", "2026-01-01T14:00:00")
        self._record(
            "how does the stand rush work",
            "2026-01-01T11:00:00",
            [
                {"tool": "Bash", "input": {"command": './tools/dev.sh context --brief "stand rush"'}},
                {"tool": "Edit", "input": {"file_path": "godot/src/stand.gd"}},
            ],
            external_id="question-stand",
        )
        self._record(
            "when does requiem awaken",
            "2026-01-01T15:00:00",
            [
                {"tool": "Bash", "input": {"command": './tools/dev.sh context --brief "requiem awaken"'}},
                {"tool": "Edit", "input": {"file_path": "godot/src/requiem.gd"}},
            ],
            external_id="question-requiem",
        )
        result = evaluate.run(self.root, GenericAdapter())
        plans = {row["commit"]: row["plan"] for row in result["rows"] if row["style"] == "agent"}
        self.assertEqual(set(plans), {self.first, last})
        self.assertIn("godot/src/stand.gd", plans[self.first])
        self.assertIn("godot/src/requiem.gd", plans[last])

    def test_a_question_without_a_commit_is_counted_not_guessed(self):
        self._record(
            "who",
            "2025-12-01T00:00:00",
            [{"tool": "Edit", "input": {"file_path": "godot/src/stand.gd"}}],
        )
        result = evaluate.run(self.root, GenericAdapter())
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["skipped"]["no_commit"], 1)


if __name__ == "__main__":
    unittest.main()
