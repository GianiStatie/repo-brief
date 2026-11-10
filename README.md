<p align="center">
  <img src="assets/logo.svg" width="104" alt="A pair of white boxer briefs with red hearts and three index rows">
</p>

# repo-brief

Give an agent a map of your repo, a ranked answer to "where does this live",
and a check that tells you when either has gone stale.

Standard library only. No installer, no venv, no dependencies. Python 3.9+.

```
pip install nothing
python3 -m repobrief.cli context --brief "save migration"
```

Why it is shaped this way: [DESIGN.md](DESIGN.md).
Known limits, consuming-repo workarounds and lessons: [LIMITATIONS.md](LIMITATIONS.md).

## The pattern

Three generated layers, one entry point.

| Layer | Question | Command |
|---|---|---|
| Map | What exists? | `index` |
| Context | What matters for this task? | `context` |
| Check | Is the derived layer still true? | `preflight` |

`graph` sits beside them as a view over the same units, not a fourth artifact.
`archive` keeps finished material out of all of them without deleting it — see
[Archive](#archive).

The invariant that keeps it from rotting: **derived, committed, verified.**
Derived so it never competes with the source, committed so a fresh clone has
it, verified so it cannot quietly go out of date. Drop any one and it becomes
a second source of truth.

The map carries a structure fingerprint. `index --check` compares that
fingerprint, not the bytes, so editing a `##` docstring does not make the map
stale; adding, renaming or deleting a file does. Descriptions refresh the next
time you run `index`.

One file in `.repo-brief/` is none of the three: `parse-cache.json` remembers
what each file parsed to, keyed by a hash of its text and of the parser, so a
second `context` call skips the parsing. It is local and rebuilt on demand;
repo-brief writes a `.repo-brief/.gitignore` that keeps it out of commits. What
a file says about other files — which import or link resolves — is never
cached, so a new module or note shows up on the next call.

## The read plan

`context --brief` is not a reading list. Each `Read:` line names a path, the
smallest useful slice, its size, and the heading or symbol that anchored it:

```
Read (26 KB):
  src/ui/settings.gd:1-120   4.1 KB
  docs/status.md:25-233                    18 KB  # Current focus
```

Small files are named whole; a large doc gets a heading section; code gets the
function and its doc comment. The cap keeps a long log from being quoted
as one "section". `--json` exposes the same plan as `read_plan`, with `start`,
`end`, `bytes`, `line`, `label`, and a `budget`.

## What ships and what doesn't

**Parsers ship.** Reading `.gd`, `.py`, `.java`, `.md`, `.tscn` is the same work
in every repo, so it lives here once.

**Adapters don't.** An adapter encodes a repo's *shape*: where decisions live,
what counts as a live loop, which directories are tiers. That is a judgement,
not a fact about the file tree, and it stays in the consuming repo.

**The kernel scores; adapters only declare.** An adapter returns units,
decisions, open-work items and canonical paths. It never ranks anything. That
split is what lets a ranking fix reach every repo at once.

## Adopting it

From the repository you want to annotate:

```bash
repo-brief scaffold
```

This reads the tree, then writes:

- `.repo-brief/profile.json` — what was detected, plus the judgements still to make
- `tools/brief_adapter.py` — a thin `GenericAdapter` subclass

The adapter starts with two `TODO`s, which is correct: a machine can find
`docs/decisions/`, but it cannot know that `docs/design/bugs.md` is authoritative
and `docs/notes/wishlist.md` is not.

Then:

```bash
repo-brief context --brief "something you would actually ask"
repo-brief index              # writes .repo-brief/map.md
repo-brief preflight          # fails on a missing front door
```

## Detection, not configuration

Everything factual is recomputed on every run: languages, manifests, test
directories, decision logs, the directory that holds product code.

`profile.json` records what was detected when the adapter was generated, but it
is a snapshot, not a cache: nothing reads it to decide anything. `preflight`
compares it against a fresh detection and says when the repo's shape has moved
on; `repo-brief profile` rewrites the snapshot without touching your adapter.

## Commands

```
repo-brief index [--check]         write, or verify the structure of, .repo-brief/map.md
repo-brief context "<q>" [--brief] [--json] [--archived] [--limit N]
repo-brief preflight               stale map structure, stale profile, missing front doors, undated docs, broken res:// refs
repo-brief graph [area] [--task Q] [--hubs] [--all] [--archived] [--json]
repo-brief archive [--apply PATH...] [--dry-run]
repo-brief doctor                  what was detected and parsed
repo-brief scaffold [--force]      generate an adapter for this repo
repo-brief profile                 refresh the recorded facts, leaving the adapter alone
repo-brief writing [--force]       write docs/meta/writing.md and docs/meta/repo-brief-notes.md
repo-brief skill [--check]         install, or verify, the shipped skills
```

`--root <path>` points it at another repository. `--adapter <path>` overrides
the default `tools/brief_adapter.py`.

## Dependency views

`graph` is a view, not an artifact: it is computed from the same units the map
is, never committed, and never checked. The "derived, committed, verified"
invariant applies to the map and the context pack; a graph is a lens on them.

```bash
repo-brief graph                     # areas as nodes, dependencies between them
repo-brief graph player              # files inside one area
repo-brief graph --task "save migration"   # subgraph for what a query surfaced
repo-brief graph --hubs              # singletons and the areas that reach them
repo-brief graph --all               # include scene and doc edges
```

Edges are inferred from what the parsers already read: `extends` from a class
header, `loads` from `res://` references, `uses` from a symbol mentioned in
another file, `references` from a scene to its script, `doc` from a wikilink.
An adapter overrides `edges()` only for a kind the kernel cannot see.

Java names only mean something inside a package, so a Java reference resolves
the way the compiler would: explicit import, then the file's own package, then
wildcard imports, plus fully qualified names written inline. `implements` is an
`extends` edge. A name that resolves nowhere is not an edge; a guess would be.

Areas default to the first directory under the derived `source_root` — for a
Godot project, `godot/src/<area>/`. Java files take theirs from the package: the
segment under the base package most classes share, so
`com.example.jojo.stands.model` is in `stands`. A Maven or Gradle build has one
source root per module (`<module>/src/main/java`), and usage hits come from all
of them.

## Archive

Completed and superseded material lives in an archive directory — `docs/archive`,
`archive`, `attic`, `deprecated`. It is never an answer by default: `context`
and `graph` skip it, and `index` lists it in its own `## Archived` section.

Ask for it with `--archived`, or by naming the past in the query (`archive`,
`history`, `historical`, `superseded`, `legacy`). The output says when it has:
`Context: ... (including archive)`, and hits are marked `(archived)`.

`repo-brief archive` reviews what looks finished:

- nothing links to it any more
- everything that links to it is itself archived
- its title duplicates a live page
- it is a root-level scratch file

`repo-brief archive --apply <path>...` moves one or more of them, rewrites the
wikilinks that pointed at them, and creates the archive with a README on first
use. `--dry-run` prints the move and rewrite plan. There is no `--all`: the
rules find orphans, not irrelevance.

## Writing and retrieving

Two commands set up the two loops:

```
repo-brief writing [--force]   write docs/meta/writing.md and docs/meta/repo-brief-notes.md
repo-brief skill [--check]     install, or verify, the shipped skills
```

`writing.md` is filled from what detection already knows: where decisions live,
where open work lives, which docs are front doors. The judgment — how entries are
shaped, what a captured sentence means — is stated once there instead of being
re-invented every session.

`skill` copies two skills into `.opencode/skills/`:

- `document` owns the writing loop below. It captures the owner's words, then
  writes the linked summary in the decision log, backlog, or status.
- `retrieve` owns the reading loop. It starts with `context --brief`, reads the
  plan's slices, budgets screenshots and test runs, and records where the brief
  fell short in `docs/meta/repo-brief-notes.md`.

The copies are committed with the repo and checked by `preflight`: edit one and
preflight fails. A missing `document` skill is a warning; a missing `retrieve`
skill is also a warning, so existing repos can adopt it before it becomes
required. Repo-specific rules belong in `writing.md` and `AGENTS.md`; the
workflow belongs in the tool.

The writing loop it sets up:

1. The owner says something that decides, rejects, prioritises, or corrects.
2. Their words go verbatim into `docs/meta/capture/YYYY-MM-DD.md`, tagged
   `C-YYYY-MM-DD-NN`.
3. The meaning goes into the decision log, backlog, or status, linking that tag.
4. The capture tier is excluded from `context`, the map and the graph — it is
   evidence, not guidance.

If the quote and the summary ever disagree, the quote wins.

## Using it from another repo

The adapter imports this package, so either install it (`pip install -e .`)
or put this directory on `PYTHONPATH`. A git submodule at
`tools/vendor/repo-brief` works too; set `PYTHONPATH` in the repo's
entry-point script.

A repo with a Makefile does not have to write those targets itself. Include the
shipped fragment at the bottom of the Makefile:

```make
REPO_BRIEF ?= tools/vendor/repo-brief
-include $(REPO_BRIEF)/make/repo-brief.mk
```

It supplies `index`, `index-check`, `context`, `graph`, `preflight`, `archive`,
`writing` and `skill`. Bottom matters: GNU make takes the last definition of a
target, so the fragment owns those names and the repo keeps its own. Variable
assignments made before the include (`PYTHON`, `ARGS`, `REPO_BRIEF`) override
the fragment's defaults. `-include` keeps `make test` working before the
submodule is initialized; only the fragment's targets are missing then.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## License

MIT. See `LICENSE`.
