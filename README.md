<p align="center">
  <img src="assets/logo.svg" width="104" alt="A pair of white boxer briefs with red hearts and three index rows">
</p>

# repo-brief

A map of your repository, a ranked answer to "where does this live", and a check
that tells you when either has gone stale.

It runs as a CLI, or as one MCP server per repo for an orchestrator. The core is
standard library only, Python 3.9+.

```bash
repo-brief init
repo-brief context --brief "save migration"
```

## Three layers

| Layer | Question | Command |
|---|---|---|
| Map | What exists? | `index` |
| Brief | What matters for this task? | `context` |
| Check | Is the derived layer still true? | `preflight` |

`update` keeps all of it current, re-reading only the files that changed.
`graph` is a view over the same units, not a fourth artifact.

The map carries a structure fingerprint. `index --check` fails when a file or a
decision is added, renamed or removed, not when a docstring or a paragraph
changes.

```text
$ repo-brief context --brief "save migration"
Read (26 KB):
  src/ui/settings.gd:1-120   4.1 KB
  docs/status.md:25-233                    18 KB  # Current focus
```

What it must do: [SPEC.md](SPEC.md). Why it is shaped this way:
[DESIGN.md](DESIGN.md). Known limits: [LIMITATIONS.md](LIMITATIONS.md).

## Start

Install it once per machine. There is no package on PyPI:

```bash
uv tool install "git+https://github.com/GianiStatie/repo-brief"
# the MCP server and the agent are optional:
uv tool install "git+https://github.com/GianiStatie/repo-brief" --with mcp --with claude-agent-sdk
```

Then, in a repository:

```bash
repo-brief init                        # adapter, skills, map, overview, tools
repo-brief context --brief "..."       # ask where something lives
repo-brief update                      # after edits or a pull
```

`init` writes the adapter only when the repo has none, installs the two skills,
and builds `.repo-brief/map.md`, `.repo-brief/overview.md` and
`.repo-brief/tools.json`. Re-run `init` or `update` whenever the tree moves.

Two flags change what `init` does:

```bash
repo-brief init --local --trust
```

- `--local` keeps the derived layer out of git: the adapter and profile live in
  `.repo-brief/`, and `docs/meta/` is excluded. `.repo-brief/local.json` tells
  detection to read `docs/meta/` anyway, so the decision log still ranks.
- `--trust` lets repo-brief run the repo's own adapter and scripts. It never
  blocks the map, `context`, `graph` or `ask`; without it, the generic adapter
  is used and the repo's own tools stay off.

## What it writes

| Path | What | Committed |
|---|---|---|
| `.repo-brief/map.md` | every file, its symbols, tests and decisions | yes |
| `.repo-brief/overview.md` | stack, layout, areas, entry points, docs, commands | yes |
| `.repo-brief/profile.json` | what detection saw when the adapter was written | yes |
| `.repo-brief/tools.json` | the repo's scripts and their groups | yes |
| `tools/brief_adapter.py` | the repo's judgements (under `.repo-brief/` with `--local`) | yes / no |
| `.repo-brief/index.sqlite` | local word index, rebuilt on demand | no |
| `.repo-brief/parse-cache.json` | local parse cache, rebuilt on demand | no |

## Commands

| Command | What it does |
|---|---|
| `init [--local] [--trust]` | set a repo up in one command |
| `update` | refresh index, map, profile, overview and tools |
| `index [--check]` | write, or verify, the map structure |
| `context "<q>" [--brief] [--json]` | rank what matters and return a read plan |
| `graph [area]` | dependency view; `--task <q>` for a query's subgraph |
| `preflight` | stale map, stale profile, missing front doors, broken `res://` refs |
| `doctor` | what detection and the adapter see |
| `archive [--apply PATH...]` | review, or move, finished docs |
| `tools [--set ID GROUP]` | list the repo's scripts and their groups |
| `scaffold` / `profile` / `writing` / `skill` | the manual steps behind `init` |
| `serve` / `connect` / `ask` / `log` | the MCP face (below) |

## One MCP server per repo

An orchestrator adds each repository once. There is no wrapper code per repo.

```bash
repo-brief serve                       # stdio, started by the client
repo-brief serve --http --port 8765    # or a long-lived HTTP server
repo-brief connect --client claude     # print the config; --apply runs it
repo-brief connect --client opencode
repo-brief ask "how does saving work?" # the same agent, from a terminal
```

The server exposes `context`, `graph`, the repo's command catalog and `ask`, and
serves the overview and map as `repo://overview` and `repo://map`.
`connect` knows Claude Code and OpenCode.

`ask` runs a read-only Claude Agent SDK session inside the repo. It gets
repo-brief's tools and, in a trusted repo, the `inspect` group; never `dev` or
`debug`. Every ask is logged locally (session, time, cost) and never committed.

## Tool groups

A repo's scripts are grouped, so adding a repo adds a few tools, not forty.

| Group | What it holds | Exposed | `ask` may use it |
|---|---|---|---|
| `brief` | repo-brief itself | yes | yes |
| `inspect` | read-only checks and views | yes | trusted repos |
| `dev` | build, test, run, train | yes | never |
| `debug` | look inside a running game or model | yes | never |
| `manual` | writes files, touches secrets, publishes | never | never |

`init` and `update` guess the group by reading each script; the guess is a
judgement in `.repo-brief/tools.json`. Change one with
`repo-brief tools --set <id> <group>`.

## The two loops

`init` installs two skills into `.claude/skills/`, which Claude Code, the Agent
SDK and OpenCode all read:

- **retrieve** — start with `context --brief`, read the planned slices, budget
  screenshots and test runs, record where the brief fell short.
- **document** — capture the owner's words verbatim, then write the linked
  summary in the decision log, backlog or status.

The repo-specific half lives in `docs/meta/writing.md`. Edit the skill in the
tool, never the installed copy: `skill --check` and `preflight` fail on drift.

## Adopting by hand

`scaffold` is the manual path behind `init`. It writes
`tools/brief_adapter.py`, a thin `GenericAdapter` subclass with two `TODO`s:
where decisions live, and what counts as open work. A machine can find
`docs/decisions/`; it cannot know that `docs/design/bugs.md` is authoritative
and `docs/notes/wishlist.md` is not.

The kernel does every bit of ranking; the adapter only declares structure.
[DESIGN.md](DESIGN.md) explains the split and where to change what.

## Using it from another repo

The adapter imports the package, so install it or put it on `PYTHONPATH`. A git
submodule at `tools/vendor/repo-brief` works too.

A repo with a Makefile can include the shipped fragment at the bottom:

```make
REPO_BRIEF ?= tools/vendor/repo-brief
-include $(REPO_BRIEF)/make/repo-brief.mk
```

It supplies `index`, `index-check`, `init`, `update`, `context`, `graph`,
`preflight`, `archive`, `writing`, `skill`, `trust` and `serve`.

## Upgrading

A release can change what the map's structure fingerprint covers, so every
existing map reads as stale once:

```bash
repo-brief index          # rewrite the map; commit it if the repo commits it
repo-brief skill --check  # then `repo-brief skill` if a shipped skill changed
```

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## License

MIT. See [LICENSE](LICENSE).
