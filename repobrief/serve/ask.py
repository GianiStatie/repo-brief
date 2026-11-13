"""`ask`: one read-only agent session, the same from the CLI and the server.

The settings come from `.repo-brief/serve.json`; the tools from
`tools.agent_specs`, which is where `dev` and `debug` are kept out; the prompt
from `prompt.build`. The runner named in the settings does the rest. Asks beyond
the `concurrency` setting wait for a slot, and every ask is offered to the log,
which keeps it only when the log is on. The answer's references are checked
against the files, with no model call, and what doesn't match is reported.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .. import log as log_mod
from .. import trust
from . import prompt as prompt_mod
from . import settings as settings_mod
from . import tools as tools_mod
from .result import AskResult, check_references
from .runners import RunnerUnavailable, get as get_runner

__all__ = ["AskResult", "RunnerUnavailable", "run"]

_slots: dict = {}


def _slot(root: Path, size: int) -> asyncio.Semaphore:
    # One semaphore per repo and event loop: a server has one loop; each CLI
    # call and each test gets its own.
    key = (str(root), id(asyncio.get_running_loop()))
    if key not in _slots:
        _slots[key] = asyncio.Semaphore(max(1, int(size)))
    return _slots[key]


async def run(
    root: Path,
    adapter,
    question: str,
    *,
    audience=None,
    session_id=None,
    client: str = "cli",
    trusted=None,
    groups=None,
    on_progress=None,
    runner=None,
    client_cls=None,
) -> AskResult:
    root = Path(root)
    settings = settings_mod.load(root)
    if runner:
        settings.runner = runner
    if audience not in settings_mod.AUDIENCES:
        audience = settings.audience
    trusted = trust.is_trusted(root) if trusted is None else trusted
    specs = tools_mod.agent_specs(
        root, adapter, trusted=trusted, groups=groups, script_timeout=settings.script_timeout_seconds
    )
    families = ["brief"]
    if any(spec.name == "repo_brief_facts" for spec in specs):
        families.append("facts")
    if any(spec.name == "repo_brief_inspect" for spec in specs):
        families.append("inspect")
    runner_cls = get_runner(settings.runner)
    agent = runner_cls(
        root,
        settings,
        specs,
        prompt_mod.build(
            root, audience, families, structured=getattr(runner_cls, "structured", True)
        ),
        client_cls=client_cls,
    )
    async with _slot(root, settings.concurrency):
        result = await agent.ask(question, session_id=session_id, on_progress=on_progress)
    result.reference_problems = check_references(root, result.references)
    log_mod.record_ask(
        root,
        question,
        result.answer,
        session_id=result.session_id,
        model=result.model,
        duration_ms=result.duration_ms,
        cost_usd=result.cost_usd,
        status=result.status,
        client=client,
        runner=result.runner,
        audience=audience,
        turns=result.turns,
        references=result.references,
        gaps=result.gaps,
        calls=result.calls,
    )
    return result
