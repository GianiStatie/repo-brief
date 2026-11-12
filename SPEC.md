# Spec

What repo-brief must do. [DESIGN.md](DESIGN.md) explains why it is shaped this
way, [README.md](README.md) how to use it, and [LIMITATIONS.md](LIMITATIONS.md)
what it deliberately leaves out. When the code and this file disagree, one of
them has a bug: fix the code, or change this file on purpose.

## Two faces

- **The CLI** uses the standard library only, on Python 3.9 or later. Every
  deterministic capability lives here: `init`, `update`, `index`, `context`,
  `graph`, `overview`, `tools`, `preflight` and the rest.
- **The MCP server** (`repo-brief serve`) offers the same capabilities to an
  orchestrator, one server per repository, plus `ask`. It needs optional
  extras: `repo-brief[mcp]` for the server, `repo-brief[claude]` for the
  Claude runner.

## Layout

### In the repository

| Path | What it holds | Written by | Committed |
|---|---|---|---|
| `tools/`, `scripts/` | the repository's own scripts | you | yes |
| `tools/brief_adapter.py` | the adapter: this repository's judgements, as Python | `init`, `scaffold`, then you | yes |
| `.repo-brief/map.md` | every file, what it declares, its tests; decisions and open work | `index`, `update` | yes |
| `.repo-brief/overview.md` | detected facts, plus a written half that updates keep | `update`, `overview` | yes |
| `.repo-brief/profile.json` | what detection saw, and the judgements still to make | `init`, `update`, `profile` | yes |
| `.repo-brief/tools.json` | the scripts detected, and your choices: script folders and groups | `init`, `update`, `tools` | yes |
| `.repo-brief/serve.json` | settings for `serve` and `ask` | `init` writes the defaults, then you | yes |
| `.repo-brief/prompt.md` | optional notes for `ask` about this repository | you | yes |
| `.repo-brief/parse-cache.json`, `.repo-brief/cache.json` | caches, rebuilt on demand | any command | no |
| `.repo-brief/index.sqlite` | the optional index | `--index` | no |
| `.repo-brief/.gitignore` | the names of every file in the "no" rows | repo-brief | no |

A local-only repository (`init --local`) keeps all of this out of git:

- `.repo-brief/` and `docs/meta/` are excluded through `.git/info/exclude`.
- The adapter is `.repo-brief/adapter.py`, and `.repo-brief/tools/` is a
  script folder.
- `.repo-brief/local.json` names the excluded trees that detection still reads
  (`docs/meta/`), so a local decision log still ranks.

### On the machine

| Path | What it holds |
|---|---|
| `~/.config/repo-brief/trust.json` | the repositories whose scripts repo-brief may run |
| `~/.local/share/repo-brief/<repo>-<hash>.sqlite` | the optional interaction log, one per repository |

`REPO_BRIEF_CONFIG_DIR` and `REPO_BRIEF_DATA_DIR` move them; so do
`XDG_CONFIG_HOME` and `XDG_DATA_HOME`.

### In repo-brief

| Path | What |
|---|---|
| `repobrief/` | the core, standard library only |
| `repobrief/serve/` | the MCP server, `ask`, the tool list both share, and the settings; only `serve` and `ask` import the parts that need an extra |
| `repobrief/serve/runners/` | one class per agent CLI that can run `ask` |
| `repobrief/serve/prompts/` | the prompts every runner shares, as Markdown |

## Trust

Trust answers one question: may repo-brief run this repository's scripts?

- A script is a file in a script folder (`tools/` and `scripts/` unless
  `tools.json` says otherwise, plus `.repo-brief/tools/` in a local-only
  repository), a subcommand of `tools/dev.sh` or `dev.sh`, or a documented
  Makefile target (`target: ## what it does`).
- Trust is recorded on the machine, with `repo-brief trust` or `init --trust`,
  never in the repository: a repository cannot vouch for itself.
  `trust --revoke` removes it.
- Trust gates nothing else. Mapping, the adapter, `context`, `graph`,
  `overview`, `serve` and `ask` work the same in an untrusted repository; there
  the script groups are hidden, and `ask` uses only repo-brief's own tools.
- The adapter is Python, and it runs whenever repo-brief reads the repository.
  Treat `init` on an unfamiliar repository like running its Makefile.
- A script runs only when the repository is trusted, the script is in the
  catalog, its path is inside a script folder of this repository, and its
  group is one the caller may use.

## Tool groups

| Group | Holds | Orchestrator | `ask` |
|---|---|---|---|
| `brief` | repo-brief's own tools | every repository | every repository |
| `inspect` | scripts that only read and report | trusted repositories | trusted repositories |
| `dev` | build, test, run, benchmark, train | trusted repositories | never |
| `debug` | look inside a running game or model | trusted repositories | never |
| `manual` | anything that writes files on its own, publishes, touches the network or secrets, or is unclear | never | never |

- `init` and `update` place each script by reading it, never by running it.
  A Python file in a script folder is a script when it has a `__main__` guard
  or code at the top level; a module of definitions is a helper, not a command.
  What a script imports and calls decides, together with every module of this
  repository it imports, directly or not, in this order:
  1. unreadable, or calling names it never defines or imports (a script for
     another runtime, such as a deploy tool's scripting language) → `manual`
  2. a name or docstring that says it generates, publishes, releases, uploads,
     deploys, pushes, refreshes, installs or logs in → `manual`
  3. the network (an HTTP, cloud or mail client; a local socket does not
     count), secrets, or a command that publishes → `manual`
  4. a subprocess, a local server or a training library → `dev`
  5. writing files → `manual`
  6. otherwise it only reads → `inspect`

  Words that say debug, diagnose, trace, profile or watch turn a `dev` or
  `inspect` result into `debug`. Words never make a script look safer than its
  code, and the imported modules count whole, so a guess errs toward `manual`.
- A shell subcommand or Makefile target cannot be read that way. A name or
  summary that says test, build, run, export, bench, check or lint puts it in
  `dev`, a debugging word in `debug`, and anything else in `manual`; the
  `manual` words win over both.
- `tools.json` has two halves. `detected` is rewritten by every scan, with each
  script's group and the evidence for it. `folders` and `choices` are yours,
  and a scan never changes them; `repo-brief tools --set <id> <group>` records
  a choice.
- `init` and `update` name every new script and the group it landed in.
- The MCP server exposes each non-empty group the client may use as one tool:
  `repo_inspect`, `repo_dev`, `repo_debug`. Its description lists the group's
  scripts with their one-line summaries and options. `serve --groups
  brief,inspect` narrows the groups a client gets.
- `ask` never gets `dev` or `debug`, whatever flags a server starts with. The
  rule is enforced where `ask`'s tools are built, not in a runner's permission
  settings, and a test checks it.

## The MCP server

`repo-brief serve` serves one repository, over stdio by default (the transport
an MCP client starts) or streamable HTTP with `--http`.

- Instructions: the repository's name, the overview's opening paragraph, and
  what each tool is for.
- Resources: `repo://overview` and `repo://map`.
- Tools: `repo_context`, `repo_graph`, `repo_tools` (the catalog), `repo_ask`,
  and one tool per exposed script group. Every tool that only reads says so
  (`readOnlyHint`).

### Over HTTP

`serve --http` listens on `--host` (default `127.0.0.1`) and `--port` (default
8765); the endpoint is `/mcp`.

- A request whose `Host` header is not loopback, the address the server binds
  to, or a name given with `--allowed-host` is refused with 421. This is what
  stops a web page from reaching the server through DNS rebinding.
- When `REPO_BRIEF_HTTP_TOKEN` is set, every request must carry
  `Authorization: Bearer <token>`, and is refused with 401 otherwise.
- Binding to anything but loopback without that variable is refused at start:
  a reachable server would run scripts for whoever finds it.
- The token is read from the environment, never from a flag, so it does not show
  up in a process list.

### Registering with a client

`repo-brief connect` registers the server with Claude Code and OpenCode in one
orchestrator project, never globally.

- `--into DIR` names the project. `--apply` runs each client's own `mcp add`
  there: Claude Code in local scope (private to you, tied to that directory),
  OpenCode in the project's config. Without `--apply` it only prints the
  commands; `--apply` without an existing `--into` directory is refused.
- The command a client runs names repo-brief by its absolute path, because a
  client starts servers with its own `PATH`.
- `--name` sets the server's name, which is the repository's folder name by
  default.

## `ask`

`ask` runs a read-only agent session inside the repository and returns a
structured answer. It is `repo-brief ask` on the command line and `repo_ask` on
the server.

- The runner's own file tools only read, search and list. Its other tools are
  repo-brief's `brief` tools, plus the `inspect` group in a trusted repository.
- It never loads the repository's agent configuration: hooks, settings, MCP
  servers or plugins.
- Settings come from `.repo-brief/serve.json`, and environment variables
  override them:

  | Setting | Default | Variable |
  |---|---|---|
  | `runner` | `claude` | `REPO_BRIEF_RUNNER` |
  | `model` | `claude-haiku-5-5` | `REPO_BRIEF_MODEL` |
  | `max_turns` | 40 | `REPO_BRIEF_MAX_TURNS` |
  | `max_budget_usd` | 2.0 | `REPO_BRIEF_MAX_BUDGET_USD` |
  | `timeout_seconds` | 600 | `REPO_BRIEF_TIMEOUT` |
  | `concurrency` | 2 | `REPO_BRIEF_CONCURRENCY` |
  | `audience` | `developer` | `REPO_BRIEF_AUDIENCE` |
  | `script_timeout_seconds` | 600 | `REPO_BRIEF_SCRIPT_TIMEOUT` |

- The prompt is assembled from `repobrief/serve/prompts/`: the base
  instructions, a section for each tool family the agent has, the answer format
  for the audience, then this repository's overview and `.repo-brief/prompt.md`
  when they exist.
- The answer has `answer` (Markdown), `references` (path, lines, note), `gaps`,
  and a session id that a follow-up passes back to resume the session.
- Every answer carries a status: `ok`, `max_turns` or `max_budget` (a cap
  stopped the agent; the answer may be partial), `timeout`, or `error`.
- Asks beyond `concurrency` wait for a slot.
- While `repo_ask` runs, the server sends a progress notification for each
  tool the agent calls.

## Optional stores

repo-brief keeps no database unless you ask for one. A command you run creates
the store, and from then on it is kept current; deleting its file turns it off.

| Store | Turn it on | What it holds |
|---|---|---|
| Index, `.repo-brief/index.sqlite` | `init --index` or `update --index` | per file: size, modification time, parsed fields, symbols, and the words it uses |
| Log, `~/.local/share/repo-brief/<repo>-<hash>.sqlite` | `repo-brief log --enable` | per ask: question, answer, references, gaps, client, runner, model, audience, turns, time, cost, status and commit; per tool call the agent made: tool, input, result size, time and error |

- Without the index, `context` reads the files on every call, through the parse
  cache. With it, a file is read again only when its size or modification time
  changes, `context` opens only the files a query can hit, and usage lines match
  whole words and their prefixes rather than any substring.
- Both stay on the machine, and neither is ever committed.

## Not built yet

Each line leaves this list when it lands.

- The orchestrator's MCP tool calls in the log, and importing Claude Code and
  OpenCode sessions into it (`log import`).
- The OpenCode runner, and `serve --toolbox ask` to serve the agent's tools
  over stdio to runners that need them there.
- Facts: `Adapter.facts()`, stored in the index and served as `repo_facts` and
  `repo_fact`.
- `tools suggest`, `tools accept` and `tools reject`.
- The written half of the overview, the `lookup` and `dependents` tools, Godot
  scene tools, and a git hook that runs `update` after a pull.
