"""Fixture repositories, built in a temp directory.

Programmatic rather than committed: a checked-in tree of empty files is harder
to read than the code that creates it, and it can't drift from a test.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

FILES = {
    "project.godot": "[application]\nconfig/name=\"fixture\"\n",
    "godot/src/abilities/base_ability.gd": (
        "class_name BaseAbility\n"
        "extends Resource\n"
        "signal cast_started\n"
        "\n"
        "## Resolve the cooldown for this caster.\n"
        "func resolved_cooldown() -> float:\n"
        "\treturn 1.0\n"
    ),
    "godot/src/abilities/emeraldbarrier.gd": (
        "class_name EmeraldBarrier\n"
        "extends BaseAbility\n"
        "\n"
        "## Freezes the target for a short window.\n"
        "func trigger() -> void:\n"
        "\tpass\n"
    ),
    "godot/tests/abilities/test_emeraldbarrier.gd": (
        "extends GdUnitTestSuite\n"
        "\n"
        "func test_trigger_freezes_the_target() -> void:\n"
        "\tvar barrier := EmeraldBarrier.new()\n"
    ),
    "docs/decisions/D-001.md": (
        "---\n"
        "id: D-001\n"
        "area: combat\n"
        "---\n"
        "\n"
        "# D-001 — Barriers freeze instead of slowing\n"
        "\n"
        "**Rule.** Barriers apply a freeze, never a slow.\n"
    ),
    "docs/decisions/D-002.md": (
        "---\n"
        "id: D-002\n"
        "area: ui\n"
        "---\n"
        "\n"
        "# D-002 — The icon is not the mechanic\n"
        "\n"
        "**Rule.** A missing texture never disables an ability.\n"
    ),
    "docs/design/bugs.md": (
        "# Bugs\n"
        "\n"
        "## Bugs\n"
        "\n"
        "- [ ] **Barrier triggers twice on the same target** — `combat` · keywords: barrier, double\n"
        "  The area signal fires once per overlapping body.\n"
        "\n"
        "- [x] **Fixed thing** — `ui`\n"
        "\n"
        "## Improvements\n"
        "\n"
        "- [ ] **Add a cooldown icon** — `ui` · keywords: cooldown, icon\n"
    ),
    "docs/index.md": "# Index\n\nRouting for the fixture.\n",
    "docs/status.md": "# Status\n\n**Updated:** 2026-01-01\n",
    "docs/changelog.md": "# Changelog\n",
}


def build() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-fixture-"))
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# A second shape: one dated decision log and a backlog written as tables. Some
# repos use this; the first fixture uses a decision directory.
DATED_FILES = {
    "project.godot": "[application]\nconfig/name=\"dated\"\n",
    "godot/src/player/rush.gd": (
        "class_name Rush\n"
        "extends Node\n"
        "\n"
        "## Rush hold timer.\n"
        "func hold() -> void:\n"
        "\tpass\n"
    ),
    "godot/tests/player/test_rush.gd": (
        "extends GdUnitTestSuite\n"
        "\n"
        "const RUSH := preload(\"res://src/player/rush.gd\")\n"
        "\n"
        "func test_hold_keeps_the_player_up() -> void:\n"
        "\tvar rush := Rush.new()\n"
    ),
    "docs/project/decisions.md": (
        "# Decision Log\n"
        "\n"
        "## 2026-10-01 — Rush scales with hold time\n"
        "\n"
        "**Status:** Implemented.\n"
        "\n"
        "The longer the button is held, the longer the rush.\n"
        "\n"
        "## 2026-10-02 — Barriers freeze instead of slowing\n"
        "\n"
        "**Status:** Open.\n"
        "\n"
        "A barrier applies a freeze, never a slow.\n"
    ),
    "docs/project/active-backlog.md": (
        "# Backlog\n"
        "\n"
        "| # | Task | Notes | Priority |\n"
        "|---|---|---|---|\n"
        "| A1 | **Rush hold tuning** | Needs a rematch. | High |\n"
        "| A2 | **Barrier freeze** | Done 2026-10-03. | Low |\n"
        "| ~~A3~~ | ~~Old thing~~ | Done. | — |\n"
    ),
    "docs/status.md": "# Status\n\n**Updated:** 2026-10-02\n",
    "docs/index.md": "# Index\n\nRouting.\n",
    "docs/changelog.md": "# Changelog\n",
}


def build_dated() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-dated-"))
    for rel, text in DATED_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# A dependency slice: inheritance, a preload, an autoload symbol, a scene's
# script reference, and a doc wikilink. One edge of every kind the kernel infers.
GRAPH_FILES = {
    "godot/project.godot": (
        "[application]\n"
        'config/name="graph"\n'
        "\n"
        "[autoload]\n"
        "\n"
        'Bus="*uid://busuid"\n'
    ),
    "godot/src/singletons/bus.gd": "class_name Bus\nextends Node\n\nfunc ping() -> void:\n\tpass\n",
    "godot/src/singletons/bus.gd.uid": "uid://busuid\n",
    "godot/src/player/base_player.gd": "class_name BasePlayer\nextends Node\n",
    "godot/src/player/stats.gd": "class_name PlayerStats\nextends RefCounted\n",
    "godot/src/player/player.gd": (
        "class_name Player\n"
        "extends BasePlayer\n"
        "\n"
        'const STATS := preload("res://src/player/stats.gd")\n'
        "\n"
        "func _ready() -> void:\n"
        "\tBus.ping()\n"
    ),
    "godot/src/player/jump.gd": (
        "class_name Jump\n"
        "extends RefCounted\n"
        "\n"
        "var player: Player\n"
    ),
    "godot/src/player/player.tscn": (
        "[gd_scene format=3]\n"
        "\n"
        '[ext_resource type="Script" path="res://src/player/player.gd" id="1"]\n'
        "\n"
        '[node name="Player" type="Node"]\n'
        'script = ExtResource("1")\n'
    ),
    "docs/index.md": "# Index\n\nSee [[technical/notes]].\n",
    "docs/technical/notes.md": "# Notes\n",
}


def build_graph() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-graph-"))
    for rel, text in GRAPH_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# A docs tree with one of each archive candidate: an orphan, a page linked
# only from the archive, a duplicate title, and a root-level scratch file.
ARCHIVE_FILES = {
    "project.godot": "[application]\nconfig/name=\"archive\"\n",
    "godot/src/game.gd": "class_name Game\nextends Node\n",
    "docs/index.md": "# Index\n\nStart at [[keep|Keep]].\n",
    "docs/keep.md": "# Keep\n\nA live page.\n",
    "docs/orphan.md": "# Orphan\n\nNothing points here.\n",
    "docs/only-old.md": "# Only old\n\nLinked from the archive.\n",
    "docs/dup-a.md": "# Core Loop\n\nOne.\n",
    "docs/dup-b.md": "# Core Loop\n\nTwo.\n",
    "docs/archive/old.md": "# Old\n\nSuperseded by [[only-old]].\n",
    "docs/status.md": "# Status\n\n**Updated:** 2026-01-01\n",
    "docs/changelog.md": "# Changelog\n",
    "PLAN.md": "# Plan\n\nScratch.\n",
}


def build_archive() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-archive-"))
    for rel, text in ARCHIVE_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# A two-module Maven build. The same class name in two packages, a wildcard,
# a static and a nested import, an inline qualified name, a class mentioned
# without an import, a stub package outside the base, and a generated file:
# every case the Java resolver has to get right.
JAVA_MAIN = "stardust/src/main/java/com/example/jojo/"
JAVA_FILES = {
    "pom.xml": "<project/>\n",
    JAVA_MAIN + "stands/StarPlatinum.java": (
        "package com.example.jojo.stands;\n"
        "\n"
        "import com.example.jojo.arrow.Arrow;\n"
        "import com.example.jojo.hamon.*;\n"
        "import com.example.jojo.joestar.BaseStand;\n"
        "import com.example.jojo.phantom.Outer.Inner;\n"
        "import static com.example.jojo.joestar.Stamina.round;\n"
        "import java.util.List;\n"
        "\n"
        "/**\n"
        " * Punches faster than the eye can follow.\n"
        " *\n"
        " * @author someone\n"
        " */\n"
        "@Stand\n"
        "@TimeStop(seconds = 5,\n"
        "        resumes = true)\n"
        "public class StarPlatinum extends BaseStand<StandUser> implements Menacing {\n"
        "\n"
        "    public static final int MAX_PUNCHES = 50;\n"
        "\n"
        "    // public void commented() { }\n"
        "    private static final String NOT_A_METHOD = \"public void fake() {\";\n"
        "\n"
        "    private final Arrow arrow;\n"
        "    private StoneMask mask;\n"
        "    private Requiem requiem;\n"
        "    private Inner inner;\n"
        "\n"
        "    public StarPlatinum(Arrow arrow) {\n"
        "        super(arrow);\n"
        "        this.arrow = arrow;\n"
        "    }\n"
        "\n"
        "    /** Unleashes a rush on the target. */\n"
        "    @Override\n"
        "    @BattleCry(value = \"/ora\",\n"
        "            style = Cry.RUSH)\n"
        "    public Map<String, List<Integer>> oraRush(\n"
        "            @Aim(value = \"target\") String target,\n"
        "            int punches) throws IllegalStateException {\n"
        "        if (target == null) {\n"
        "            return null;\n"
        "        }\n"
        "        validate(target);\n"
        "        com.example.jojo.arrow.Diary.record(target);\n"
        "        return round(punches);\n"
        "    }\n"
        "\n"
        "    abstract void validate(String target);\n"
        "}\n"
    ),
    JAVA_MAIN + "stands/StandUser.java": "package com.example.jojo.stands;\n\npublic class StandUser {\n}\n",
    JAVA_MAIN + "stands/Menacing.java": (
        "package com.example.jojo.stands;\n"
        "\n"
        "public interface Menacing extends Named, Comparable<Menacing> {\n"
        "    int menace(String sound);\n"
        "\n"
        "    default String label() {\n"
        "        return \"menacing\";\n"
        "    }\n"
        "}\n"
    ),
    JAVA_MAIN + "joestar/BaseStand.java": (
        "package com.example.jojo.joestar;\n"
        "\n"
        "public abstract class BaseStand<T> {\n"
        "    protected BaseStand(Object user) {\n"
        "    }\n"
        "}\n"
    ),
    JAVA_MAIN + "joestar/Stamina.java": (
        "package com.example.jojo.joestar;\n"
        "\n"
        "public final class Stamina {\n"
        "    public static int round(int value) {\n"
        "        return value;\n"
        "    }\n"
        "}\n"
    ),
    JAVA_MAIN + "joestar/Awakened.java": (
        "package com.example.jojo.joestar;\n"
        "\n"
        "public @interface Awakened {\n"
        "    String value() default \"\";\n"
        "}\n"
    ),
    JAVA_MAIN + "arrow/Arrow.java": "package com.example.jojo.arrow;\n\npublic class Arrow {\n}\n",
    JAVA_MAIN + "arrow/Diary.java": (
        "package com.example.jojo.arrow;\n"
        "\n"
        "public class Diary {\n"
        "    public static void record(String target) {\n"
        "    }\n"
        "}\n"
    ),
    JAVA_MAIN + "arrow/Requiem.java": "package com.example.jojo.arrow;\n\npublic class Requiem {\n}\n",
    JAVA_MAIN + "arrow/Prophecy.java": (
        "package com.example.jojo.arrow;\n"
        "\n"
        "/** Mentions StandUser without importing it, so it cannot mean the one in stands. */\n"
        "public class Prophecy {\n"
        "    private StandUser user;\n"
        "}\n"
    ),
    JAVA_MAIN + "hamon/StoneMask.java": "package com.example.jojo.hamon;\n\npublic class StoneMask {\n}\n",
    JAVA_MAIN + "hamon/Requiem.java": "package com.example.jojo.hamon;\n\npublic class Requiem {\n}\n",
    JAVA_MAIN + "phantom/Outer.java": (
        "package com.example.jojo.phantom;\n"
        "\n"
        "public class Outer {\n"
        "    public static class Inner {\n"
        "    }\n"
        "}\n"
    ),
    "vento/src/main/java/com/example/jojo/hamon/Ripple.java": (
        "package com.example.jojo.hamon;\n\npublic class Ripple {\n}\n"
    ),
    "stardust/src/main/java/org/speedwagon/stubs/Foundation.java": (
        "package org.speedwagon.stubs;\n\npublic class Foundation {\n}\n"
    ),
    "stardust/src/main/java/org/speedwagon/stubs/Stub.java": (
        "/**\n"
        " * Stub.java\n"
        " *\n"
        " * This file was auto-generated from stands.idl\n"
        " * by the Speedwagon Foundation stub generator.\n"
        " */\n"
        "package org.speedwagon.stubs;\n"
        "\n"
        "public class Stub {\n"
        "}\n"
    ),
    "stardust/src/test/java/com/example/jojo/stands/StarPlatinumTest.java": (
        "package com.example.jojo.stands;\n"
        "\n"
        "public class StarPlatinumTest {\n"
        "    public void rushesTheTarget() {\n"
        "        new StarPlatinum(null).oraRush(\"x\", 1);\n"
        "    }\n"
        "}\n"
    ),
}


def build_java() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-java-"))
    for rel, text in JAVA_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root
