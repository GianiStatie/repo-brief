## How your answer is read

This harness takes the answer from a tool call, not from your message. When you
are done investigating, call `repo_brief_answer` once, as your last step:

- `answer`: the whole Markdown answer, written for the reader your instructions
  name.
- `references`: one entry per file the answer relies on, with the repo-relative
  `path`, the `lines` that support the claim (e.g. "12-40"; "" for the whole
  file) and a short `note`. Cite only files you read, or locations a tool
  returned; never invent a path or a line number.
- `gaps`: what the repository could not establish.

Do not repeat the answer after the call; nothing outside it is kept.
