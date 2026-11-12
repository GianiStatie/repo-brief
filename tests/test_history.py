"""Importing Claude Code and OpenCode sessions into the optional log."""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief import cli, history, log  # noqa: E402

ROOT = Path(tempfile.mkdtemp(prefix="repo-brief-history-repo-"))


def build_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-history-repo-"))
    (root / "save.py").write_text("def save():\n    pass\n", encoding="utf-8")
    return root


def claude_entry(kind, uuid, stamp, content, **extra):
    entry = {
        "type": kind,
        "uuid": uuid,
        "sessionId": "s1",
        "timestamp": stamp,
        "cwd": str(ROOT),
        "message": {"content": content},
    }
    entry.update(extra)
    return entry


class Env(unittest.TestCase):
    def setUp(self):
        self.root = build_root()
        self.set_env("REPO_BRIEF_DATA_DIR", tempfile.mkdtemp(prefix="repo-brief-history-data-"))
        self.home = Path(tempfile.mkdtemp(prefix="repo-brief-history-home-"))
        self.set_env("CLAUDE_CONFIG_DIR", str(self.home))
        self.set_env("PATH", os.environ.get("PATH", ""))

    def set_env(self, name, value):
        previous = os.environ.get(name)
        self.addCleanup(
            lambda: os.environ.__setitem__(name, previous) if previous is not None else os.environ.pop(name, None)
        )
        os.environ[name] = value

    def write_claude(self, session="s1", cwd=None, entries=()):
        folder = self.home / "projects" / "-some-project"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("%s.jsonl" % session)
        lines = []
        for entry in entries:
            entry = dict(entry)
            entry["cwd"] = cwd or str(self.root)
            lines.append(json.dumps(entry))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def fake_opencode(self, sessions, exports):
        """A stand-in CLI: `session list` and `session export` read fixtures."""
        fixtures = Path(tempfile.mkdtemp(prefix="repo-brief-history-oc-"))
        (fixtures / "list.json").write_text(json.dumps(sessions), encoding="utf-8")
        for name, data in exports.items():
            (fixtures / ("%s.json" % name)).write_text(json.dumps(data), encoding="utf-8")
        bin_dir = Path(tempfile.mkdtemp(prefix="repo-brief-history-bin-"))
        script = bin_dir / "opencode"
        script.write_text(
            "#!/bin/sh\n"
            'if [ "$1 $2" = "session list" ]; then cat "$OC_FIXTURES/list.json"\n'
            'elif [ "$1 $2" = "session export" ]; then cat "$OC_FIXTURES/$3.json"\n'
            "else exit 1\n"
            "fi\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.set_env("PATH", "%s%s%s" % (bin_dir, os.pathsep, os.environ["PATH"]))
        self.set_env("OC_FIXTURES", str(fixtures))


class CleanTest(unittest.TestCase):
    def test_reminders_and_plumbing_are_not_questions(self):
        self.assertEqual(history.clean_question("<system-reminder>x</system-reminder>\nWho saves?"), "Who saves?")
        self.assertEqual(history.clean_question("[Request interrupted by user]"), "")
        self.assertEqual(history.clean_question("[structured-output-enforce] call it"), "")
        self.assertEqual(history.clean_question("<command-name>/clear</command-name>"), "")
        self.assertEqual(history.clean_question("<system-reminder>only</system-reminder>"), "")

    def test_input_drops_file_contents_and_long_values(self):
        cleaned = history.clean_input(
            {
                "file_path": str(ROOT / "save.py"),
                "content": "SECRET BODY",
                "new_string": "SECRET BODY",
                "pattern": "save",
                "lines": "line one\nline two",
                "huge": "x" * 500,
            },
            ROOT,
        )
        self.assertEqual(cleaned["file_path"], "save.py")
        self.assertNotIn("content", cleaned)
        self.assertNotIn("new_string", cleaned)
        self.assertEqual(cleaned["lines"], "line one")
        self.assertEqual(len(cleaned["huge"]), 203)  # 200 plus "..."


class ClaudeTest(Env):
    def entries(self):
        return [
            claude_entry("user", "u1", "2026-11-01T10:00:00.000Z", "How does saving work?"),
            {
                "type": "assistant",
                "uuid": "a1",
                "sessionId": "s1",
                "timestamp": "2026-11-01T10:00:01.000Z",
                "cwd": str(self.root),
                "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Grep",
                                         "input": {"pattern": "save", "path": str(self.root)}}]},
            },
            {
                "type": "user",
                "uuid": "u2",
                "sessionId": "s1",
                "timestamp": "2026-11-01T10:00:03.000Z",
                "cwd": str(self.root),
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                         "content": "x" * 50, "is_error": False}]},
            },
            claude_entry("user", "u3", "2026-11-01T10:00:04.000Z", "[Request interrupted by user]"),
            claude_entry("user", "u4", "2026-11-01T10:00:05.000Z", "<system-reminder>mode</system-reminder>"),
            claude_entry("user", "u5", "2026-11-01T10:01:00.000Z", "Where is the save written?"),
            {
                "type": "assistant",
                "uuid": "a2",
                "sessionId": "s1",
                "timestamp": "2026-11-01T10:01:01.000Z",
                "cwd": str(self.root),
                "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Write",
                                         "input": {"file_path": str(self.root / "save.py"),
                                                   "content": "SECRET BODY"}}]},
            },
            {
                "type": "user",
                "uuid": "u6",
                "sessionId": "s1",
                "timestamp": "2026-11-01T10:01:02.000Z",
                "cwd": str(self.root),
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t2",
                                         "content": "denied", "is_error": True}]},
            },
        ]

    def test_only_real_prompts_become_questions(self):
        self.write_claude(entries=self.entries())
        questions = list(history.claude_questions(history.claude_project(self.root), self.root))
        self.assertEqual([item.question for item in questions], [
            "How does saving work?", "Where is the save written?",
        ])
        self.assertEqual([item.turns for item in questions], [1, 1])
        self.assertEqual(questions[0].duration_ms, 3000)  # prompt to last tool result

    def test_a_call_keeps_its_arguments_not_the_file(self):
        self.write_claude(entries=self.entries())
        questions = list(history.claude_questions(history.claude_project(self.root), self.root))
        first = questions[0].calls[0]
        self.assertEqual((first["tool"], first["input"]["path"]), ("Grep", "."))
        self.assertEqual(first["duration_ms"], 2000)
        write = questions[1].calls[0]
        self.assertNotIn("content", write["input"])
        self.assertEqual(write["input"]["file_path"], "save.py")
        self.assertTrue(write["is_error"])

    def test_import_writes_them_once_and_the_second_run_skips(self):
        self.write_claude(entries=self.entries())
        counts = history.import_sessions(self.root, source="claude")
        self.assertTrue(counts["created"])
        self.assertEqual(counts["sources"]["claude-code"], {"questions": 2, "updated": 0, "calls": 2})
        rows = log.recent(self.root)
        self.assertEqual([row["source"] for row in rows], ["claude-code", "claude-code"])
        self.assertEqual(log.calls_for(self.root, rows[0]["id"])[0]["tool"], "Write")
        again = history.import_sessions(self.root, source="claude")
        self.assertEqual(again["sources"]["claude-code"], {"questions": 0, "updated": 2, "calls": 2})
        self.assertEqual(len(log.recent(self.root)), 2)
        # Refreshing rewrites the calls rather than adding a second set.
        self.assertEqual(len(log.calls_for(self.root, log.recent(self.root)[0]["id"])), 1)

    def test_a_sessions_own_asks_are_not_imported(self):
        log.enable(self.root)
        log.record_ask(self.root, "asked through repo-brief", "answer", session_id="s1")
        self.write_claude(entries=self.entries())
        counts = history.import_sessions(self.root, source="claude")
        self.assertEqual(counts["sources"]["claude-code"]["questions"], 0)


class OpenCodeTest(Env):
    def fixtures(self, session="ses_1"):
        question = {
            "id": "m1", "type": "user", "time": {"created": 1794000000000},
            "text": "Which file saves the game?",
        }
        answer = {
            "id": "m2", "type": "assistant", "time": {"created": 1794000005000},
            "cost": 0.01, "model": {"id": "claude-x"}, "content": [
                {"type": "tool", "name": "read", "time": {"created": 1794000001000},
                 "native": {"sessionID": "s1"},
                 "state": {"status": "completed", "input": {"path": str(self.root / "save.py")},
                           "content": [{"type": "text", "text": "ok"}]}},
                {"type": "tool", "name": "edit", "time": {"created": 1794000002000},
                 "native": {"sessionID": "s1"},
                 "state": {"status": "error", "error": "denied",
                           "input": {"path": str(self.root / "save.py"),
                                     "oldString": "A", "newString": "SECRET BODY"},
                           "content": [{"type": "text", "text": "no"}]}},
            ],
        }
        sessions = [{"id": session, "directory": str(self.root), "title": "harness"}]
        exports = {session: {"info": {"id": session, "title": "harness"}, "messages": [question, answer]}}
        return sessions, exports

    def test_a_question_keeps_its_calls_and_the_file_contents_stay_out(self):
        self.fake_opencode(*self.fixtures())
        counts = history.import_sessions(self.root, source="opencode")
        self.assertEqual(
            counts["sources"]["opencode"], {"questions": 1, "updated": 0, "calls": 2}
        )
        row = log.recent(self.root)[0]
        self.assertEqual((row["source"], row["client"], row["turns"]), ("opencode", "opencode", 1))
        self.assertAlmostEqual(row["cost_usd"], 0.01)
        calls = log.calls_for(self.root, row["id"])
        self.assertEqual([call["tool"] for call in calls], ["read", "edit"])
        self.assertEqual(json.loads(calls[0]["input"])["path"], "save.py")
        self.assertNotIn("SECRET BODY", calls[1]["input"])
        self.assertEqual(calls[1]["is_error"], 1)
        self.assertEqual(calls[1]["result_bytes"], history._size([{"type": "text", "text": "no"}]))

    def test_opencode_covers_its_claude_sessions(self):
        self.write_claude(entries=ClaudeTest.entries(self))
        self.fake_opencode(*self.fixtures())
        counts = history.import_sessions(self.root)
        self.assertEqual(counts["sources"]["opencode"]["questions"], 1)
        self.assertEqual(counts["sources"]["claude-code"]["questions"], 0)

    def test_a_session_imported_while_running_is_completed_later(self):
        sessions, exports = self.fixtures()
        self.fake_opencode(sessions, exports)
        history.import_sessions(self.root, source="opencode")
        first = log.recent(self.root)[0]
        self.assertEqual(first["turns"], 1)
        # The session went on: the same question, one more assistant step.
        more = exports["ses_1"] = json.loads(json.dumps(exports["ses_1"]))
        more["messages"].append({
            "id": "m3", "type": "assistant", "time": {"created": 1794000010000},
            "cost": 0.02, "model": {"id": "claude-x"},
            "content": [{"type": "text", "text": "It saves in save.py."}],
        })
        self.fake_opencode(sessions, exports)
        counts = history.import_sessions(self.root, source="opencode")
        self.assertEqual(counts["updated"], 1)
        rows = log.recent(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["turns"], 2)
        self.assertAlmostEqual(rows[0]["cost_usd"], 0.03)
        self.assertEqual(len(log.calls_for(self.root, rows[0]["id"])), 2)

    def test_a_missing_opencode_is_a_note_not_a_failure(self):
        self.set_env("PATH", str(Path(tempfile.mkdtemp())))
        counts = history.import_sessions(self.root, source="opencode")
        self.assertIn("not installed", counts["notes"][0])
        self.assertEqual(counts["sources"], {})


class CliTest(Env):
    def test_log_import_from_the_command_line(self):
        self.write_claude(entries=ClaudeTest.entries(self))
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--root", str(self.root), "log", "import", "--from", "claude"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("claude-code", out.getvalue())
        self.assertIn("imported 2 question(s) and 2 tool call(s)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
