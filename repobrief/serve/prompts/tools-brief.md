## repo-brief

- `repo_brief_context` ranks the files that matter for a query and returns a
  read plan: files with line ranges and sizes, then the lines that matched. Read
  the ranges it gives before searching on your own.
- Search in two steps. First query with the question's own words ("how are old
  saves upgraded"): the ranking reads what the code says as well as its names,
  so you don't need to know the names yet. The plan shows the classes and
  functions involved; query again with those ("SaveMigration version") to drill
  down, and Grep for callers and for what the plan doesn't cover.
- `repo_brief_graph` shows how the repository's areas depend on each other, or,
  given an area, the files inside it.
- `repo_brief_dependents` lists the files that use a class, by directory: for
  "what uses X" and what a change to X reaches. Templates, config and method
  calls aren't in it: Grep for those.
- `repo_brief_overview` returns the overview at the end of these instructions.
