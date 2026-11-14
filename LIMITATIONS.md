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
- **Anonymous:** this file is public. Say what went wrong, never which
  repository it happened in or what that repository is built with; examples
  use JoJo's Bizarre Adventure names. See `AGENTS.md`.

## Deliberate non-goals

| Limit | Why | Workaround |
|---|---|---|
| No session memory | Stateless by default: no daemon, database, or state beyond `.repo-brief/` until you turn on the index or the log | Keep a 3–5 file working set; re-run `context` |
| No duplicate-read detection | The tool cannot see the agent's context | Re-read only after a file changes; the `retrieve` skill owns this |
| No test runner | Non-goal | Run the repo's focused suite; full suite once |
| No screenshot tooling | Project tooling | Prefer text trees/properties; crop and batch when pixels are required |
| No prose judgment | Structure can be checked; meaning cannot | Owner review; `archive` only flags structure |
| No harness orchestration | The tool is harness-agnostic | Put subagent/delegation rules in `AGENTS.md` or the skill |
| No session cost data by default | No telemetry, and repo-brief runs no model of its own; the log is opt-in and never leaves the machine | Read the harness's own store; `repo-brief log import` copies its questions and tool calls |
| Imported sessions are a snapshot | `log import` reads both stores when you run it; nothing watches them | Run it again: a question that grew is refreshed, not duplicated |
| `--from` imports one store | The OpenCode-to-Claude check needs both stores in the same run, so one store at a time can duplicate a harness conversation | Use the default `all`; `--from` is for when the other store is missing or broken |
| Facts are only what the adapter extracts | The tool cannot know a flag is on; that state lives outside the code | Read a fact as "declared and checked here", and say the runtime state is unknown |
| The facts pointer needs a cache | `context` reads `.repo-brief/facts.json` and never builds it, so a fresh clone shows no pointer until `repo-brief facts` runs | Run `facts` once; the hint then appears |
| No third-party dependency in the core | Standard-library only keeps the map and brief usable anywhere | Install `repo-brief[mcp]` for the server |
| Trust is machine-local, and covers scripts only | A repo that could grant itself trust would not be a gate; the adapter is how repo-brief reads a repo, so it runs either way | `repo-brief trust` once per repo, `--revoke` to remove it; read an unfamiliar repo's `tools/brief_adapter.py` before `init` |
| `search` reads the modules that hold the product code | A module is the folder above a usage root's `src`; the repo's other trees (tooling, release scripts, design mockups) stay out so they do not push the code down. A repo whose code sits at its top is read whole, vendored libraries included | Use `context` for docs and decisions; widen with `grep` |
| A running server reads the tree as it was when it started | The adapter keeps its file list for the life of the process, and without the index the files it read too | Start a new session after `update` (a client starts the server with it), or restart `serve --http`; with the index on, edits to files it knows are seen, new files are not |
| Plurals fold by a light English rule | The core has no dictionary or language model; `stories` finds `story` and `endpoint` finds `Endpoints`, but a few words fold wrongly (`cookies` to `cooky`, so `cookie` misses it) and other languages' plurals stay apart | Ask with the form the code uses; widen with `grep` |
| Words are ASCII letters, digits and underscores | An accented letter ends a word, so `Kakyōin` is counted as `kaky` and `in`, and a query typed as `kakyoin` misses it | Search for the plain runs either side of the accent, or `grep` |
| With the index on, usage matches words and prefixes, not substrings | An inverted index cannot answer arbitrary substring scans cheaply; `cat` inside `concatenate` was a false positive | Ask with the word or its start; widen with `grep`, or run once with `REPO_BRIEF_NO_INDEX=1` |

## Encountered limitations

| Date | Symptom | Workaround | Upstream? |
|---|---|---|---|
| 2026-11-03 | `context` listed paths, not slices; agents read a long `status.md` whole | Read the named line range/heading | fixed: read plan |
| 2026-11-03 | a very long decision log; agents paged arbitrary chunks | `grep` date/title, read ±30 lines | partial: read plan; split is repo work |
| 2026-11-03 | screenshots read whole to verify a small UI change, the same one again and again | crop to the widget, batch changes, one capture | no: project tooling + `retrieve` skill |
| 2026-11-03 | a large review read linearly; the user had to check in partway | split by area, delegate, read changed hunks | no: harness/AGENTS |
| 2026-11-03 | no guidance on full-suite reruns; the full suite ran again and again in one review | focused tests while iterating, full suite once | no: repo policy |
| 2026-11-03 | `context` was rarely the first call; docs-first instructions won | make `context` the docs-first step | fixed: `retrieve` skill + AGENTS |
| 2026-11-10 | `skill` installed into `.opencode/skills/`, which only OpenCode reads | copy the skills into `.claude/skills/` by hand | fixed: `skill` installs into `.claude/skills/`, which OpenCode also reads, and removes the old copies |
| 2026-11-10 | every map read as stale once; `index --check` said only "stale", and the instruction to re-index was in a commit message | run `repo-brief index` after upgrading | partial: README "Upgrading"; `--check` still cannot tell an upgrade from a changed repo |
| 2026-11-13 | a Markdown table row could be larger than the whole slice cap, and a heading section could cost more than the plan | cap slices and the plan by bytes; a row over the cap is named in the path list, not sliced | fixed: byte cap, plan budget, path list |

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
4. **Doc shape is a retrieval feature.** A long status page and a longer
   decision log make the mandated docs-first path expensive. The tool can flag
   size; the repo owns the split.
5. **Write the workaround down.** An unwritten limitation is rediscovered next
   session. Local notes are the intake; this file is the curated copy.
6. **Generated and append-only files need different flags.** `writing --force`
   refreshed the conventions and erased the notes in the same run. Notes are
   created only when missing; `--force` never overwrites intake.
7. **A name is only unique where its language says it is.** Two packages can
   declare the same class name. Resolve a name the way the compiler would, or
   draw no edge: a guessed edge is worse than a missing one.
8. **Cache what a file says, not what it says about other files.** A parse keyed
   by the file's own text cannot go stale. Whether its imports and links
   resolve depends on the rest of the tree, so that part is recomputed on
   every pass.
9. **Derive from what a clone has, not from what the disk has.** A committed map
   built from the working directory picked up a `.env`, `.DS_Store` files and a
   data directory, and became one machine's map. In a git checkout, the walk is
   what git would check out.
10. **A position is not structure.** Fingerprinting the line a decision sits on
    made every paragraph above it a stale map. What was decided is structure;
    where it sits refreshes on the next `index`.
11. **Ask the adapter before acting on what it declares.** `preflight` warned,
    and `writing` steered the `document` skill, by detection alone, after the
    adapter had already said where decisions live.
12. **Some files are never the repository's, whatever git says.** Walking what
    git would check out still counted Finder's `.DS_Store` files in a repo whose
    ignore rules never named them. OS metadata is skipped by name, because no
    ignore file can be relied on to list it.
13. **Send each question to the layer that answers it.** The skill pointed
    "where does this live" at the map, whose rows name a file but not its
    directory; the brief answers it in one call. The map keeps what a file
    declares and what a directory holds.
