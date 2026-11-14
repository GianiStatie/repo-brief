<p align="center">
  <img src="assets/logo.svg" width="104" alt="A pair of white boxer briefs with red hearts and three index rows">
</p>

# repo-brief

A map of your repository, a ranked answer to "where does this live", and a check
that tells you when either has gone stale.

It runs as a CLI, or as one MCP server per repo that an agent (Claude Code,
OpenCode, an orchestrator) calls directly. It runs no model of its own. The core
is standard library only, Python 3.9+.

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
`graph` is a view over the same units, not a fourth artifact, and `search`
ranks the same files by the words they contain.

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
# the MCP server is an optional extra:
uv tool install "repo-brief[mcp] @ git+https://github.com/GianiStatie/repo-brief"
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
  all it gates. The map, the adapter, `context`, `search`, `graph` and `serve`
  work the same without it; the scripts just stay off.

## What it writes

| Path | What | Committed |
|---|---|---|
| `.repo-brief/map.md` | every file, its symbols, tests and decisions | yes |
| `.repo-brief/overview.md` | stack, layout, areas, entry points, docs, commands | yes |
| `.repo-brief/profile.json` | what detection saw when the adapter was written | yes |
| `.repo-brief/tools.json` | the repo's scripts and their groups | yes |
| `.repo-brief/serve.json` | settings for `serve`: how long a script may run | yes |
| `.repo-brief/facts.json` | the adapter's fact cache, rebuilt on demand | no |
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
| `overview --prompt` | print the instructions for your agent to write the overview's written half: purpose, domain words, main flows (`update` keeps it) |
| `index [--check]` | write, or verify, the map structure |
| `context "<q>" [--brief] [--json]` | rank what matters and return a read plan |
| `search "<words>" [--json]` | rank the code files by the words they contain (BM25), each with its best lines |
| `graph [area]` | dependency view; `--task <q>` for a query's subgraph, `--dependents <class>` for what uses a class |
| `preflight` | stale map, stale profile, missing front doors, broken `res://` refs |
| `doctor` | what detection and the adapter see |
| `archive [--apply PATH...]` | review, or move, finished docs |
| `tools [--set ID GROUP]` | list the repo's scripts and their groups |
| `scaffold` / `profile` / `writing` / `skill` | the manual steps behind `init` |
| `facts [words] [--id ID] [--kind K]` | search the named things the adapter extracts |
| `log [--enable] [--calls]` | turn on, or read, the optional record of what clients call |
| `log import [--from claude\|opencode]` | copy questions and tool calls from Claude Code and OpenCode sessions |
| `log eval [--max N] [--split S] [--save P] [--compare P]` | replay imported questions at the commit of the moment and score the plan |
| `serve` / `connect` | the MCP face (below) |

## One MCP server per repo

An agent or an orchestrator adds each repository once. There is no wrapper code
per repo.

```bash
repo-brief serve                       # stdio, started by the client
repo-brief serve --groups brief,inspect   # only some of the script groups
repo-brief serve --http --port 8765    # or a long-lived HTTP server on loopback
REPO_BRIEF_HTTP_TOKEN=... repo-brief serve --http --host 0.0.0.0 --allowed-host repo.example.com
repo-brief connect                     # print how to register it, for Claude Code and OpenCode
repo-brief connect --into ~/orchestrator --apply   # register it in that project, never globally
```

The server offers `repo_context`, `repo_search`, `repo_graph`, `repo_dependents`
(the files that use a class), `repo_tools`, `repo_facts` and `repo_fact`, plus
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

The client's agent does the reading and the answering. `repo_context` turns a
question in plain words into a read plan; `repo_search` ranks the code files by
the words they contain (BM25), for the names the code itself uses: a class, a
method, a table, a message key. Both return paths, lines and sizes for the agent
to open. The overview's written half, what the project is for and the words its
domain uses, is the agent's to write too: `repo-brief overview --prompt` prints
the instructions, and the server sends the section at the start of every
session. Up to 0.3.0 repo-brief ran an agent of its own (`ask`, `repo_ask`);
0.4.0 removed it, since a client that has these tools investigates for itself
and a second model run in between only adds cost.

After `repo-brief log --enable`, every tool a client calls over MCP in that
repository is recorded on this machine. `repo-brief log import` adds the questions and tool calls from
Claude Code's and OpenCode's own session stores; importing again refreshes them
rather than duplicating anything. File contents are never copied. Leave
`--from` off: reading one store at a time cannot de-duplicate a conversation
that both stores recorded.

## Tool groups

A repo's scripts are grouped, so adding a repo adds a few tools, not forty.

| Group | What it holds | Served |
|---|---|---|
| `brief` | repo-brief itself | every repo |
| `inspect` | scripts that only read and report | trusted repos |
| `dev` | build, test, run, benchmark, train | trusted repos |
| `debug` | look inside a running game or model | trusted repos |
| `manual` | writes on its own, publishes, network, secrets, or unclear | never |

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

## Contributing

The repository is public, so read [AGENTS.md](AGENTS.md) first: examples and
fixtures use JoJo's Bizarre Adventure names, never anything from another
repository.

## License

MIT. See [LICENSE](LICENSE).
