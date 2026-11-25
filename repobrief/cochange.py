"""What git history says about which files go together. An experiment.

Behind `REPO_BRIEF_COCHANGE=1`, the files that were committed together with
the hits join the reciprocal rank fusion as one more list. Nothing is stored;
the log is read when the flag asks for it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV_TOGETHER = "REPO_BRIEF_COCHANGE"
# How far back the log is read.
HISTORY_LIMIT = 300
# A commit that touches more files than this is a sweep: a rename, a formatter,
# a first import. It says nothing about which files belong together.
SWEEP_FILES = 20


def wanted() -> bool:
    return bool(os.environ.get(ENV_TOGETHER))


def commits(root: Path, rev: str = "HEAD", limit: int = HISTORY_LIMIT) -> list[list[str]]:
    """The files each commit changed, newest commit first. Empty outside git."""
    try:
        found = subprocess.run(
            [
                "git", "-C", str(root), "log", "--no-merges", "--name-only",
                "--format=%x00", "-n", str(limit), rev, "--",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if found.returncode != 0:
        return []
    history = []
    for block in found.stdout.split("\x00"):
        paths = [line for line in block.splitlines() if line]
        if paths:
            history.append(paths)
    return history


def changed_together(history: list[list[str]], seeds: dict[str, float]) -> list[str]:
    """Files ordered by how often they were committed with a seed.

    A seed passes its weight to a file by the share of the seed's own commits
    that also changed that file, so a file edited every time beats one that
    happened to share a single large commit.
    """
    touched: dict[str, int] = {}
    pairs: dict[tuple[str, str], int] = {}
    for paths in history:
        if len(paths) > SWEEP_FILES:
            continue
        for path in paths:
            if path not in seeds:
                continue
            touched[path] = touched.get(path, 0) + 1
            for other in paths:
                if other != path:
                    pairs[(path, other)] = pairs.get((path, other), 0) + 1
    scores: dict[str, float] = {}
    for (seed, other), count in pairs.items():
        scores[other] = scores.get(other, 0.0) + seeds[seed] * count / touched[seed]
    return sorted(scores, key=lambda path: (-scores[path], path))
