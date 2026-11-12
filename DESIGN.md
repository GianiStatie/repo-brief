# Design

A repository holds two kinds of knowledge: what is there, and why. The map
answers the first. Decisions and open work carry the second. An agent needs
both before it edits anything, ranked for the task at hand, with a way to tell
when either has gone stale.

This file is the reasoning behind the shape. [SPEC.md](SPEC.md) is what it
must do, and [README](README.md) is how to use it.

## One entry point, three layers

| Layer | Question | Command |
|---|---|---|
| Map | What exists? | `index` |
| Context | What matters for this task? | `context` |
| Check | Is the derived layer still true? | `preflight` |

`graph` is a view over the same units, not a fourth artifact. `archive` is a
storage rule shared by all of them. By default there is no daemon, no database,
and no state beyond `.repo-brief/`. The index and the interaction log are
databases you turn on, and the MCP server is a process a client starts
([SPEC.md](SPEC.md#optional-stores)).

## Derived, committed, verified

The invariant is **derived, committed, verified**. Derived so the map never
competes with the source. Committed so a fresh clone has it. Verified so it
cannot quietly go out of date. Drop any one and it becomes a second source of
truth.

The map and the profile are committed. The context pack is computed per query,
and the graph is never committed. `preflight` verifies the map and the profile;
it does not verify a graph, because there is nothing to verify.

The parse cache is not committed, and neither are the optional index and the
other local files `.repo-brief/.gitignore` names. The parse cache
memoises a pure function: an entry is keyed by a hash of the file's text, and
the cache as a whole by a hash of the parsing code and the Python version, so a
changed file or a changed parser can never be answered from it. Readers see one
file's text and nothing else; whatever depends on other files existing --
Python imports, relative markdown links -- is resolved again on every pass.
That split is what keeps the cache from becoming the stale fact `detect()`
refuses to keep. A `.gitignore` beside it, which names itself, keeps it out of
commits without touching the repository's own ignore rules.

The map embeds each file's `##` docstring, so a comment edit would otherwise
make the committed map stale. Instead the map carries a `structure:`
fingerprint, computed by rendering it once with the description column blanked
and the decision and open-work line numbers dropped. `index --check` compares
the fingerprint, not the bytes: a changed docstring, or a paragraph that pushes
a decision down its log, passes and refreshes on the next `index`; a new,
renamed or deleted file or decision fails. A line number says where a record
sits, not what the repository decided.

A row names a file, not its path: the directory is the `###` heading above it.
Repeating the directory on every row would have made a large Java map much
larger to save an agent a lookup the brief already does in one call. So "where
does this live" goes to `context`, and the map answers what a file declares and
what a directory holds.

## Kernel and adapters

Adapters declare structure. The kernel scores, ranks and renders. An adapter
never decides what matters.

The contract is deliberately small: units, decisions, open work, sources,
canonical and dated docs, exclusions, edges, areas, archive directories, usage
roots and the generated trees to skip. Every one is a fact about the
repository, not a judgement about relevance. The skip list matters for repos
with a multi-gigabyte `runs/`: it prunes the walk instead of filtering it
afterwards.

Parsers ship. Reading `.gd`, `.tscn`, `.md`, `.py` or `.java` is the same work
in every repository, so it lives in `parsers.py` once. Parsers stay standard
library: Java is read with regular expressions over a copy of the source whose
comments and literals are blanked, not with a grammar. Adapters do not ship: where a
repository keeps its decisions, what counts as a live loop, which directories
are the front door -- that is a judgement, and it stays in that repository.

`scaffold` writes a repo-local adapter and a profile, leaving two `TODO`s that
`preflight` counts. The generated adapter subclasses the generic one, so an
unseen repository still works with no configuration.

The split is the main bet. Without it, each repository grows its own matcher,
ranker and renderer, and a ranking fix reaches only the repository where it was
made.

## Detection and the profile

`detect()` reads the tree: extension census, manifests, decision log, open work,
status and changelog, test directories, CI workflows, Godot autoloads, archive
directories, and a `source_root` that keeps usage hits inside product code.
Vendored directories never become the product root. A Maven or Gradle build
keeps each module's code in its own `src/main/java`, so `source_roots` lists
every one of them, and each `src/test/java` is a test directory.

In a git checkout the walk covers what git would check out: tracked files, and
untracked ones it does not ignore. The map is committed, so it has to be
rebuildable from a fresh clone, and a file only one machine has -- a `.env`, a
`.DS_Store`, a gitignored data directory -- would make it one machine's map.
Git's answer is only as good as the repository's ignore file, and few list
`.DS_Store`, so OS metadata -- `.DS_Store`, AppleDouble `._*` files,
`Thumbs.db`, `desktop.ini` -- is skipped by name, tracked or not: it never says
anything about the repository. Every fact is read from the walked files, never
from the disk, so an ignored `TODO.md` is not open work either. The skipped
trees go to git as exclusions: `--others` would otherwise list every file of an
untracked `runs/` the walk is about to prune, and a gitignored directory is
never entered at all. A submodule is another repository, and its files are not
part of this one's map. Outside a checkout, or without git, every file counts.

`profile.json` is a snapshot, not a cache. Every fact is recomputed on every
run; the profile records what was true when it was written, so `preflight` can
notice when it has aged out. `repo-brief profile` refreshes the facts and
leaves the judgements alone. File counts are excluded from the comparison: a
profile that goes stale because someone added a file would cry wolf on every
commit.

## The map

One row per file, grouped by directory: kind, what it declares, and which tests
cover it. A class file shows `class -> parent`, followed by the interfaces it
implements; anything else shows its first docstring line. Decisions and open work are summarised below, and archived
material gets its own section.

The map is an index, not evidence. It says where to look; it does not prove
what the code does.

## The context pack

Queries are tokenised with stopwords removed, plus literal phrases for
underscored identifiers and the whole query. Matches are weighted by field:
class names and titles first, descriptions next, tags and structural fields
last. A multi-word query must match more than one word, which is what keeps a
single generic term from dragging in unrelated records.

The pack ranks units, decision records, open loops, tests and usage sites. Usage
hits are restricted to `usage_roots`, so the tooling does not answer its own
query. Coverage is graded only over the files the brief actually shows. Archive
is hidden unless the query asks for it.

The brief is capped at roughly a dozen lines. Full mode keeps the same order
and adds excerpts and links.

A ranked path is still a reading list, so the brief also carries a **read plan**:
the smallest useful slice, its line range, its byte cost, and the heading or
symbol that anchored it. Small files are named whole; a large doc gets a heading
section; code gets the enclosing function and its doc comment. The range is
capped, so "read this section" cannot quietly mean "read the whole decision
log". `--json` exposes the same plan for a harness that wants to drive reads
itself.

The plan is a starting point, not a fence. It is chosen from the same tokens as
the ranking, so it can point at the wrong function in a file whose name matches
the query. The `retrieve` skill tells the agent to widen once, then state what
is missing — and to record the miss rather than silently work around it.

## The graph

Edges are inferred from source: `extends` (a class inherits another), `loads`
(a file references another by path, including Python imports resolved through
the repo's own packages), `uses` (a file names a class declared elsewhere),
`references` (a scene points at a script) and `doc` (prose links to a doc).
Prose edges follow wikilinks and relative markdown links alike, so a repo does
not have to be an Obsidian vault to get a doc graph. An adapter may declare
edges when its repository has a kind generic inference cannot see, but the
default covers the common cases.

Class names resolve by name for GDScript and Python, where a name is global. A
name two files declare resolves nowhere, and live code keeps a name an
archived copy also declares: a guessed edge is worse than a missing one. Java
names are scoped by package, so a Java reference resolves through the file's
imports, its own package, then its wildcard imports, and a nested type through
its outer class's file. `implements` is an `extends` edge.

Areas are the first directory under `source_root`. Java areas come from the
package instead: the segment under the base package, found by walking down
while one sub-package holds at least 80% of the classes below. The graph
offers area rollups, a task subgraph, hub ranking and a whole-repo view,
rendered as Mermaid. Every view is recomputed on demand.

## Archive

Finished material is still real; it just should not be an answer. Units under
an archive directory carry `kind="archive"`. `context` and `graph` hide them by
default, bring them back with `--archived`, and label the output so the reader
knows history is included.

`repo-brief archive` reviews candidates by structure, not by taste: orphaned
docs, docs linked only from the archive, duplicate titles, and root scratch
files. Review is advice. `--apply` moves explicit paths, refuses canonical
docs, duplicate destinations and missing files, rewrites inbound wikilinks in
the vault's own convention, and creates the archive directory with a README on
first use. There is no `--all`. An unlinked document can still be the only
record of why something is the way it is.

## Writing

The documentation loop is the one place the tool deliberately stops short of
doing the work. A tool can tell you a doc is unlinked; it cannot tell you what
the owner meant.

So the split is explicit. `writing` writes `docs/meta/writing.md` from what the
adapter reads and detection finds — decision log, open work, front doors,
capture directory. An adapter that reads decisions from a planning doc is the
authority on where they live; `writing` and `preflight` ask it, not detection. `skill` copies a
generic `document` skill into `.claude/skills/`, and `preflight` compares it to
the shipped file, so a hand-edited copy fails loudly instead of drifting. That
folder is the only one Claude Code and the Agent SDK read, and OpenCode reads it
too. A copy in the old `.opencode/skills/` is removed rather than kept beside
it: OpenCode prefers that folder, so the stale copy would win.

The skill's job is to keep two layers apart. The **capture** tier
(`docs/meta/capture/YYYY-MM-DD.md`) holds the owner's words verbatim, append-only,
tagged `C-YYYY-MM-DD-NN`. The **summary** holds what those words mean for the
work, in the decision log, backlog, or status, linked back to the tag. If the
two disagree, the quote wins.

The capture tier is excluded from units and sources, so it never competes in
`context` and never appears in the graph. Evidence is quoted, not ranked.

The reading loop gets the same treatment. `retrieve` is a second shipped skill:
it starts with `context --brief`, reads the plan's slices, budgets screenshots
and test runs, and stops when the next action is decidable. It cannot enforce
anything; the harness owns tool calls. Its value is that the procedure travels
with the tool and is checked for drift instead of being re-invented in every
`AGENTS.md`.

A skill cannot record a limitation, though. The local intake is
`docs/meta/repo-brief-notes.md`, written by `writing` and excluded from ranking
like the capture tier. The curated copy is `LIMITATIONS.md`: deliberate
non-goals, encountered limits with their workarounds, and the lessons
they taught. An unwritten workaround is rediscovered next session;
a written one can become a kernel fix.

## Checks

`preflight` verifies the map's structure, compares the profile against fresh
detection, confirms canonical docs exist, checks that dated docs carry a date,
resolves `res://` references, compares the installed skill to the shipped copy,
and counts unfinished adapter `TODO`s. It warns about archivable docs; it does
not move them.

`doctor` prints what detection and the adapter see -- parsers, manifests,
decisions, open work, tests, autoloads, archive, unit and edge counts -- for
when a result looks wrong.

## Non-goals

- It does not judge prose. Structure can be checked; meaning cannot.
- It is not a linter, type checker or test runner.
- It does not commit a dependency graph. A graph is a lens, not a record.
- It has no plugin discovery and no interactive prompts. `scaffold` and `init`
  must be runnable by an agent.
- The core has no third-party dependencies and supports Python 3.9+. The MCP
  server (`repo-brief[mcp]`) and the Claude runner for `ask`
  (`repo-brief[claude]`) are optional extras; without them the map, brief,
  overview and checks still work.
- The core has no daemon, and keeps no state beyond the repository unless
  asked. The index is opt-in and git-ignored, the trust list and the opt-in log
  live in the user's config and data directories, and the server is a process
  a client starts. The index must never change what the map says, so turning it
  on or off is safe at any time.
- The workarounds for these non-goals are recorded in
  [LIMITATIONS.md](LIMITATIONS.md), not re-litigated here.

## Where to change what

| Change | File |
|---|---|
| Scoring, thresholds, ranking | `repobrief/kernel.py` |
| A new file type or syntax | `repobrief/parsers.py` |
| Repository shape | the consuming repo's `tools/brief_adapter.py` |
| Detection, profile facts | `repobrief/detect.py` |
| Parse cache | `repobrief/cache.py` |
| Word index, incremental refresh | `repobrief/index.py` |
| Project overview | `repobrief/overview.py` |
| Tool catalog, groups, who may run what | `repobrief/catalog.py` |
| Trust list | `repobrief/trust.py` |
| Interaction log | `repobrief/log.py` |
| Importing Claude Code and OpenCode sessions | `repobrief/history.py` |
| The tools `serve` and `ask` share | `repobrief/serve/tools.py` |
| MCP server | `repobrief/serve/server.py` |
| `ask`: caps, slots, the log | `repobrief/serve/ask.py` |
| `ask`'s prompt | `repobrief/serve/prompts/`, assembled by `repobrief/serve/prompt.py` |
| `serve.json` settings | `repobrief/serve/settings.py` |
| A new agent CLI for `ask` | a class in `repobrief/serve/runners/` |
| Map and brief rendering | `repobrief/render.py` |
| Edge inference and graph views | `repobrief/graph.py` |
| Archive rules | `repobrief/archive.py` |
| Commands and checks | `repobrief/cli.py` |
