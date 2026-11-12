"""The OpenCode runner: its config, its command, its event stream, a fake CLI.

No model is called. `parse_events` and `_calls` are pure functions; the rest
runs against a stand-in `opencode` executable that records its arguments and
environment and replays a recorded event stream.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.serve import prompt, settings as settings_mod, tools  # noqa: E402
from repobrief.serve.runners import RunnerUnavailable, opencode  # noqa: E402


def events(*items) -> list:
    return [json.dumps(item) + "\n" for item in items]


TOOL = {
    "type": "tool_use", "sessionID": "ses_1", "timestamp": 1794481327000,
    "part": {
        "type": "tool", "tool": "repo_repo_brief_context",
        "state": {"status": "completed", "input": {"query": "Player move"},
                  "output": "a read plan", "time": {"start": 1000, "end": 1250}},
    },
}
ANSWER = {
    "type": "tool_use", "sessionID": "ses_1", "timestamp": 1794481329000,
    "part": {
        "type": "tool", "tool": "repo_repo_brief_answer",
        "state": {
            "status": "completed",
            "input": {
                "answer": "It moves.",
                "references": [{"path": "godot/src/player.gd", "lines": "1-6", "note": "the class"}],
                "gaps": ["whether it runs on mobile"],
            },
            "output": "Recorded; stop here.",
        },
    },
}
STEP = {
    "type": "step_finish", "sessionID": "ses_1", "timestamp": 1794481330000,
    "part": {"type": "step-finish", "reason": "stop", "cost": 0.01, "tokens": {}},
}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repo-brief-oc-"))
        (self.root / "godot").mkdir()
        (self.root / "godot" / "player.gd").write_text("class_name Player\n", encoding="utf-8")
        self.settings = settings_mod.load(self.root)
        self.adapter = GenericAdapter()

    def runner(self, **overrides):
        chosen = dataclasses.replace(self.settings, **overrides) if overrides else self.settings
        specs = tools.agent_specs(self.root, self.adapter, trusted=True)
        system = prompt.build(self.root, "developer", ["brief"], structured=False)
        return opencode.OpenCodeRunner(self.root, chosen, specs, system)


class ConfigTest(Fixture):
    def test_the_toolbox_is_a_local_server_and_the_answer_is_not_wrapped(self):
        config = self.runner().config()
        server = config["mcp"]["servers"]["repo"]
        self.assertEqual(server["type"], "local")
        self.assertFalse(server["codemode"])
        self.assertEqual(server["command"][-2:], ["--toolbox", "ask"])
        self.assertIn("serve", server["command"])

    def test_the_agent_may_read_and_use_the_toolbox_and_nothing_else(self):
        config = self.runner().config()
        rules = config["permissions"]
        self.assertEqual(rules[0], {"action": "*", "resource": "*", "effect": "deny"})
        allowed = {(rule["action"], rule["resource"]) for rule in rules if rule["effect"] == "allow"}
        for expected in (("read", "*"), ("glob", "*"), ("grep", "*"), ("repo_*", "*")):
            self.assertIn(expected, allowed)
        self.assertNotIn("shell", {rule["action"] for rule in rules if rule["effect"] == "allow"})
        self.assertEqual(rules[-1], {"action": "external_directory", "resource": "*", "effect": "deny"})
        self.assertEqual(config["agents"]["repo-brief-ask"]["permissions"], rules)
        self.assertIn("repo_brief_answer", config["agents"]["repo-brief-ask"]["system"])

    def test_the_environment_hides_the_repositorys_own_configuration(self):
        env = self.runner().environment()
        self.assertEqual(env["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")
        self.assertEqual(env["OPENCODE_DISABLE_CLAUDE_CODE"], "1")
        injected = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(injected["permissions"][0]["effect"], "deny")


class CommandTest(Fixture):
    def test_a_run_is_standalone_json_and_archived(self):
        command = self.runner().command("/usr/local/bin/opencode", "how does it move?")
        self.assertEqual(command[0], "/usr/local/bin/opencode")
        for flag in ("--standalone", "--archived", "--format", "json", "--agent", "repo-brief-ask"):
            self.assertIn(flag, command)
        self.assertNotIn("--model", command)  # the Claude model name is not OpenCode's
        self.assertEqual(command[-1], "how does it move?")

    def test_a_provider_qualified_model_and_a_session_are_passed(self):
        command = self.runner(model="ollama-cloud/deepseek-v4.1-flash:1017").command(
            "opencode", "q", session_id="ses_9"
        )
        self.assertIn("--model", command)
        self.assertEqual(command[command.index("--model") + 1], "ollama-cloud/deepseek-v4.1-flash:1017")
        self.assertEqual(command[command.index("--session") + 1], "ses_9")

    def test_no_opencode_on_path_is_explained(self):
        previous = os.environ.get("PATH")
        os.environ["PATH"] = tempfile.mkdtemp()
        self.addCleanup(lambda: os.environ.__setitem__("PATH", previous))
        with self.assertRaises(RunnerUnavailable):
            asyncio.run(self.runner().ask("q"))


class ParseTest(Fixture):
    def test_the_answer_tool_beats_the_last_text(self):
        found = opencode.parse_events(events(
            {"type": "step_start", "sessionID": "ses_1", "part": {}},
            TOOL,
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "working..."}},
            ANSWER,
            STEP,
        ))
        self.assertEqual(found["answer"], "It moves.")
        self.assertEqual(found["references"][0]["path"], "godot/src/player.gd")
        self.assertEqual(found["gaps"], ["whether it runs on mobile"])
        self.assertEqual((found["session"], found["turns"]), ("ses_1", 1))
        self.assertAlmostEqual(found["cost"], 0.01)

    def test_without_the_answer_tool_the_last_text_is_the_answer(self):
        found = opencode.parse_events(events(
            {"type": "text", "sessionID": "ses_2", "part": {"type": "text", "text": "first"}},
            {"type": "text", "sessionID": "ses_2", "part": {"type": "text", "text": "It moves."}},
        ))
        self.assertEqual(found["answer"], "It moves.")
        self.assertEqual(found["references"], [])

    def test_the_calls_leave_out_the_answer_and_keep_the_rest(self):
        calls = self.runner()._calls(events(TOOL, ANSWER))
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["tool"], "repo_repo_brief_context")
        self.assertEqual(call["input"], {"query": "Player move"})
        self.assertEqual((call["result_bytes"], call["duration_ms"]), (len("a read plan"), 250))
        self.assertFalse(call["is_error"])


class AskTest(Fixture):
    def fake(self, body: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="repo-brief-oc-bin-"))
        path = directory / "opencode"
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        previous = os.environ.get("PATH")
        os.environ["PATH"] = "%s%s%s" % (directory, os.pathsep, previous or "")
        self.addCleanup(lambda: os.environ.__setitem__("PATH", previous))
        return path

    def paths(self):
        work = Path(tempfile.mkdtemp(prefix="repo-brief-oc-run-"))
        return work / "args.txt", work / "env.txt", work / "events.jsonl"

    def test_a_run_returns_its_answer_calls_and_progress(self):
        args, env, stream = self.paths()
        stream.write_text("".join(events(TOOL, ANSWER, STEP)), encoding="utf-8")
        self.fake(
            'printf "%%s\\n" "$@" > "%s"\n' % args
            + 'env > "%s"\n' % env
            + 'cat "%s"\n' % stream
        )
        progress = []

        async def collect(step, message):
            progress.append((step, message))

        result = asyncio.run(self.runner().ask("how does it move?", on_progress=collect))
        self.assertEqual((result.status, result.answer, result.session_id), ("ok", "It moves.", "ses_1"))
        self.assertEqual(result.references[0]["lines"], "1-6")
        self.assertEqual([call["tool"] for call in result.calls], ["repo_repo_brief_context"])
        self.assertAlmostEqual(result.cost_usd, 0.01)
        self.assertEqual(progress, [(1, "repo_repo_brief_context Player move")])

        arguments = args.read_text(encoding="utf-8").splitlines()
        for flag in ("--standalone", "--format", "json", "--archived", "--agent", "repo-brief-ask"):
            self.assertIn(flag, arguments)
        environment = env.read_text(encoding="utf-8")
        self.assertIn("OPENCODE_DISABLE_PROJECT_CONFIG=1", environment)
        injected = json.loads(environment.split("OPENCODE_CONFIG_CONTENT=", 1)[1].splitlines()[0])
        self.assertEqual(injected["mcp"]["servers"]["repo"]["command"][-1], "ask")

    def test_a_failed_run_is_an_error_with_the_cli_s_message(self):
        args, env, stream = self.paths()
        self.fake('echo "no such model" >&2\nexit 2\n')
        result = asyncio.run(self.runner().ask("q"))
        self.assertEqual(result.status, "error")
        self.assertIn("no such model", result.answer)

    def test_the_time_limit_kills_the_run(self):
        self.fake("sleep 5\n")
        result = asyncio.run(self.runner(timeout_seconds=0.3).ask("q"))
        self.assertEqual(result.status, "timeout")


if __name__ == "__main__":
    unittest.main()
