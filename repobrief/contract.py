"""The public surface: what an adapter declares, and what the kernel consumes.

An adapter never scores anything. It answers structural questions about a
repository -- what its units are, where its decisions live, what counts as open
work -- and the kernel does every bit of matching, ranking and rendering.

That split is the whole point. If an adapter scores its own text, you are back
to four independent implementations that drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Unit:
    """One addressable thing in the repo: a file, a note, an experiment."""

    path: str
    kind: str = "source"
    fields: dict = field(default_factory=dict)
    symbols: list = field(default_factory=list)
    text: str = ""


@dataclass
class Item:
    """A decision record or an open loop."""

    path: str
    line: int
    text: str
    body: str = ""
    kind: str = "open"
    date: str = ""
    group: str = ""
    label: str = ""
    score: int = 0


@dataclass
class Source:
    """Supporting prose or configuration that is not a unit."""

    path: str
    kind: str = "docs"
    text: str = ""


@dataclass(frozen=True)
class Edge:
    """A dependency between two units.

    Kinds are open, but these are the ones the kernel can infer without help:
    `extends` (a class inherits another), `loads` (a file references another by
    path), `uses` (a file names a class declared elsewhere), `references`
    (a scene or resource points at a script), `doc` (prose links to a doc).
    Frozen so edges can be deduplicated in a set.
    """

    source: str
    target: str
    kind: str = "uses"


class Adapter:
    """Base class. Override only what differs from the generic adapter."""

    name = "generic"

    def units(self, root: Path) -> list[Unit]:
        """Every file worth ranking. Text may be empty if the adapter can't read it."""
        return []

    def decisions(self, root: Path) -> list[Item]:
        """Normative records. Empty is fine; many repos have none."""
        return []

    def open_work(self, root: Path) -> list[Item]:
        """Live loops. This is what the brief surfaces under Open work."""
        return []

    def sources(self, root: Path) -> list[Source]:
        """Docs, tools and build files that explain or configure the units."""
        return []

    def canonical(self, root: Path) -> list[str]:
        """Front-door docs that must exist. Checked by `preflight`."""
        return []

    def dated(self, root: Path) -> list[str]:
        """Docs that must carry a date, because they claim a current state.

        A router or a backlog does not need one; a status page does, or it
        silently becomes a lie the moment work moves on.
        """
        return []

    def excluded(self, root: Path) -> set[str]:
        """Paths that exist but must never be offered as an answer.

        Retired tooling, superseded archives, fixtures. Excluding is a
        judgement: the file is real, but citing it would point a reader at
        something that is no longer the current way to do the thing.
        """
        return set()

    def edges(self, root: Path) -> list[Edge]:
        """Dependencies between units. Empty means "infer them generically".

        Return structure, never scores. The kernel renders these as graphs;
        an adapter overrides this only when its repo has an edge kind the
        generic inference cannot see.
        """
        return []

    def areas(self, root: Path) -> dict[str, str]:
        """Path -> area name. Empty means "first directory under source_root".

        An area is the unit of collapse for a whole-repo dependency view. Both
        Godot repos mean the same thing by it, so the default reproduces them
        without configuration.
        """
        return {}

    def archived(self, root: Path) -> list[str]:
        """Directories whose contents are history, not current state.

        Archive is never an answer unless the question is about the past, so
        the kernel hides these by default and says when it has not.
        """
        return []

    def usage_roots(self, root: Path) -> list[str]:
        """Directories where a matching line counts as a real usage site.

        Empty means anywhere. Defaults in the generic adapter to the directory
        that holds the product code, so tooling does not win its own query.
        """
        return []

    def describe(self, unit: Unit) -> str:
        return unit.fields.get("description", "") or ""
