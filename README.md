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
# the MCP server and the Claude runner for `ask` are optional extras:
uv tool install "repo-brief[mcp,claude] @ git+https://github.com/GianiStatie/repo-brief"
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

On a large repository, `repo-brief update --index` turns on an optional local
index: files are re-read only when they change, and `context` opens only the
files a query can hit. It never changes the map. Delete
`.repo-brief/index.sqlite` to turn it off.

Two flags change what `init` does:

```bash
repo-brief init --local --trust
```

- `--local` keeps the derived layer out of git: the adapter and profile live in
  `.repo-brief/`, and `docs/meta/` is excluded. `.repo-brief/local.json` tells
  detection to read `docs/meta/` anyway, so the decision log still ranks.
- `--trust` lets repo-brief run the repo's scripts: the files in `tools/` and
  `scripts/`, `dev.sh` subcommands and documented Makefile targets. That is
  all it gates. The map, the adapter, `context`, `graph`, `serve` and `ask`
  work the same without it; the scripts just stay off.

## What it writes

| Path | What | Committed |
|---|---|---|
| `.repo-brief/map.md` | every file, its symbols, tests and decisions | yes |
| `.repo-brief/overview.md` | stack, layout, areas, entry points, docs, commands | yes |
| `.repo-brief/profile.json` | what detection saw when the adapter was written | yes |
| `.repo-brief/tools.json` | the repo's scripts and their groups | yes |
| `.repo-brief/serve.json` | settings for `serve` and `ask`: runner, model, caps | yes |
| `.repo-brief/facts.json` | the adapter's fact cache, rebuilt on demand | no |
| `.repo-brief/prompt.md` | optional: notes `ask` should know, written by you | yes |
| `tools/brief_adapter.py` | the repo's judgements (under `.repo-brief/` with `--local`) | yes / no |
| `.repo-brief/index.sqlite` | the optional index, only after `--index` | no |
| `.repo-brief/parse-cache.json` | local parse cache, rebuilt on demand | no |

Nothing else is written outside the repository unless you ask:
`~/.config/repo-brief/trust.json` after `repo-brief trust`, and the optional log
after `repo-brief log --enable`. [SPEC.md](SPEC.md#layout) has the full layout.

## Commands

| Command | What it does |
|---|---|
| `init [--local] [--trust] [--index]` | set a repo up in one command |
| `update [--index]` | refresh the map, profile, overview, tools and the index if it is on |
| `overview --write` | have `ask`'s agent write the overview's written half: purpose, domain words, main flows (a model call; `update` keeps it) |
| `index [--check]` | write, or verify, the map structure |
| `context "<q>" [--brief] [--json]` | rank what matters and return a read plan |
| `graph [area]` | dependency view; `--task <q>` for a query's subgraph, `--dependents <class>` for what uses a class |
| `preflight` | stale map, stale profile, missing front doors, broken `res://` refs |
| `doctor` | what detection and the adapter see |
| `archive [--apply PATH...]` | review, or move, finished docs |
| `tools [--set ID GROUP]` | list the repo's scripts and their groups |
| `scaffold` / `profile` / `writing` / `skill` | the manual steps behind `init` |
| `facts [words] [--id ID] [--kind K]` | search the named things the adapter extracts |
| `log [--enable] [--calls]` | turn on, or read, the optional record of asks |
| `log import [--from claude\|opencode]` | copy questions and tool calls from Claude Code and OpenCode sessions |
| `serve` / `connect` / `ask` | the MCP face (below) |

## One MCP server per repo

An orchestrator adds each repository once. There is no wrapper code per repo.

```bash
repo-brief serve                       # stdio, started by the client
repo-brief serve --groups brief,inspect   # only some of the script groups
repo-brief serve --http --port 8765    # or a long-lived HTTP server on loopback
REPO_BRIEF_HTTP_TOKEN=... repo-brief serve --http --host 0.0.0.0 --allowed-host repo.example.com
repo-brief connect                     # print how to register it, for Claude Code and OpenCode
repo-brief connect --into ~/orchestrator --apply   # register it in that project, never globally
repo-brief ask "how does saving work?" # the same agent, from a terminal
```

The server offers `repo_context`, `repo_graph`, `repo_dependents` (the files
that use a class), `repo_tools`, `repo_ask`, `repo_facts` and `repo_fact`, plus
one tool per script group the client may use
(`repo_inspect`, `repo_dev`, `repo_debug`), whose description lists that
group's scripts. The overview and map are resources, `repo://overview` and
`repo://map`. `connect` knows Claude Code and OpenCode.

Facts are the named things a repository's adapter extracts, such as feature
flags. `repo-brief facts gold experience` searches names, summaries, tickets
and tags; `repo-brief facts --id flag:GOLD_EXPERIENCE` shows where it is
declared, checked and named as data. They live in a git-ignored
`.repo-brief/facts.json` and are rebuilt when an input changes.

Over HTTP the endpoint is `/mcp`. Requests whose `Host` header names anything
other than loopback, the bound address or an `--allowed-host` are refused. Set
`REPO_BRIEF_HTTP_TOKEN` and every request needs `Authorization: Bearer <token>`;
binding beyond loopback without it is refused.

`ask` runs a read-only agent session inside the repo. Two runners ship: the
Claude Agent SDK (the default) and the `opencode` CLI; each agent CLI is one
runner class. To use OpenCode:

```bash
REPO_BRIEF_RUNNER=opencode REPO_BRIEF_MODEL=provider/model repo-brief ask "..."
```

With the OpenCode runner, a model without a `/` means OpenCode's own default;
the Claude model name is not passed to it. The agent gets repo-brief's
tools, the adapter's facts and, in a trusted repo, the `inspect` scripts; never
`dev` or `debug`. It
answers with references, gaps and a session id for a follow-up, flags any
reference to a file or lines that don't exist, and over MCP it
reports progress as it goes. Its model, turn, budget and time limits live in
`.repo-brief/serve.json` (environment variables override them), and
`.repo-brief/prompt.md` adds notes about the repo to its instructions.

After `repo-brief log --enable`, every ask in that repository is recorded on
this machine with its tool calls, time and cost, and so is every tool a client
calls over MCP. `repo-brief log import` adds the questions and tool calls from
Claude Code's and OpenCode's own session stores; importing again refreshes them
rather than duplicating anything. File contents are never copied. Leave
`--from` off: reading one store at a time cannot de-duplicate a conversation
that both stores recorded.

## Tool groups

A repo's scripts are grouped, so adding a repo adds a few tools, not forty.

| Group | What it holds | Orchestrator | `ask` |
|---|---|---|---|
| `brief` | repo-brief itself | every repo | every repo |
| `inspect` | scripts that only read and report | trusted repos | trusted repos |
| `dev` | build, test, run, benchmark, train | trusted repos | never |
| `debug` | look inside a running game or model | trusted repos | never |
| `manual` | writes on its own, publishes, network, secrets, or unclear | never | never |

`init` and `update` place each script by reading its code, never by running it:
what it imports and calls, including the repo's own modules it imports, decides.
A script that writes files, uses the network or a secret, or publishes lands in
`manual`; one that runs Godot, a subprocess or a training library lands in
`dev`; one that only reads lands in `inspect`. Words in its name can only make
the guess safer. `repo-brief tools` shows each guess with its evidence, and
`repo-brief tools --set <id> <group>` overrides one; later scans keep your
choice. The full rules are in [SPEC.md](SPEC.md#tool-groups).

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

A release can also change the optional index's format. The next command that
uses the index rebuilds it once, so run `repo-brief update` after upgrading
rather than leaving the rebuild to the first `context` call.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## License

MIT. See [LICENSE](LICENSE).
