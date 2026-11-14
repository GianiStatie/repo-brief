# Spec

What repo-brief must do. [DESIGN.md](DESIGN.md) explains why it is shaped this
way, [README.md](README.md) how to use it, and [LIMITATIONS.md](LIMITATIONS.md)
what it deliberately leaves out. When the code and this file disagree, one of
them has a bug: fix the code, or change this file on purpose.

## Two faces

- **The CLI** uses the standard library only, on Python 3.9 or later. Every
  deterministic capability lives here: `init`, `update`, `index`, `context`,
  `search`, `graph`, `overview`, `tools`, `preflight` and the rest.
- **The MCP server** (`repo-brief serve`) offers the same capabilities to an
  agent or an orchestrator, one server per repository. It needs the optional
  extra `repo-brief[mcp]`.

repo-brief runs no model. The client's agent searches, reads and answers with
these tools; up to 0.3.0 repo-brief ran an agent of its own (`ask`), and 0.4.0
removed it.

## Layout

### In the repository

| Path | What it holds | Written by | Committed |
|---|---|---|---|
| `tools/`, `scripts/` | the repository's own scripts | you | yes |
| `tools/brief_adapter.py` | the adapter: this repository's judgements, as Python | `init`, `scaffold`, then you | yes |
| `.repo-brief/map.md` | every file, what it declares, its tests; decisions and open work | `index`, `update` | yes |
| `.repo-brief/overview.md` | detected facts, plus a written half that updates keep | `update`, `overview`; the written half by your agent | yes |
| `.repo-brief/profile.json` | what detection saw, and the judgements still to make | `init`, `update`, `profile` | yes |
| `.repo-brief/tools.json` | the scripts detected, and your choices: script folders and groups | `init`, `update`, `tools` | yes |
| `.repo-brief/serve.json` | settings for `serve` | `init` writes the defaults, then you | yes |
| `.repo-brief/parse-cache.json`, `.repo-brief/cache.json` | caches, rebuilt on demand | any command | no |
| `.repo-brief/facts.json` | the adapter's fact cache, rebuilt on demand | `facts`, the MCP facts tools, `update` | no |
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
| `repobrief/serve/` | the MCP server, the tools it offers, and its settings; only `serve` imports the part that needs the extra |

## Trust

Trust answers one question: may repo-brief run this repository's scripts?

- A script is a file in a script folder (`tools/` and `scripts/` unless
  `tools.json` says otherwise, plus `.repo-brief/tools/` in a local-only
  repository), a subcommand of `tools/dev.sh` or `dev.sh`, or a documented
  Makefile target (`target: ## what it does`).
- Trust is recorded on the machine, with `repo-brief trust` or `init --trust`,
  never in the repository: a repository cannot vouch for itself.
  `trust --revoke` removes it.
- Trust gates nothing else. Mapping, the adapter, `context`, `search`, `graph`,
  `overview` and `serve` work the same in an untrusted repository; there the
  script groups are hidden.
- The adapter is Python, and it runs whenever repo-brief reads the repository.
  Treat `init` on an unfamiliar repository like running its Makefile.
- A script runs only when the repository is trusted, the script is in the
  catalog, its path is inside a script folder of this repository, and its
  group is one the caller may use.

## Tool groups

| Group | Holds | Served |
|---|---|---|
| `brief` | repo-brief's own tools | every repository |
| `inspect` | scripts that only read and report | trusted repositories |
| `dev` | build, test, run, benchmark, train | trusted repositories |
| `debug` | look inside a running game or model | trusted repositories |
| `manual` | anything that writes files on its own, publishes, touches the network or secrets, or is unclear | never |

- `init` and `update` place each script by reading it, never by running it.
  A Python file in a script folder is a script when it has a `__main__` guard
  or code at the top level; a module of definitions is a helper, not a command.
  What a script imports and calls decides, together with every module of this
  repository it imports, directly or not, in this order:
  1. unreadable, or calling names it never defines or imports (a script for
     another runtime, which injects those names) → `manual`
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

## Facts

Some repositories have named things worth knowing by name: feature flags,
settings, routes. An adapter may extract them, and repo-brief then answers
"what matches these words?" and "where is this one declared and used?".

- `Adapter.facts()` returns dicts with `id` (`<kind>:<name>`), `kind`, `name`,
  and optional `summary`, `tickets`, `tags`, `declared_at`, `used_at` and
  `mentioned_at`; each place is `{path, line, text, where, how}`.
  `fact_notes()` gives caveats per kind, and `fact_inputs()` names the files the
  cache is keyed on; an empty `fact_inputs` means every detected file.
- The cache is `.repo-brief/facts.json`, git-ignored and rebuilt when the
  adapter's code, an input file's size or modification time, or this module's
  format changes. Deleting it costs one rebuild.
- `repo-brief facts` lists them, `facts <words>` searches names, summaries,
  tickets and tags (then the places that use them), `facts --id ID` opens one,
  `--kind` narrows it, and `--rebuild` reads the files again. `update` refreshes
  the cache when it exists.
- The server offers `repo_facts` and `repo_fact`. A fact says where something
  is declared and used, not whether it is on: that state usually lives outside
  the repository.
- `context` adds a one-line pointer when the query matches a fact or names a
  kind ("flags"). It reads the cache and never builds it, so the line appears
  once `facts` has run.

## The MCP server

`repo-brief serve` serves one repository, over stdio by default (the transport
an MCP client starts) or streamable HTTP with `--http`.

- Instructions: the repository's name, the overview's opening paragraph, what
  each tool is for, and the overview's written half once someone has written
  it. That half is cut at 2,500 characters, so the whole fits in the 4,096
  that Claude Code keeps.
- Resources: `repo://overview` and `repo://map`.
- Tools: `repo_context`, `repo_search`, `repo_graph`, `repo_dependents`,
  `repo_tools` (the catalog), `repo_facts` and `repo_fact`, and one tool per
  exposed script group. Every tool that only reads says so (`readOnlyHint`).
- A read that raises is tried once more, then answered with a sentence naming
  the tool and the error. A tree can change while it is read; a client never
  gets a bare MCP failure.
- A brief that matches nothing says which words it searched, split the way the
  index splits a name, so a name it could not find is visible at a glance.
- `repo_dependents` (and `graph --dependents`) takes a class name, a qualified
  class name or a path, and lists the files that import, extend or name it, by
  directory, subclasses marked. It reads the graph's links, so a template, a
  config file or a method call that names the class doesn't count.

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

## Search

`repo-brief search` (and `repo_search`) ranks the files by the words they
contain, for the names the code itself uses: a class, a method, a table, a
message key. `context` serves a question in plain words; `search` serves those.

- The ranking is Okapi BM25 alone, with no name ranking and no fusion, over
  every file with words in the modules that hold the product code: code, tests,
  config, views and notes. A module is the folder above a usage root's `src`
  (`web/` for `web/src/main/java`), or the folder above a usage root without
  one; when that is the repo itself, or the adapter names no usage root, the
  whole repo is read. Archived, generated and binary files are left out.
- Words are split at CamelCase, snake_case and digits (`stand2user` is also
  `stand` and `user`), and a plural matches its singular either way by a light
  English rule (`endpoint` finds `Endpoints`). The query is split the way
  `context` splits it, the whole name first and then its pieces.
- Each file comes with its size and up to three lines, in file order: those
  that name the most query words, the earlier line first among equals.
- `--limit` (`limit`) is 10 by default and 50 at most; `--json` prints the
  query, its words and each file's path, score, size and lines.
- It reads the index when it is on and the files otherwise, and ranks the same
  either way.

## The overview's written half

The overview's written half says what the project is for, the words its domain
uses with the code's names for them, and its main flows. The server sends it at
the start of every session.

- repo-brief never calls a model: the user's agent writes the half.
  `overview --prompt` prints the instructions for it: replace what is between
  the two markers, in under 2,400 characters of Markdown.
- `update` keeps the half verbatim. It replaces a placeholder, an earlier
  version's included, with the current one, and the server leaves a
  placeholder out of its instructions.

## Optional stores

repo-brief keeps no database unless you ask for one. A command you run creates
the store, and from then on it is kept current; deleting its file turns it off.

| Store | Turn it on | What it holds |
|---|---|---|
| Index, `.repo-brief/index.sqlite` | `init --index` or `update --index` | per file: size, modification time, parsed fields, symbols, its length in words, and the words it uses with how often |
| Log, `~/.local/share/repo-brief/<repo>-<hash>.sqlite` | `repo-brief log --enable` | per question: the question, the client, turns, time, cost, status and commit, and the answer when there was one; per tool call: tool, input, result size, time and error |

- Without the index, `context` reads the files on every call, through the parse
  cache. With it, a file is read again only when its size or modification time
  changes, `context` opens only the files a query can hit, and usage lines match
  whole words and their prefixes rather than any substring.
- `context` ranks code by what it says as well as by its names, the same way
  with or without the index: the word counts come from the index, or from the
  text read for the call. A file no parser reads, such as TypeScript or a
  properties file, is counted too; a binary or generated one is not. A format
  change rebuilds the index on its next use.
- Both stay on the machine, and neither is ever committed.

### The log's sources

- **Ask**, up to 0.3.0: what repo-brief's own agent ran, with its tool calls
  and what it cost. 0.4.0 has no agent; a log kept before it still holds them.
- **MCP**: every `tools/call` a client makes itself, with the client's name and
  the session id it sends (`ai.opencode/sessionID` for OpenCode), recorded while
  the log is on.
- **Import**: `repo-brief log import` reads Claude Code's session files for this
  directory (`~/.claude/projects/`, or `CLAUDE_CONFIG_DIR`) and OpenCode's
  sessions for this project, and copies the questions and the tool calls
  between them. It creates the log when it is off, since importing is an
  explicit request to keep this history; `--from claude|opencode` narrows it.
  - Only file contents are never copied: a write's or an edit's body is
    dropped, long arguments are cut, and paths inside the repository become
    relative.
  - Each question has an external id. Importing again adds nothing twice and
    refreshes a question that grew since it was imported, which is how a
    session still running is completed later.
  - An OpenCode session that ran Claude Code records the Claude session's id on
    its tool calls, so that session is not imported from both stores.
  - That check needs both stores in one run. `--from claude` followed later by
    `--from opencode` can import a harness conversation twice, because the two
    stores name it differently; the default `all` reads both together.

### Replaying the log

`repo-brief log eval` replays imported questions against the tree they were
asked on: the commit before the question's timestamp (`git rev-list -1
--before`), written into a temporary directory with `git archive`. Ranking and
plan size get a before-and-after on real questions instead of invented ones.

- Each question is asked twice: with the brief call the session made
  (`repo-brief context`, a `dev.sh context` wrapper, `make context q=...` or
  the `repo_context` tool), and with the question as typed. The plan is scored
  against the files the session edited afterwards; whether it named a file the
  session read is reported separately, as a guard.
- Only files inside the repository are graded. A path outside it, a scratch
  file for instance, is one no brief could have named.
- Each commit is read by an adapter of its own. An adapter keeps what it
  detected, file list included, so one shared across commits would rank every
  tree against the first one's files.
- `--max N` takes the newest N questions (200 by default, `0` for all), so one
  run stays a few minutes.
- The newest third of the questions is held out. `--split holdout` or `--split
  tune` reports one side; the default reports both together.
- `--save PATH` writes the rows as JSON. `--compare PATH` prints the delta
  against a saved run, matched by question and style, including the questions
  that lost a plan hit.
- `--at-head` replays at HEAD, which is faster but not a fair before-and-after:
  files a session created already exist there.
- A question with no commit before it, or whose turn touched no file, is
  counted and left out, never guessed at.

## Not built yet

Each line leaves this list when it lands.

- `tools suggest`, `tools accept` and `tools reject`.
- The `lookup` tool, Godot scene tools, and a git hook that runs `update` after
  a pull.
