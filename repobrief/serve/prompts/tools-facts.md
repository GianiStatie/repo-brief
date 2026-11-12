## Extracted facts

This repository's adapter extracts facts: named things the code has, such as
feature flags, settings or routes. `repo_brief_facts` searches them by words
from the name, its summary, its ticket or its tags, and returns each id, the
summary and how many places declare or use it; `repo_brief_fact` opens one id
with the file and line of every declaration, use and mention.

Use them before grepping for a name you suspect is a flag or a setting: the
adapter has already mapped where it is declared and checked. A fact says where
it is used, not whether it is on: state is usually outside the repository, and
that belongs in `gaps`.
