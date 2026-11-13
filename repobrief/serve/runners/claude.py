"""Run `ask` with the Claude Agent SDK.

Settings: Read, Grep and Glob are the only built-in tools, and anything else that would need approval is refused; repo-brief's tools
are served in-process; nothing from the repository's own agent configuration is
loaded (no settings, hooks or MCP servers); the answer comes back as structured
output; and the time limit sits inside the client block, so the CLI still shuts
down normally when it fires.
"""
from __future__ import annotations

import asyncio
import json
import time

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    ToolAnnotations,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from ..result import ANSWER_SCHEMA, AskResult
from . import Runner

SERVER = "repo"
READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]
# How the CLI hands back the structured answer; not part of the investigation.
ANSWER_TOOL = "StructuredOutput"
STATUS_BY_SUBTYPE = {
    "success": "ok",
    "error_max_turns": "max_turns",
    "error_max_budget_usd": "max_budget",
}


def _describe(block) -> str:
    """A progress line for one tool call: the tool, then what it was given."""
    detail = ""
    if isinstance(block.input, dict):
        for key in ("query", "pattern", "file_path", "path", "script", "area"):
            if block.input.get(key):
                detail = str(block.input[key])
                break
    name = block.name.split("__")[-1]
    return "%s %s" % (name, detail[:80]) if detail else name


def _size(content) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    return len(json.dumps(content, default=str))


def _structured(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


class ClaudeRunner(Runner):
    name = "claude"

    def _toolbox(self):
        read_only = ToolAnnotations(readOnlyHint=True)
        tools = []
        for spec in self.specs:

            def make(spec=spec):
                async def handler(args):
                    # The tools read files and may run a script: off the event loop.
                    text = await asyncio.to_thread(spec.call, args or {})
                    return {"content": [{"type": "text", "text": text}]}

                return tool(spec.name, spec.description, spec.params, annotations=read_only)(handler)

            tools.append(make())
        allowed = ["mcp__%s__%s" % (SERVER, spec.name) for spec in self.specs]
        return create_sdk_mcp_server(SERVER, tools=tools), allowed

    def options(self, session_id=None) -> ClaudeAgentOptions:
        server, allowed = self._toolbox()
        return ClaudeAgentOptions(
            cwd=self.root,
            tools=READ_ONLY_TOOLS,
            mcp_servers={SERVER: server},
            allowed_tools=allowed,
            permission_mode="dontAsk",
            setting_sources=[],
            strict_mcp_config=True,
            verbatim_prompts=True,
            system_prompt=self.system_prompt,
            output_format={"type": "json_schema", "schema": ANSWER_SCHEMA},
            model=self.settings.model,
            max_turns=self.settings.max_turns,
            max_budget_usd=self.settings.max_budget_usd,
            resume=session_id or None,
        )

    async def ask(self, question: str, session_id=None, on_progress=None) -> AskResult:
        options = self.options(session_id)
        client_cls = self.client_cls or ClaudeSDKClient
        started = time.monotonic()
        result = None
        last_text = ""
        pending: dict = {}
        calls: list = []
        timed_out = False

        def done(**fields) -> AskResult:
            for call in pending.values():
                call.pop("started", None)
                calls.append(call)
            fields.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
            return AskResult(model=self.settings.model, runner=self.name, calls=calls, **fields)

        try:
            async with client_cls(options=options) as client:
                with anyio.move_on_after(self.settings.timeout_seconds) as timer:
                    await client.query(question)
                    async for message in client.receive_response():
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, ToolUseBlock) and block.name != ANSWER_TOOL:
                                    pending[block.id] = {
                                        "tool": block.name,
                                        "input": block.input,
                                        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                        "started": time.monotonic(),
                                    }
                                    if on_progress is not None:
                                        await on_progress(len(calls) + len(pending), _describe(block))
                                elif isinstance(block, TextBlock):
                                    last_text = block.text
                        elif isinstance(message, UserMessage) and isinstance(message.content, list):
                            for block in message.content:
                                if isinstance(block, ToolResultBlock) and block.tool_use_id in pending:
                                    call = pending.pop(block.tool_use_id)
                                    call["duration_ms"] = int((time.monotonic() - call.pop("started")) * 1000)
                                    call["result_bytes"] = _size(block.content)
                                    call["is_error"] = bool(block.is_error)
                                    calls.append(call)
                        elif isinstance(message, ResultMessage):
                            result = message
                timed_out = timer.cancelled_caught
        except ClaudeSDKError as exc:
            return done(answer="The agent failed: %s" % exc, status="error")

        if timed_out:
            return done(
                answer=last_text
                or "No answer within %g s; ask a narrower question." % self.settings.timeout_seconds,
                status="timeout",
            )
        if result is None:
            return done(answer=last_text or "The agent stopped without a result.", status="error")

        status = STATUS_BY_SUBTYPE.get(result.subtype, "error")
        if status == "ok" and result.is_error:
            status = "error"
        answer, references, gaps = last_text, [], []
        structured = _structured(result.structured_output)
        if structured is not None:
            answer = str(structured.get("answer") or "")
            references = [ref for ref in structured.get("references") or [] if isinstance(ref, dict)]
            gaps = [str(gap) for gap in structured.get("gaps") or []]
        elif result.result:
            # A cap stopped the agent before it answered: what it wrote so far.
            answer = result.result
        if status == "error" and not answer:
            answer = "; ".join(result.errors or []) or "The agent failed (%s)." % result.subtype
        return done(
            answer=answer,
            references=references,
            gaps=gaps,
            session_id=result.session_id or "",
            status=status,
            turns=result.num_turns or 0,
            duration_ms=result.duration_ms or int((time.monotonic() - started) * 1000),
            cost_usd=result.total_cost_usd or 0.0,
        )
