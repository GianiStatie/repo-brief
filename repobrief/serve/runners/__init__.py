"""The agent CLIs that can run `ask`, one class each.

A runner gets the settings, the tools (already filtered by the rule in
`tools.agent_specs`) and the assembled prompt, and returns an `AskResult`.
Adding a CLI means one class and one line in `RUNNERS`; the tool set, the
read-only rule and the prompt stay the same for all of them.
"""
from __future__ import annotations

import importlib

# name -> (module, class, what to install when the import fails)
RUNNERS = {
    "claude": ("repobrief.serve.runners.claude", "ClaudeRunner", "repo-brief[claude]"),
    "opencode": ("repobrief.serve.runners.opencode", "OpenCodeRunner", "the opencode CLI"),
}


class RunnerUnavailable(Exception):
    """The runner is unknown, or its library is not installed."""


class Runner:
    name = ""
    # Whether the runner hands the answer back as a schema (Claude) or through
    # the toolbox's answer tool (OpenCode).
    structured = True

    def __init__(self, root, settings, specs, system_prompt, client_cls=None):
        self.root = root
        self.settings = settings
        self.specs = specs
        self.system_prompt = system_prompt
        # Tests hand in a fake client; a real run uses the CLI's own.
        self.client_cls = client_cls

    async def ask(self, question: str, session_id=None, on_progress=None):
        raise NotImplementedError


def get(name: str) -> type:
    if name not in RUNNERS:
        raise RunnerUnavailable("unknown runner %r; known: %s" % (name, ", ".join(sorted(RUNNERS))))
    module_name, attribute, extra = RUNNERS[name]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RunnerUnavailable(
            "the %s runner needs `%s` (%s)" % (name, extra, exc.name or exc)
        ) from exc
    return getattr(module, attribute)
