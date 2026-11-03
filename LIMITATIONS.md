# Limitations

repo-brief is a map, a ranked brief, and a staleness check — not an agent
runtime. This file records what it deliberately does not do, what consuming
repos have had to work around, and what those workarounds taught the kernel.

Add an entry when a real task hit a real limit. Do not add wishes; add
observations.

## How to record one

- **Upstream, recurring:** add a row to "Encountered limitations" here.
- **Local, one-off:** append to `docs/meta/repo-brief-notes.md` in the
  consuming repo; promote it here when a second repo hits it.
- **Deliberate:** add it to "Deliberate non-goals" with the workaround, not as
  a feature request.

## Deliberate non-goals

| Limit | Why | Workaround |
|---|---|---|
| No session memory | Stateless by design: no daemon, database, or state beyond `.repo-brief/` | Keep a 3–5 file working set; re-run `context` |
| No duplicate-read detection | The tool cannot see the agent's context | Re-read only after a file changes; the `retrieve` skill owns this |
| No test runner | Non-goal | Run the repo's focused suite; full suite once |
| No screenshot tooling | Project tooling | Prefer text trees/properties; crop and batch when pixels are required |
| No prose judgment | Structure can be checked; meaning cannot | Owner review; `archive` only flags structure |
| No harness orchestration | The tool is harness-agnostic | Put subagent/delegation rules in `AGENTS.md` or the skill |
| No session cost data | No telemetry | Read the harness's own session store |

## Encountered limitations

| Date | Repo | Symptom | Workaround | Upstream? |
|---|---|---|---|---|
| 2026-11-03 | dragon-jump-remaster | `context` listed paths, not slices; agents read 28 KB `status.md` whole | Read the named line range/heading | fixed: read plan |
| 2026-11-03 | dragon-jump-remaster | 360 KB / 2,749-line decision log; agents paged arbitrary chunks | `grep` date/title, read ±30 lines | partial: read plan; split is repo work |
| 2026-11-03 | dragon-jump-remaster | 1 MB of screenshots read to verify a small UI change; same PNG 7× | crop to the widget, batch changes, one capture | no: project tooling + `retrieve` skill |
| 2026-11-03 | dragon-jump-remaster | 102-file review read linearly; user checked in at 12 min / 103 calls | split by area, delegate, read changed hunks | no: harness/AGENTS |
| 2026-11-03 | dragon-jump-remaster | no guidance on full-suite reruns; 10 full runs in one review | focused tests while iterating, full suite once | no: repo policy |
| 2026-11-03 | dragon-jump-remaster | `context` was rarely the first call; docs-first instructions won | make `context` the docs-first step | fixed: `retrieve` skill + AGENTS |

## Consuming repos

| Repo | Shape | Adapter notes |
|---|---|---|
| dragon-jump-remaster | Godot 4, docs vault, one dated decision log + table backlog | custom `open_work` for `open-design-questions.md`; canonical docs in `tools/brief_adapter.py` |
| shaman-arena-remastered | Godot 4, docs vault, decision directory | see its `tools/brief_adapter.py` |
| PLaiGROUND | Python/ML experiments, single decision log, root `archive/` | see its `tools/brief_adapter.py` |

## Lessons

1. **A path is not a plan.** The first version printed "Read: file.gd"; agents
   read the whole file. Line ranges and sizes change behavior more than
   ranking tweaks do.
2. **Adoption beats precision.** A 12-line brief that is never called loses to
   a 40-line brief that is called first. Put the entry point in `AGENTS.md`
   and a skill, not only in the README.
3. **The tool is not the agent.** Session memory, duplicate reads, screenshot
   budgets, and delegation are harness concerns. Keep them in the skill or
   `AGENTS.md`; adding them to the kernel would break the stateless design.
4. **Doc shape is a retrieval feature.** A 28 KB status page and a 2,749-line
   decision log make the mandated docs-first path expensive. The tool can flag
   size; the repo owns the split.
5. **Write the workaround down.** An unwritten limitation is rediscovered next
   session. Local notes are the intake; this file is the curated copy.
