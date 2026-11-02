---
name: document
description: Capture what the repo owner says and turn it into structured repo docs — a verbatim entry in the capture tier plus a linked summary in the decision log, backlog, or status. Use when the owner states a requirement, preference, priority, rejection, correction, or decision.
---

# Document

The owner sets *what* and *why*; you own *when* and *how*. This skill records that
split so a later session can tell the two apart.

## Before you write

Read `docs/meta/writing.md` — it names this repo's decision log, open work,
front doors, and capture directory. If it is missing, run `repo-brief writing`
rather than guessing the layout.

Search before writing:

```bash
repo-brief context --brief "<the thing they said>"
```

If an existing decision already covers it, do not amend it. Add a dated follow-up.

## Two layers, one link

1. **Capture — evidence.** Their words, verbatim, appended to
   `docs/meta/capture/YYYY-MM-DD.md`. Never edit an existing entry; a correction
   is a new entry quoting the correction.
2. **Summary — the claim.** What it means for the work, in the doc that owns it:
   a dated decision entry, a backlog row, or a status line.

The summary links to the capture entry by heading:

```markdown
[[project/decisions#2026-11-02 — Expo build order]]     <!-- summary -->
```

```markdown
## C-2026-11-02-01 — Expo build order                    <!-- capture -->
```

If the two ever disagree, the quote wins.

## Entry format

```markdown
## C-YYYY-MM-DD-NN — <short title>

> "their words, exactly, including the typo"

Recorded YYYY-MM-DD HH:MM. Summary: [[project/decisions#YYYY-MM-DD — <short title>]]
```

Number entries from `01` within the day. Keep the quote to the sentence that
carries the decision, not the whole conversation.

## What counts

Capture when they decide, reject, prioritise, correct you, state a constraint, or
describe how they want to work. Do not capture musings, questions, or thinking
aloud unless they ask you to.

A request is not a decision until it is stated as one. "Maybe we could" is a
quote and a note; "do it" is a decision.

## Writing the summary

- Match the doc's existing shape — heading style, date format, frontmatter, link
  style. Follow what is there rather than inventing a format.
- Dated entries are append-only. Never rewrite an old stance; add today's.
- If open work is a table or checkboxes, follow that. Do not introduce a second
  convention.
- Link the summary from an index or backlog so it is discoverable. An unlinked
  entry is an orphan, and `repo-brief archive` will eventually call it one.

## Boundaries

- Never commit. Leave the working tree for the owner to review.
- Never invent intent. If you are inferring, mark it "inferred" and keep it out
  of the decision log.
- The capture tier is evidence, not a scratchpad. Do not write summaries into it.
