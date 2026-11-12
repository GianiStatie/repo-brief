## repo-brief

- `repo_brief_context` ranks the files that matter for a query and returns a
  read plan: files with line ranges and sizes, then the lines that matched. Read
  the ranges it gives before searching on your own.
- It matches the code's words, not product phrases: ask "SaveMigration version",
  not "how are old saves upgraded". Turn the question into class, function or
  package names first, and Grep for what the plan doesn't cover.
- `repo_brief_graph` shows how the repository's areas depend on each other, or,
  given an area, the files inside it.
- `repo_brief_overview` returns the overview at the end of these instructions.
