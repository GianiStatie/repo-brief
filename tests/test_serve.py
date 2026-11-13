"""serve and ask: settings, the shared tools, the prompt, the server, the Claude runner.

The first tests need only the standard library. The server tests need
`repo-brief[mcp]` and the runner tests `repo-brief[claude]`; each is skipped
when its library is missing, and CI runs them in a job that installs both.
"""
from __future__ import annotations

import asyncio
import http.client
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief import catalog, log, overview, trust  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.serve import prompt, settings, tools  # noqa: E402
from repobrief.serve.result import ANSWER_SCHEMA, AskResult, check_references  # noqa: E402

HAS_MCP = importlib.util.find_spec("mcp") is not None
HAS_SDK = importlib.util.find_spec("claude_agent_sdk") is not None
LATEST_PROTOCOL_VERSION = ""
if HAS_MCP:
    from mcp.types import LATEST_PROTOCOL_VERSION  # noqa: E402

FILES = {
    "godot/src/player.gd": (
        "class_name Player\nextends Node\n\n## Move the player.\nfunc move() -> void:\n\tpass\n"
    ),
    "tools/check_docs.py": (
        '"""Check the docs front doors."""\nimport argparse\n\n'
        'if __name__ == "__main__":\n'
        "    parser = argparse.ArgumentParser()\n"
        '    parser.add_argument("--strict", action="store_true")\n'
        '    print("docs ok", parser.parse_args().strict)\n'
    ),
    "tools/slow_report.py": (
        '"""Report on the levels, slowly."""\nimport time\n\n'
        'if __name__ == "__main__":\n    time.sleep(5)\n'
    ),
    "tools/bench.py": (
        '"""Benchmark the player."""\nimport subprocess\n\n'
        'if __name__ == "__main__":\n    subprocess.run(["godot", "--headless"])\n'
    ),
    "docs/index.md": "# Demo\n\nA small game about moving.\n",
}


def build() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-serve-"))
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
    catalog.write(root, GenericAdapter().detected(root))
    return root


class Env(unittest.TestCase):
    """A fresh repo, and config, data and settings that belong to this test only."""

    def setUp(self):
        for name, value in (
            ("REPO_BRIEF_CONFIG_DIR", tempfile.mkdtemp(prefix="repo-brief-config-")),
            ("REPO_BRIEF_DATA_DIR", tempfile.mkdtemp(prefix="repo-brief-data-")),
        ):
            self.set_env(name, value)
        for name in settings.ENV.values():
            self.set_env(name, None)
        self.root = build()
        self.adapter = GenericAdapter()

    def set_env(self, name, value):
        previous = os.environ.get(name)
        self.addCleanup(lambda: os.environ.__setitem__(name, previous) if previous is not None
                        else os.environ.pop(name, None))
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class SettingsTest(Env):
    def test_defaults_without_a_file(self):
        found = settings.load(self.root)
        self.assertEqual((found.runner, found.model, found.max_turns), ("claude", "claude-haiku-5-5", 40))
        self.assertEqual((found.concurrency, found.audience), (2, "developer"))

    def test_the_file_then_the_environment(self):
        settings.write_defaults(self.root)
        path = settings.path_for(self.root)
        path.write_text('{"model": "claude-sonnet-5-5", "max_turns": 12, "audience": "product"}', encoding="utf-8")
        self.set_env("REPO_BRIEF_MAX_TURNS", "7")
        found = settings.load(self.root)
        self.assertEqual((found.model, found.max_turns, found.audience), ("claude-sonnet-5-5", 7, "product"))

    def test_a_value_that_makes_no_sense_falls_back(self):
        settings.path_for(self.root).parent.mkdir(exist_ok=True)
        settings.path_for(self.root).write_text(
            '{"max_turns": "many", "concurrency": 0, "audience": "everyone"}', encoding="utf-8"
        )
        found = settings.load(self.root)
        self.assertEqual((found.max_turns, found.concurrency, found.audience), (40, 2, "developer"))

    def test_defaults_are_written_once(self):
        self.assertIsNotNone(settings.write_defaults(self.root))
        settings.path_for(self.root).write_text('{"model": "mine"}', encoding="utf-8")
        self.assertIsNone(settings.write_defaults(self.root))
        self.assertEqual(settings.load(self.root).model, "mine")


class ToolsTest(Env):
    def test_ask_never_gets_dev_or_debug(self):
        everything = set(catalog.GROUPS)
        for trusted in (False, True):
            for groups in (None, {"dev", "debug"}, everything):
                names = {
                    spec.name
                    for spec in tools.agent_specs(self.root, self.adapter, trusted=trusted, groups=groups)
                }
                self.assertTrue(names <= {
                    "repo_brief_context", "repo_brief_graph", "repo_brief_overview", "repo_brief_inspect",
                }, names)
        trusted_names = {spec.name for spec in tools.agent_specs(self.root, self.adapter, trusted=True)}
        self.assertIn("repo_brief_inspect", trusted_names)
        untrusted = {spec.name for spec in tools.agent_specs(self.root, self.adapter, trusted=False)}
        self.assertNotIn("repo_brief_inspect", untrusted)

    def test_the_inspect_tool_cannot_reach_a_dev_script(self):
        trust.trust(self.root)
        spec = next(
            spec for spec in tools.agent_specs(self.root, self.adapter, trusted=True)
            if spec.name == "repo_brief_inspect"
        )
        self.assertIn("No inspect script named 'bench'", spec.call({"script": "bench"}))
        self.assertIn("docs ok True", spec.call({"script": "check_docs", "args": "--strict"}))

    def test_scripts_need_trust(self):
        output = tools.run_script(self.root, "inspect", "check_docs", trusted=False, timeout=30)
        self.assertIn("not trusted", output)

    def test_a_script_that_runs_too_long_is_stopped(self):
        output = tools.run_script(self.root, "inspect", "slow_report", trusted=True, timeout=0.5)
        self.assertIn("Stopped script:tools/slow_report.py", output)

    def test_the_group_description_lists_its_scripts(self):
        text = tools.group_description(self.root, "inspect")
        self.assertIn("`check_docs`: Check the docs front doors. (options: --strict)", text)
        self.assertNotIn("bench", text)

    def test_the_catalog_says_what_this_client_may_run(self):
        self.assertIn("not trusted", tools.catalog_text(self.root, trusted=False))
        text = tools.catalog_text(self.root, trusted=True, groups={"brief", "inspect"})
        self.assertIn("run them with repo_inspect", text)
        self.assertIn("dev (1, not available to this client)", text)


class PromptTest(Env):
    def test_it_assembles_the_sections_the_audience_and_the_repo(self):
        state = self.root / ".repo-brief"
        state.mkdir(exist_ok=True)
        (state / "overview.md").write_text(
            "# Overview — demo\n\n<!-- facts: abc -->\n\n## Summary\n\nA small game.\n", encoding="utf-8"
        )
        (state / "prompt.md").write_text("Levels live in godot/levels.\n", encoding="utf-8")
        text = prompt.build(self.root, "product", ["brief", "inspect"])
        for expected in (
            "## repo-brief",
            "## This repository's read-only scripts",
            "The reader plans features",
            "## This repository",
            "A small game.",
            "## Notes for this repository",
            "Levels live in godot/levels.",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("<!--", text)
        self.assertNotIn("# Overview", text)

    def test_a_runner_without_a_schema_is_told_to_use_the_answer_tool(self):
        self.assertNotIn("repo_brief_answer", prompt.build(self.root, "developer", ["brief"]))
        text = prompt.build(self.root, "developer", ["brief"], structured=False)
        self.assertIn("repo_brief_answer", text)


class ResultTest(unittest.TestCase):
    def test_render_states_a_cap_then_the_parts(self):
        text = AskResult(
            answer="It moves.",
            references=[{"path": "godot/src/player.gd", "lines": "1-6", "note": "the class"}],
            reference_problems=["godot/src/player.gd:1-6: no such file"],
            gaps=["whether it runs on mobile"],
            session_id="ses_1",
            status="max_turns",
        ).render()
        self.assertTrue(text.startswith("Stopped at the turn limit"))
        self.assertIn("- godot/src/player.gd:1-6 - the class", text)
        self.assertIn("Reference problems:\n- godot/src/player.gd:1-6: no such file", text)
        self.assertIn("Gaps:\n- whether it runs on mobile", text)
        self.assertIn("Session: ses_1", text)

    def test_references_are_checked_against_the_files(self):
        root = Path(tempfile.mkdtemp(prefix="repo-brief-refs-"))
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
        refs = [
            {"path": "src/a.py", "lines": "1-3", "note": ""},
            {"path": "src/a.py", "lines": "1, 3", "note": ""},
            {"path": str(root / "src" / "a.py"), "lines": "", "note": ""},
            {"path": "src", "lines": "", "note": ""},
            {"path": "src/a.py", "lines": "2-9", "note": ""},
            {"path": "src/a.py", "lines": "L2", "note": ""},
            {"path": "src/b.py", "lines": "", "note": ""},
            {"path": "../a.py", "lines": "", "note": ""},
        ]
        self.assertEqual(check_references(root, refs), [
            "src/a.py:2-9: lines 2-9 are not in the file (3 lines)",
            'src/a.py:L2: lines should look like "12" or "12-40"',
            "src/b.py: no such file",
            "../a.py: outside the repository",
        ])


@unittest.skipUnless(HAS_MCP, "needs repo-brief[mcp]")
class ServerTest(Env):
    def server(self, **options):
        from repobrief.serve import server

        return server.build_server(self.root, self.adapter, **options)

    def names(self, server):
        return {tool.name for tool in asyncio.run(server.list_tools())}

    def test_an_untrusted_repo_gets_no_script_tools(self):
        self.assertEqual(
            self.names(self.server(trusted=False)),
            {"repo_context", "repo_graph", "repo_tools", "repo_ask", "repo_facts", "repo_fact"},
        )

    def test_a_trusted_repo_gets_one_tool_per_group_it_has(self):
        names = self.names(self.server(trusted=True))
        self.assertTrue({"repo_inspect", "repo_dev"} <= names)
        self.assertFalse({"repo_debug", "repo_manual"} & names)

    def test_groups_narrow_what_a_client_gets(self):
        names = self.names(self.server(trusted=True, groups={"inspect"}))
        self.assertIn("repo_inspect", names)
        self.assertNotIn("repo_dev", names)
        self.assertIn("repo_context", names)

    def test_tools_that_only_read_say_so(self):
        found = {tool.name: tool for tool in asyncio.run(self.server(trusted=True).list_tools())}
        for name in ("repo_context", "repo_graph", "repo_tools", "repo_ask", "repo_inspect"):
            self.assertTrue(found[name].annotations.read_only_hint, name)
        self.assertFalse(found["repo_dev"].annotations.read_only_hint)

    def test_a_group_tool_runs_its_script(self):
        result = asyncio.run(
            self.server(trusted=True).call_tool("repo_inspect", {"script": "check_docs", "args": "--strict"})
        )
        self.assertIn("docs ok True", result.content[0].text)

    def test_the_description_lists_the_groups_scripts(self):
        found = {tool.name: tool for tool in asyncio.run(self.server(trusted=True).list_tools())}
        self.assertIn("`check_docs`", found["repo_inspect"].description)
        self.assertIn("`bench`", found["repo_dev"].description)

    def test_the_ask_toolbox_is_exactly_the_agents_tools_plus_the_answer(self):
        toolbox = {
            tool.name for tool in asyncio.run(self.server(toolbox="ask", trusted=True).list_tools())
        }
        specs = {spec.name for spec in tools.agent_specs(self.root, self.adapter, trusted=True)}
        self.assertEqual(toolbox, specs | {"repo_brief_answer"})
        untrusted = {
            tool.name for tool in asyncio.run(self.server(toolbox="ask", trusted=False).list_tools())
        }
        self.assertNotIn("repo_brief_inspect", untrusted)
        self.assertIn("repo_brief_answer", untrusted)

    def test_the_toolbox_has_no_ask_and_no_dev_or_debug(self):
        names = {tool.name for tool in asyncio.run(self.server(toolbox="ask", trusted=True).list_tools())}
        self.assertFalse({"repo_ask", "repo_dev", "repo_debug", "repo_inspect"} & names)

    def test_the_instructions_carry_the_overviews_written_half_once_written(self):
        path = self.root / overview.OVERVIEW
        path.parent.mkdir(exist_ok=True)
        page = "# Overview\n\n## Summary\n\nA game.\n\n%s\n"
        path.write_text(page % overview.PLACEHOLDER, encoding="utf-8")
        self.assertNotIn("Not written yet", self.server().instructions)
        written = "%s\nA save is a slot on disk.\n%s" % (overview.WRITTEN_START, overview.WRITTEN_END)
        path.write_text(page % written, encoding="utf-8")
        self.assertIn("A save is a slot on disk.", self.server().instructions)
        long = "%s\n%s\n%s" % (overview.WRITTEN_START, "A word.\n" * 1000, overview.WRITTEN_END)
        path.write_text(page % long, encoding="utf-8")
        instructions = self.server().instructions
        self.assertLess(len(instructions), 4096)
        self.assertTrue(instructions.endswith("(The rest is in repo://overview.)"))

    def test_overview_and_map_are_resources(self):
        uris = {str(item.uri) for item in asyncio.run(self.server().list_resources())}
        self.assertEqual(uris, {"repo://overview", "repo://map"})

    def test_a_client_call_is_logged_only_once_the_log_is_on(self):
        from mcp import Client

        async def call():
            async with Client(self.server(trusted=True)) as client:
                await client.call_tool(
                    "repo_context", {"query": "Player move"},
                    meta={"ai.opencode/sessionID": "ses_xyz"},
                )

        asyncio.run(call())
        self.assertFalse(log.enabled(self.root))
        log.enable(self.root)
        asyncio.run(call())
        calls = log.recent_calls(self.root)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "repo_context")
        self.assertEqual(calls[0]["client_session"], "ses_xyz")
        self.assertGreater(calls[0]["result_bytes"], 0)
        self.assertEqual(calls[0]["is_error"], 0)


class ConnectTest(Env):
    """`connect` registers in one project, by absolute path; stand-in clients record the calls."""

    def setUp(self):
        super().setUp()
        bin_dir = Path(tempfile.mkdtemp(prefix="repo-brief-bin-"))
        self.record = bin_dir / "calls.txt"
        for client in ("claude", "opencode"):
            path = bin_dir / client
            path.write_text('#!/bin/sh\necho "$(basename "$0")|$PWD|$*" >> "%s"\n' % self.record, encoding="utf-8")
            path.chmod(0o755)
        self.set_env("PATH", "%s%s%s" % (bin_dir, os.pathsep, os.environ.get("PATH", "")))
        self.orchestrator = Path(tempfile.mkdtemp(prefix="repo-brief-orchestrator-")).resolve()

    def connect(self, *argv):
        from repobrief import cli

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--root", str(self.root), "connect", *argv])
        return code, out.getvalue() + err.getvalue()

    def calls(self):
        if not self.record.exists():
            return []
        return [line.split("|", 2) for line in self.record.read_text(encoding="utf-8").splitlines()]

    def test_it_prints_and_runs_nothing_by_default(self):
        code, output = self.connect()
        self.assertEqual(code, 0)
        self.assertIn("claude mcp add --scope local %s --" % self.root.name, output)
        self.assertIn("opencode mcp add %s --" % self.root.name, output)
        self.assertNotIn("--global", output)
        self.assertNotIn("--scope user", output)
        self.assertEqual(self.calls(), [])

    def test_apply_needs_an_existing_project(self):
        code, output = self.connect("--apply")
        self.assertEqual(code, 2)
        self.assertIn("never globally", output)
        code, output = self.connect("--into", str(self.orchestrator / "missing"), "--apply")
        self.assertEqual(code, 2)
        self.assertEqual(self.calls(), [])

    def test_apply_registers_in_the_project_by_absolute_path(self):
        code, output = self.connect("--into", str(self.orchestrator), "--name", "the repo!", "--apply")
        self.assertEqual(code, 0, output)
        calls = self.calls()
        self.assertEqual([client for client, _cwd, _args in calls], ["claude", "opencode"])
        for client, cwd, args in calls:
            self.assertEqual(Path(cwd).resolve(), self.orchestrator)
            before, server = args.split(" -- ", 1)
            self.assertEqual(before.split()[-1], "the-repo")
            self.assertTrue(os.path.isabs(server.split()[0]), server)
            self.assertTrue(server.endswith("serve --root %s" % self.root.resolve()), server)
        self.assertIn("--scope local", calls[0][2])


REPO = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(HAS_MCP, "needs repo-brief[mcp]")
class HttpTest(Env):
    TOKEN = "s3cret-token"

    def setUp(self):
        super().setUp()
        self.set_env("REPO_BRIEF_HTTP_TOKEN", None)

    def test_hosts_are_loopback_the_bind_address_and_the_allowed_ones(self):
        from repobrief.serve import server

        found = server.http_security("0.0.0.0", ["repo.example.com"])
        self.assertTrue(found.enable_dns_rebinding_protection)
        for host in ("127.0.0.1:*", "localhost", "[::1]:*", "repo.example.com", "repo.example.com:*"):
            self.assertIn(host, found.allowed_hosts)
        self.assertNotIn("0.0.0.0", found.allowed_hosts)
        self.assertIn("10.0.0.5:*", server.http_security("10.0.0.5").allowed_hosts)
        self.assertIn("https://repo.example.com", found.allowed_origins)

    def test_off_loopback_a_token_is_required(self):
        from repobrief.serve import server

        app = server.build_server(self.root, self.adapter, trusted=False)
        with self.assertRaises(server.HttpRefused):
            server.http_app(app, "0.0.0.0")
        self.assertIsNotNone(server.http_app(app, "0.0.0.0", token="x"))
        self.assertIsNotNone(server.http_app(app, "127.0.0.1"))

    def test_the_cli_refuses_instead_of_serving(self):
        from repobrief import cli

        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["--root", str(self.root), "serve", "--http", "--host", "0.0.0.0"])
        self.assertEqual(code, 2)
        self.assertIn("REPO_BRIEF_HTTP_TOKEN", err.getvalue())

    def start(self, *extra) -> int:
        port = free_port()
        log = tempfile.TemporaryFile()
        process = subprocess.Popen(
            [sys.executable, "-m", "repobrief.cli", "serve", "--root", str(self.root), "--http",
             "--port", str(port), *extra],
            env=dict(os.environ, REPO_BRIEF_HTTP_TOKEN=self.TOKEN, PYTHONPATH=str(REPO)),
            stdout=log,
            stderr=log,
        )
        self.addCleanup(log.close)
        self.addCleanup(process.wait, 10)
        self.addCleanup(process.terminate)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.seek(0)
                self.fail("the server exited: %s" % log.read().decode(errors="replace"))
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                return port
            except OSError:
                time.sleep(0.1)
        self.fail("the server did not start within 30 s")

    def post(self, port: int, headers: dict) -> int:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        })
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("POST", "/mcp", body=body, headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **headers,
            })
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def test_a_session_over_http_and_the_requests_it_refuses(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        port = self.start("--allowed-host", "repo.test")
        bearer = {"Authorization": "Bearer %s" % self.TOKEN}

        async def session():
            async with create_mcp_http_client(headers=bearer) as http_client:
                url = "http://127.0.0.1:%d/mcp" % port
                async with streamable_http_client(url, http_client=http_client) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        names = {tool.name for tool in (await client.list_tools()).tools}
                        found = await client.call_tool("repo_context", {"query": "Player move"})
                        return names, found.content[0].text

        names, text = asyncio.run(session())
        self.assertEqual(
            names, {"repo_context", "repo_graph", "repo_tools", "repo_ask", "repo_facts", "repo_fact"}
        )
        self.assertIn("godot/src/player.gd", text)

        self.assertEqual(self.post(port, {}), 401)
        self.assertEqual(self.post(port, {"Authorization": "Bearer wrong"}), 401)
        self.assertEqual(self.post(port, {**bearer, "Host": "evil.example:%d" % port}), 421)
        self.assertEqual(self.post(port, {**bearer, "Host": "repo.test:%d" % port}), 200)


def scripted(messages, delay: float = 0.0):
    """A stand-in for ClaudeSDKClient that replays `messages`."""

    class Fake:
        seen: list = []
        active = 0
        most = 0

        def __init__(self, options=None):
            self.options = options
            Fake.seen.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def query(self, question):
            self.question = question

        async def receive_response(self):
            Fake.active += 1
            Fake.most = max(Fake.most, Fake.active)
            try:
                for message in messages:
                    if delay:
                        await asyncio.sleep(delay)
                    yield message
            finally:
                Fake.active -= 1

    return Fake


@unittest.skipUnless(HAS_SDK, "needs repo-brief[claude]")
class ClaudeRunnerTest(Env):
    def messages(self, **result):
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )

        final = {
            "subtype": "success",
            "duration_ms": 1500,
            "duration_api_ms": 900,
            "is_error": False,
            "num_turns": 3,
            "session_id": "ses_1",
            "total_cost_usd": 0.002,
            "usage": {},
            "result": None,
            "structured_output": {
                "answer": "It moves.",
                "references": [{"path": "godot/src/player.gd", "lines": "4-6", "note": "move()"}],
                "gaps": [],
            },
        }
        final.update(result)
        return [
            AssistantMessage(
                content=[ToolUseBlock(id="t1", name="mcp__repo__repo_brief_context", input={"query": "Player move"})],
                model="fake",
            ),
            UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="plan", is_error=False)]),
            AssistantMessage(
                content=[ToolUseBlock(id="t2", name="Read", input={"file_path": "godot/src/player.gd"})],
                model="fake",
            ),
            UserMessage(content=[ToolResultBlock(tool_use_id="t2", content="class_name Player", is_error=False)]),
            AssistantMessage(
                content=[ToolUseBlock(id="t3", name="StructuredOutput", input={})], model="fake"
            ),
            ResultMessage(**final),
        ]

    def ask(self, client_cls, **options):
        from repobrief.serve import ask

        return asyncio.run(ask.run(self.root, self.adapter, "how does the player move?",
                                   client_cls=client_cls, **options))

    def test_a_structured_answer_with_its_calls_and_progress(self):
        progress = []

        async def collect(step, message):
            progress.append((step, message))

        result = self.ask(scripted(self.messages()), on_progress=collect)
        self.assertEqual((result.status, result.answer, result.turns), ("ok", "It moves.", 3))
        self.assertEqual(result.references[0]["path"], "godot/src/player.gd")
        self.assertEqual(result.reference_problems, [])
        self.assertEqual(result.session_id, "ses_1")
        self.assertEqual([call["tool"] for call in result.calls], ["mcp__repo__repo_brief_context", "Read"])
        self.assertEqual([step for step, _ in progress], [1, 2])
        self.assertEqual(progress[0][1], "repo_brief_context Player move")

    def test_references_that_do_not_match_the_files_are_reported(self):
        answer = {"answer": "It moves.", "gaps": [], "references": [
            {"path": "godot/src/player.gd", "lines": "4-6", "note": "move()"},
            {"path": "godot/src/player.gd", "lines": "40-60", "note": "jump()"},
        ]}
        result = self.ask(scripted(self.messages(structured_output=answer)))
        self.assertEqual(
            result.reference_problems,
            ["godot/src/player.gd:40-60: lines 40-60 are not in the file (6 lines)"],
        )
        self.assertIn("Reference problems:", result.render())

    def test_the_options_keep_the_agent_read_only(self):
        client = scripted(self.messages())
        self.ask(client)
        options = client.seen[0]
        self.assertEqual(options.tools, ["Read", "Grep", "Glob"])
        self.assertEqual(options.permission_mode, "dontAsk")
        self.assertEqual(options.setting_sources, [])
        self.assertTrue(options.strict_mcp_config)
        self.assertEqual(options.output_format["schema"], ANSWER_SCHEMA)
        self.assertTrue(all(name.startswith("mcp__repo__repo_brief_") for name in options.allowed_tools))

    def test_dev_and_debug_never_reach_the_agent(self):
        trust.trust(self.root)
        client = scripted(self.messages())
        self.ask(client, groups=set(catalog.GROUPS))
        allowed = client.seen[0].allowed_tools
        self.assertIn("mcp__repo__repo_brief_inspect", allowed)
        self.assertFalse([name for name in allowed if "dev" in name or "debug" in name])

    def test_a_cap_is_reported_with_the_partial_answer(self):
        result = self.ask(scripted(self.messages(
            subtype="error_max_turns", is_error=True, result="What I had so far.", structured_output=None,
        )))
        self.assertEqual((result.status, result.answer), ("max_turns", "What I had so far."))

    def test_the_time_limit(self):
        self.set_env("REPO_BRIEF_TIMEOUT", "0.2")
        result = self.ask(scripted(self.messages(), delay=0.15))
        self.assertEqual(result.status, "timeout")
        self.assertEqual([call["tool"] for call in result.calls], ["mcp__repo__repo_brief_context"])

    def test_the_log_keeps_an_ask_only_once_it_is_on(self):
        self.ask(scripted(self.messages()))
        self.assertFalse(log.enabled(self.root))
        log.enable(self.root)
        self.ask(scripted(self.messages()))
        entry = log.recent(self.root)[0]
        self.assertEqual((entry["client"], entry["runner"], entry["turns"]), ("cli", "claude", 3))
        self.assertEqual(len(log.calls_for(self.root, entry["id"])), 2)

    def test_asks_beyond_the_concurrency_wait_for_a_slot(self):
        from repobrief.serve import ask

        self.set_env("REPO_BRIEF_CONCURRENCY", "1")
        client = scripted(self.messages(), delay=0.02)

        async def both():
            return await asyncio.gather(*(
                ask.run(self.root, self.adapter, "q%d" % number, client_cls=client) for number in range(2)
            ))

        results = asyncio.run(both())
        self.assertEqual([item.status for item in results], ["ok", "ok"])
        self.assertEqual(client.most, 1)


if __name__ == "__main__":
    unittest.main()
