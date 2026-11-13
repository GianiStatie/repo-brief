"""`overview --write`: `ask`'s agent writes the overview's written section.

One ask with a fixed question, so the runner, model and limits are the ones in
`.repo-brief/serve.json`. An answer over `LIMIT` characters goes back once, in
the same session, to be shortened: a server's instructions carry the section,
and Claude Code cuts them at 4,096 characters.
"""
from __future__ import annotations

from pathlib import Path

from .. import overview
from . import ask as ask_mod

LIMIT = 2400
QUESTION = (
    "Write the written section of this repository's overview (.repo-brief/overview.md). "
    "Agents that answer questions about this repository get it at the start of every "
    "session, before they search, so it should help them turn a question into the right "
    "search. Cover, in this order: what the project is for and who uses it, in two or three "
    "sentences; the words the domain uses, as a list: each term that users, the UI, the "
    "docs or the business use, in the words and language they use, with the name the code "
    "gives it (package, class, table, message key) where that differs; then the main "
    "flows, briefly. Take the terms from the code, the UI text and the docs, and state only "
    "what the files show. Keep it under %d characters of Markdown, with no line numbers. "
    "Your answer is the section itself, with nothing before or after it." % LIMIT
)
SHORTEN = (
    "That section is over %d characters. Shorten it to under %d characters of Markdown, "
    "keeping what helps most to turn a question into the right search. Your answer is the "
    "section itself, with nothing before or after it." % (LIMIT, LIMIT)
)


async def write(root: Path, adapter, *, runner=None, client_cls=None) -> list:
    """Every ask it took; the last one's answer is in the overview.

    Raises ValueError, and writes nothing, when the agent stops short of an answer.
    """
    root = Path(root)

    async def ask(question: str, session_id=None):
        return await ask_mod.run(
            root, adapter, question, session_id=session_id, client="overview",
            runner=runner, client_cls=client_cls,
        )

    results = [await ask(QUESTION)]
    first = results[0]
    if first.status == "ok" and len(first.answer.strip()) > LIMIT and first.session_id:
        results.append(await ask(SHORTEN, first.session_id))
    last = results[-1]
    if last.status != "ok" or not last.answer.strip():
        raise ValueError("the agent wrote no section (%s)" % last.status)
    overview.set_written(root, last.answer)
    return results
