"""Java: declarations read from blanked source, names resolved per package."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from repobrief import graph, index, kernel, render  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.detect import detect  # noqa: E402
from repobrief.parsers import parse_java  # noqa: E402

MAIN = fixture.JAVA_MAIN
STAR = MAIN + "stands/StarPlatinum.java"


def symbol_lines(unit, kind: str) -> list[tuple[str, int]]:
    return [(symbol["name"], symbol["line"]) for symbol in unit.symbols if symbol["kind"] == kind]


def line_of(unit, text: str) -> int:
    """1-based number of the first line whose stripped text starts with `text`."""
    lines = unit.text.splitlines()
    return next(number for number, line in enumerate(lines, 1) if line.strip().startswith(text))


class JavaParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_java()
        cls.star = parse_java(cls.root, cls.root / STAR)

    def parse(self, rel: str):
        return parse_java(self.root, self.root / rel)

    def test_package_and_imports(self):
        self.assertEqual(self.star.fields["package"], "com.example.jojo.stands")
        self.assertEqual(
            self.star.fields["imports"],
            [
                "com.example.jojo.arrow.Arrow",
                "com.example.jojo.hamon.*",
                "com.example.jojo.joestar.BaseStand",
                "com.example.jojo.joestar.Stamina",
                "com.example.jojo.phantom.Outer.Inner",
                "java.util.List",
            ],
        )

    def test_class_header_without_generics(self):
        fields = self.star.fields
        self.assertEqual(
            (fields["class_name"], fields["extends"], fields["implements"]),
            ("StarPlatinum", "BaseStand", ["Menacing"]),
        )

    def test_description_is_the_class_javadoc_prose(self):
        self.assertEqual(self.star.fields["description"], "Punches faster than the eye can follow.")

    def test_methods_constructors_and_constants(self):
        self.assertEqual(
            [name for name, _line in symbol_lines(self.star, "function")],
            ["StarPlatinum", "oraRush", "validate"],
        )
        self.assertEqual(
            [name for name, _line in symbol_lines(self.star, "const")],
            ["MAX_PUNCHES", "NOT_A_METHOD"],
        )

    def test_comments_and_strings_are_not_structure(self):
        names = {symbol["name"] for symbol in self.star.symbols}
        self.assertNotIn("commented", names)
        self.assertNotIn("fake", names)

    def test_a_declaration_starts_at_its_first_annotation(self):
        self.assertIn(("StarPlatinum", line_of(self.star, "@Stand")), symbol_lines(self.star, "class"))
        self.assertIn(("oraRush", line_of(self.star, "@Override")), symbol_lines(self.star, "function"))

    def test_interface_methods_and_parents(self):
        unit = self.parse(MAIN + "stands/Menacing.java")
        self.assertEqual([name for name, _ in symbol_lines(unit, "function")], ["menace", "label"])
        self.assertEqual((unit.fields["extends"], unit.fields["implements"]), ("Named", ["Comparable"]))

    def test_annotation_type_elements_are_methods(self):
        unit = self.parse(MAIN + "joestar/Awakened.java")
        self.assertEqual([name for name, _ in symbol_lines(unit, "function")], ["value"])

    def test_nested_types_are_symbols_but_the_file_names_the_class(self):
        unit = self.parse(MAIN + "phantom/Outer.java")
        self.assertEqual([name for name, _ in symbol_lines(unit, "class")], ["Outer", "Inner"])
        self.assertEqual(unit.fields["class_name"], "Outer")

    def test_windows_line_endings_keep_line_numbers(self):
        path = self.root / MAIN / "stands/CrlfService.java"
        source = fixture.JAVA_FILES[STAR].replace("StarPlatinum", "CrlfService")
        path.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
        unit = parse_java(self.root, path)
        line = dict(symbol_lines(unit, "function"))["oraRush"]
        self.assertEqual(unit.text.splitlines()[line - 1].strip(), "@Override")

    def test_test_sources_are_tests(self):
        self.assertEqual(self.parse("stardust/src/test/java/com/example/jojo/stands/StarPlatinumTest.java").kind, "test")
        self.assertEqual(self.star.kind, "source")


class JavaEdgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_java()
        cls.units = GenericAdapter().units(cls.root)
        cls.edges = graph.infer_edges(cls.units)

    def targets(self, source: str, kind: str) -> set:
        return {edge.target for edge in self.edges if edge.source == source and edge.kind == kind}

    def test_superclass_and_interface_are_extends_edges(self):
        self.assertEqual(
            self.targets(STAR, "extends"),
            {MAIN + "joestar/BaseStand.java", MAIN + "stands/Menacing.java"},
        )

    def test_same_package_needs_no_import(self):
        self.assertIn(MAIN + "stands/StandUser.java", self.targets(STAR, "uses"))

    def test_explicit_wildcard_static_and_inline_references(self):
        uses = self.targets(STAR, "uses")
        for target in ("arrow/Arrow.java", "hamon/StoneMask.java", "joestar/Stamina.java", "arrow/Diary.java"):
            self.assertIn(MAIN + target, uses)

    def test_a_shared_name_resolves_through_the_import(self):
        uses = self.targets(STAR, "uses")
        self.assertIn(MAIN + "hamon/Requiem.java", uses)
        self.assertNotIn(MAIN + "arrow/Requiem.java", uses)

    def test_a_nested_import_points_at_the_outer_file(self):
        self.assertIn(MAIN + "phantom/Outer.java", self.targets(STAR, "uses"))

    def test_an_unimported_class_from_another_package_is_not_an_edge(self):
        self.assertNotIn(MAIN + "stands/StandUser.java", self.targets(MAIN + "arrow/Prophecy.java", "uses"))

    def test_an_edge_never_points_at_itself(self):
        self.assertTrue(all(edge.source != edge.target for edge in self.edges))


class JavaAreaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_java()
        cls.adapter = GenericAdapter()
        cls.units = cls.adapter.units(cls.root)
        detected = cls.adapter.detected(cls.root)
        cls.edges, cls.areas = graph.build(cls.root, cls.adapter, cls.units, detected.source_root)

    def test_base_package_skips_the_stub_corner(self):
        self.assertEqual(graph.java_base_package(self.units), "com.example.jojo")

    def test_areas_are_packages_under_the_base(self):
        self.assertEqual(self.areas[STAR], "stands")
        self.assertEqual(self.areas["vento/src/main/java/com/example/jojo/hamon/Ripple.java"], "hamon")
        self.assertEqual(self.areas["stardust/src/main/java/org/speedwagon/stubs/Foundation.java"], "")

    def test_package_graph_collapses_to_package_areas(self):
        found = graph.package_graph(self.units, self.edges, self.areas)
        self.assertEqual(
            {node["id"] for node in found["nodes"]},
            {"arrow", "hamon", "joestar", "phantom", "stands"},
        )
        self.assertIn({"from": "stands", "to": "joestar"}, found["edges"])

    def test_area_graph_labels_start_inside_the_package(self):
        found = graph.area_graph(self.units, self.edges, self.areas, "stands")
        self.assertIn({"id": STAR, "label": "StarPlatinum.java"}, found["nodes"])


class JavaDetectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_java()
        cls.found = detect(cls.root)

    def test_every_module_is_a_source_root(self):
        self.assertEqual(self.found.source_roots, ["stardust/src/main/java", "vento/src/main/java"])

    def test_usage_hits_come_from_every_module(self):
        self.assertEqual(GenericAdapter().usage_roots(self.root), self.found.source_roots)

    def test_maven_test_sources_are_a_test_directory(self):
        self.assertIn("stardust/src/test/java", self.found.test_dirs)

    def test_a_generated_stub_is_not_parsed(self):
        unit = next(
            unit for unit in GenericAdapter().units(self.root) if unit.path.endswith("stubs/Stub.java")
        )
        self.assertEqual(unit.kind, "file")


class JavaReadPlanTest(unittest.TestCase):
    def test_a_large_class_is_sliced_to_the_annotated_method_and_its_javadoc(self):
        root = fixture.build_java()
        filler = "".join(
            "    /** Rounds line %d. */\n    public int line%d(int value) {\n        return value + %d;\n    }\n\n"
            % (number, number, number)
            for number in range(400)
        )
        source = fixture.JAVA_FILES[STAR].replace(
            "    /** Unleashes a rush", filler + "    /** Unleashes a rush", 1
        )
        (root / STAR).write_text(source, encoding="utf-8")
        self.assertGreater(len(source.encode("utf-8")), kernel.SMALL_CODE_BYTES)

        context = kernel.build_context(root, GenericAdapter(), "ora rush")
        item = next(item for item in context["read_plan"] if item["path"] == STAR)
        lines = source.splitlines()
        sliced = "\n".join(lines[item["start"] - 1:item["end"]])
        self.assertEqual(item["label"], "oraRush")
        self.assertIn("Unleashes a rush", lines[item["start"] - 1])
        self.assertIn("@BattleCry", sliced)
        self.assertIn("oraRush(", sliced)
        self.assertLess(item["end"] - item["start"], 40)


class JavaBodyRankingTest(unittest.TestCase):
    """A class named nothing like the question is still found by what its code says."""

    HOURGLASS = MAIN + "arrow/Hourglass.java"

    def setUp(self):
        self.root = fixture.build_java()
        (self.root / self.HOURGLASS).write_text(
            "package com.example.jojo.arrow;\n\n"
            "public class Hourglass {\n"
            "    public void close() {\n"
            "        // Time flows again once the frozen moment has passed.\n"
            '        log("frozen moment passed");\n'
            "    }\n"
            "}\n",
            encoding="utf-8",
        )

    def test_the_body_finds_it_the_same_with_the_index_off_or_on(self):
        adapter = GenericAdapter()
        off = kernel.build_context(self.root, adapter, "frozen moment")
        units, state, _ = index.ensure(self.root, adapter, build=True)
        try:
            on = kernel.build_context(
                self.root, adapter, "frozen moment", units=units, usage_index=state
            )
        finally:
            state.close()
        first = off["units"][0]
        self.assertEqual(first["path"], self.HOURGLASS)
        self.assertEqual(first["name_score"], 0)
        self.assertGreater(first["body_score"], 0)
        self.assertEqual([u["path"] for u in on["units"]], [u["path"] for u in off["units"]])


class JavaMapTest(unittest.TestCase):
    def test_the_map_names_the_parent_and_the_interfaces(self):
        root = fixture.build_java()
        adapter = GenericAdapter()
        markdown = render.render_map(
            adapter.detected(root), adapter.units(root), [], [], adapter.name
        )
        self.assertIn("`StarPlatinum` → `BaseStand`, `Menacing`", markdown)


if __name__ == "__main__":
    unittest.main()
