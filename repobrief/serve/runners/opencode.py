"""Run `ask` with the OpenCode CLI.

OpenCode cannot host repo-brief's tools in-process, so the runner starts
`repo-brief serve --toolbox ask` as a local MCP server in the run's config and
gives the agent a read-only permission set: reads, globs and greps, the toolbox,
nothing else. The answer comes back through the toolbox's `repo_brief_answer`
tool; a run that never calls it falls back to its last text.

Two things are deliberately off. The repository's own `opencode.json` is not
loaded (`OPENCODE_DISABLE_PROJECT_CONFIG`), so a repository cannot change the
agent's permissions, model or prompt, and neither is the `.claude` configuration
OpenCode can read. The session is created archived (`--archived`) so a scripted
run does not clutter the user's session list.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from ... import history
from ..result import AskResult
from . import Runner, RunnerUnavailable

SERVER = "repo"
AGENT = "repo-brief-ask"
ANSWER_TOOL = "repo_brief_answer"
DESCRIPTION = "Answers questions about this repository, read-only, with repo-brief's tools."
# Ordered rules: the first denies everything, later ones allow the reads and the
# toolbox, and the last ones deny a `.env` and anything outside the repository.
# OpenCode's base policy allows everything, so the first rule is the important
# one; agent rules are appended after the global ones, so this list goes in both.
PERMISSIONS = [
    {"action": "*", "resource": "*", "effect": "deny"},
    {"action": "read", "resource": "*", "effect": "allow"},
    {"action": "glob", "resource": "*", "effect": "allow"},
    {"action": "grep", "resource": "*", "effect": "allow"},
    {"action": "repo_*", "resource": "*", "effect": "allow"},
    {"action": "read", "resource": "*.env", "effect": "deny"},
    {"action": "read", "resource": "*.env.*", "effect": "deny"},
    {"action": "external_directory", "resource": "*", "effect": "deny"},
]
# Input keys worth naming in a progress line.
PROGRESS_KEYS = ("path", "pattern", "query", "script", "command", "url")


def _binary() -> str:
    found = shutil.which("opencode")
    if found is None:
        raise RunnerUnavailable(
            "the OpenCode runner needs the `opencode` CLI on PATH (see the OpenCode docs)"
        )
    return os.path.abspath(found)


def _toolbox_command(root: Path) -> list:
    """How OpenCode starts the server that holds the agent's tools."""
    found = shutil.which("repo-brief")
    binary = [os.path.abspath(found)] if found else [sys.executable, "-m", "repobrief.cli"]
    return binary + ["serve", "--root", str(root), "--toolbox", "ask"]


def _at(ms) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(ms) / 1000))
    except (TypeError, ValueError):
        return ""


def _describe(part: dict, state: dict) -> str:
    tool = str(part.get("tool") or "tool")
    given = state.get("input") if isinstance(state.get("input"), dict) else {}
    for key in PROGRESS_KEYS:
        if given.get(key):
            return "%s %s" % (tool, str(given[key])[:80])
    return tool


def parse_events(lines) -> dict:
    """What one `opencode run --format json` stream says, independent of a process.

    Kept separate so a test can replay a recorded stream without a model call.
    """
    found = {"session": "", "answer": "", "references": [], "gaps": [], "turns": 0, "cost": 0.0}
    structured = None
    for line in lines:
        line = line.strip() if isinstance(line, str) else ""
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        found["session"] = event.get("sessionID") or found["session"]
        kind = event.get("type")
        part = event.get("part") or {}
        if kind == "text":
            found["answer"] = str(part.get("text") or found["answer"])
        elif kind == "step_finish":
            found["turns"] += 1
            found["cost"] += float(part.get("cost") or 0)
        elif kind == "tool_use" and str(part.get("tool") or "").endswith(ANSWER_TOOL):
            structured = part.get("state") or {}
            structured = structured.get("input") if isinstance(structured.get("input"), dict) else None
    if structured is not None:
        found["answer"] = str(structured.get("answer") or "")
        found["references"] = [ref for ref in structured.get("references") or [] if isinstance(ref, dict)]
        found["gaps"] = [str(gap) for gap in structured.get("gaps") or []]
    return found


class OpenCodeRunner(Runner):
    name = "opencode"
    structured = False

    def config(self) -> dict:
        return {
            "mcp": {
                "servers": {
                    SERVER: {
                        "type": "local",
                        "command": _toolbox_command(self.root),
                        # Expose the tools natively: the prompt names them.
                        "codemode": False,
                    }
                }
            },
            "agents": {
                AGENT: {
                    "description": DESCRIPTION,
                    "mode": "primary",
                    "system": self.system_prompt,
                    "permissions": PERMISSIONS,
                }
            },
            "permissions": PERMISSIONS,
        }

    def environment(self) -> dict:
        env = dict(os.environ)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(self.config())
        # The repository's own OpenCode and Claude Code configuration is not
        # loaded: the agent's tools and permissions come from here only.
        env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        env["OPENCODE_DISABLE_CLAUDE_CODE"] = "1"
        env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] = "1"
        return env

    def command(self, binary: str, question: str, session_id=None) -> list:
        command = [
            binary, "run", "--standalone", "--format", "json", "--archived", "--agent", AGENT,
        ]
        # `claude-haiku-5-5` is a Claude runner name; OpenCode needs a
        # provider-qualified one, so without a `/` its own default model is used.
        if "/" in self.settings.model:
            command += ["--model", self.settings.model]
        if session_id:
            command += ["--session", session_id]
        return command + [question]

    async def ask(self, question: str, session_id=None, on_progress=None) -> AskResult:
        binary = _binary()
        started = time.monotonic()
        lines = []
        errors = []

        process = await asyncio.create_subprocess_exec(
            *self.command(binary, question, session_id),
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.environment(),
        )

        async def read_stdout():
            step = 0
            while True:
                line = await process.stdout.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace")
                lines.append(text)
                if on_progress is None:
                    continue
                try:
                    event = json.loads(text)
                except ValueError:
                    continue
                part = event.get("part") or {}
                if event.get("type") == "tool_use" and not str(part.get("tool") or "").endswith(ANSWER_TOOL):
                    step += 1
                    await on_progress(step, _describe(part, part.get("state") or {}))

        async def read_stderr():
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                errors.append(line.decode("utf-8", "replace"))

        timed_out = False
        try:
            await asyncio.wait_for(
                asyncio.gather(read_stdout(), read_stderr(), process.wait()),
                self.settings.timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise

        duration = int((time.monotonic() - started) * 1000)
        found = parse_events(lines)
        calls = self._calls(lines)
        stderr = "".join(errors).strip().splitlines()
        if timed_out:
            return AskResult(
                answer=found["answer"] or "No answer within %g s; ask a narrower question."
                % self.settings.timeout_seconds,
                status="timeout",
                session_id=found["session"],
                turns=found["turns"],
                cost_usd=round(found["cost"], 6),
                duration_ms=duration,
                model=self.settings.model,
                runner=self.name,
                calls=calls,
            )
        if process.returncode != 0:
            detail = stderr[-1] if stderr else "exit %d" % process.returncode
            return AskResult(
                answer=found["answer"] or "The OpenCode run failed: %s" % detail,
                references=found["references"],
                gaps=found["gaps"],
                status="error",
                session_id=found["session"],
                turns=found["turns"],
                cost_usd=round(found["cost"], 6),
                duration_ms=duration,
                model=self.settings.model,
                runner=self.name,
                calls=calls,
            )
        return AskResult(
            answer=found["answer"],
            references=found["references"],
            gaps=found["gaps"],
            session_id=found["session"],
            status="ok",
            turns=found["turns"],
            cost_usd=round(found["cost"], 6),
            duration_ms=duration,
            model=self.settings.model,
            runner=self.name,
            calls=calls,
        )

    def _calls(self, lines) -> list:
        """The investigation's tool calls, without the answer tool."""
        calls = []
        for text in lines:
            try:
                event = json.loads(text) if isinstance(text, str) else {}
            except ValueError:
                continue
            if event.get("type") != "tool_use":
                continue
            part = event.get("part") or {}
            if str(part.get("tool") or "").endswith(ANSWER_TOOL):
                continue
            state = part.get("state") or {}
            timing = state.get("time") or {}
            start, end = timing.get("start"), timing.get("end")
            calls.append({
                "tool": str(part.get("tool") or "tool"),
                "input": history.clean_input(state.get("input"), self.root),
                "at": _at(event.get("timestamp")),
                "result_bytes": len(str(state.get("output") or "")),
                "duration_ms": int(end - start) if isinstance(start, (int, float)) and isinstance(end, (int, float)) else 0,
                "is_error": state.get("status") == "error",
                "source": "opencode",
            })
        return calls
