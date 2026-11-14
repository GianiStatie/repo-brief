# Backlog

Improvements that evaluation pointed to and that are not built yet. Each names
what went wrong, the change, and how to tell whether it worked. What repo-brief
deliberately does not do, and the limits met in practice, are in
[LIMITATIONS.md](LIMITATIONS.md). Like every file here, this one is public: see
`AGENTS.md`.

## Wrong facts from look-alike names

The kernel ranks by words, not meaning. When the code has a look-alike, a name
whose words match the question but that means something else, the read plan
puts it first, and an agent that reads less because of the plan can take it as
the answer. In evaluation sessions, an agent with the tools built its answer on
such a field where the same agent without them searched more widely and found
the right one; another said something did not exist without searching for it.
The tools state no facts, but they choose what the agent reads first.

- [ ] **Warn about look-alikes.** One line in the server's instructions and the
  `retrieve` skill: a read plan matches words, not meaning, so check that a name
  means what the question means before relying on it, and search before saying
  something does not exist. Check: wrong facts in a sessions run, with and
  without the line.
- [ ] **Name look-alikes in the overview's glossary.** `overview --prompt` asks
  for each domain word with the code's name for it and, where one exists, the
  name it is not: "who controls a Stand → `standUser`, not `StandController`".
  The server sends the overview at the start of every session, so the agent
  starts from the right name. Check: the same comparison.

## Search and the index

- [ ] **Settle the search's scope.** Since 0.4.1, `search` reads views, config
  and tests beside the code, and agents opened more of them. One more sessions
  run of the code-only search and of the wider one tells whether that costs
  more; if it does, read the code by default, with an option to widen.
- [ ] **A smaller index.** Count the words of files no parser reads only inside
  the folders `search` reads.
- [ ] **Fold accents**, so a query typed without them finds `Kakyōin`. No
  language setting is needed for that.
- [ ] **A language setting**, English by default, only if a stemmer for another
  language is ever wanted: the plural rule is the only English part.

## Server

- [ ] **Reload after `update`.** A running server keeps the file list it started
  with, so a new file reaches it only after a restart. The replay gives each
  commit an adapter of its own; a server could start a new one when the map
  changes.
