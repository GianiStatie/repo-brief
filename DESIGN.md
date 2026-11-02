# Design

A repository holds two kinds of knowledge: what is there, and why. The map
answers the first. Decisions and open work carry the second. An agent needs
both before it edits anything, ranked for the task at hand, with a way to tell
when either has gone stale.

This file is the reasoning behind the shape. [README](README.md) is how to use
it.

## One entry point, three layers

| Layer | Question | Command |
|---|---|---|
| Map | What exists? | `index` |
| Context | What matters for this task? | `context` |
| Check | Is the derived layer still true? | `preflight` |

`graph` is a view over the same units, not a fourth artifact. `archive` is a
storage rule shared by all of them. There is no daemon, no database, and no
state beyond `.repo-brief/`.

## Derived, committed, verified

The invariant is **derived, committed, verified**. Derived so the map never
competes with the source. Committed so a fresh clone has it. Verified so it
cannot quietly go out of date. Drop any one and it becomes a second source of
truth.

The map and the profile are committed. The context pack is computed per query,
and the graph is never committed. `preflight` verifies the map and the profile;
it does not verify a graph, because there is nothing to verify.

The map embeds each file's `##` docstring, so a comment edit would otherwise
make the committed map stale. Instead the map carries a `structure:`
fingerprint, computed by rendering it once with the description column blanked.
`index --check` compares the fingerprint, not the bytes: a changed docstring
passes and refreshes on the next `index`; a new, renamed or deleted file fails.

## Kernel and adapters

Adapters declare structure. The kernel scores, ranks and renders. An adapter
never decides what matters.

The contract is deliberately small: units, decisions, open work, sources,
canonical and dated docs, exclusions, edges, areas, archive directories, and
usage roots. Every one is a fact about the repository, not a judgement about
relevance.

Parsers ship. Reading `.gd`, `.tscn`, `.md` or `.py` is the same work in every
repository, so it lives in `parsers.py` once. Adapters do not ship: where a
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
Vendored directories never become the product root.

`profile.json` is a snapshot, not a cache. Every fact is recomputed on every
run; the profile records what was true when it was written, so `preflight` can
notice when it has aged out. `repo-brief profile` refreshes the facts and
leaves the judgements alone. File counts are excluded from the comparison: a
profile that goes stale because someone added a file would cry wolf on every
commit.

## The map

One row per file, grouped by directory: kind, what it declares, and which tests
cover it. A class file shows `class -> parent`; anything else shows its first
docstring line. Decisions and open work are summarised below, and archived
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

## The graph

Edges are inferred from source: `extends` (a class inherits another), `loads`
(a file references another by path), `uses` (a file names a class declared
elsewhere), `references` (a scene points at a script) and `doc` (prose links to
a doc). An adapter may declare edges when its repository has a kind generic
inference cannot see, but the default covers the common cases.

Areas are the first directory under `source_root`. The graph offers area
rollups, a task subgraph, hub ranking and a whole-repo view, rendered as
Mermaid. Every view is recomputed on demand.

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

So the split is explicit. `writing` writes `docs/meta/writing.md` from detected
facts — decision log, open work, front doors, capture directory. `skill` copies a
generic `document` skill into `.opencode/skills/`, and `preflight` compares it to
the shipped file, so a hand-edited copy fails loudly instead of drifting.

The skill's job is to keep two layers apart. The **capture** tier
(`docs/meta/capture/YYYY-MM-DD.md`) holds the owner's words verbatim, append-only,
tagged `C-YYYY-MM-DD-NN`. The **summary** holds what those words mean for the
work, in the decision log, backlog, or status, linked back to the tag. If the
two disagree, the quote wins.

The capture tier is excluded from units and sources, so it never competes in
`context` and never appears in the graph. Evidence is quoted, not ranked.

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
- It has no plugin discovery and no interactive prompts. `scaffold` must be
  runnable by an agent.
- It has no third-party dependencies and supports Python 3.9+.

## Where to change what

| Change | File |
|---|---|
| Scoring, thresholds, ranking | `repobrief/kernel.py` |
| A new file type or syntax | `repobrief/parsers.py` |
| Repository shape | the consuming repo's `tools/brief_adapter.py` |
| Detection, profile facts | `repobrief/detect.py` |
| Map and brief rendering | `repobrief/render.py` |
| Edge inference and graph views | `repobrief/graph.py` |
| Archive rules | `repobrief/archive.py` |
| Commands and checks | `repobrief/cli.py` |
