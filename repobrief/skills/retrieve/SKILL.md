---
name: retrieve
description: Use before exploring a repository or answering "where does this live" — read the smallest slice that decides the next action, budget screenshots and test runs, and record where the brief fell short.
---

# Retrieve

The map and the brief are the cheap path. Reading the tree is the expensive one.
Use this before you search, read, screenshot, or run a broad test.

## Start with the brief

```bash
repo-brief context --brief "<the task, in the words you would ask it>"
```

Read the `Read` plan. It names a path, a line range, and a size. Those are a
starting point, not a fence: if the answer is not in the slice, widen it once,
then stop and state what is missing.

If the repo wraps the tool (`./tools/dev.sh context ...`), use the wrapper.

## Read the smallest thing that decides the next action

- Read the planned slice first. Do not read a whole file because it is open.
- For a file over ~24 KB, search it (`grep -n`) and read the matching range.
- For a doc over ~12 KB, read the heading section, not the document.
- A status page is current state, a changelog is history, a decision log is
  rationale. Do not read all three to answer one question.
- Before re-reading a file, check whether it changed since your last read. If
  it did not, use what you already have.
- Keep a working set: 3–5 paths, the invariants they carry, and the open
  question. Re-derive only when the set is wrong.

## Search cheaply

- `grep -l` / files-with-matches first, then read one hit. A repo-wide
  full-line dump buries the two lines that matter.
- Use the file type and directory filters the repo already has.
- For "where does this live", ask the brief: `repo-brief context --brief
  "<Name>"` names the path and the slice to read.
- For what a class extends, or what a directory holds, grep the map
  (`.repo-brief/map.md`). Its rows name files under `###` directory headings,
  so grep a class or directory name, not a full path.
- For how areas depend on each other, `repo-brief graph <area>`.
- Stop searching when two independent sources agree.

## Budget the expensive evidence

- **Screenshots:** prefer a text tree, node dump, or property read. If pixels
  are the only evidence, capture once, crop to the changed region, and do not
  re-read an unchanged image.
- **Tests:** run the focused suite while iterating; run the full suite once
  before finishing. Read the failure block, not the tail of the log.
- **Broad reviews:** split by area and delegate; a 100-file diff read linearly
  in one context is the most expensive shape.
- **Time:** if a task is still exploring after two rounds of retrieval, write
  down the hypothesis and test it instead of reading more.

## When the brief is wrong

Do not silently work around a limitation. Record it where the next agent will
look:

- In this repo: append to the notes file named in `docs/meta/writing.md`
  (usually `docs/meta/repo-brief-notes.md`) — date, query, expected, actual,
  workaround.
- In `repo-brief` itself: add the recurring lesson to `LIMITATIONS.md`.

A workaround that is not written down is rediscovered next session.

## Boundaries

- Do not edit `.repo-brief/map.md`; fix the adapter or the source.
- Do not treat the brief as proof. It is an index; the file is the evidence.
- Do not commit; leave the tree for the owner.
