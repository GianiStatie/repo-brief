"""Dependency views: inference, collapse, and rendering."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from repobrief import graph  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.detect import detect  # noqa: E402


class AreaTest(unittest.TestCase):
    def test_first_directory_under_product_code(self):
        self.assertEqual(graph.area_of("godot/src/player/x.gd", "godot/src"), "player")

    def test_nested_directories_stay_in_one_area(self):
        self.assertEqual(
            graph.area_of("godot/src/player/states/jump.gd", "godot/src"), "player"
        )

    def test_a_file_at_the_product_root_has_no_area(self):
        self.assertEqual(graph.area_of("godot/src/main.gd", "godot/src"), "")

    def test_files_outside_product_code_have_no_area(self):
        self.assertEqual(graph.area_of("godot/tests/x.gd", "godot/src"), "")


class DocLinkTest(unittest.TestCase):
    PATHS = {"docs/technical/notes.md", "docs/index.md", "README.md"}

    def test_resolves_a_vault_relative_link(self):
        self.assertEqual(graph.resolve_doc("technical/notes", self.PATHS), "docs/technical/notes.md")

    def test_ignores_a_link_label(self):
        self.assertEqual(
            graph.resolve_doc("technical/notes|the notes", self.PATHS),
            "docs/technical/notes.md",
        )

    def test_resolves_a_root_level_page(self):
        self.assertEqual(graph.resolve_doc("index", self.PATHS), "docs/index.md")

    def test_unknown_link_is_empty(self):
        self.assertEqual(graph.resolve_doc("nowhere", self.PATHS), "")


class EdgeInferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_graph()
        cls.detected = detect(cls.root)
        cls.units = GenericAdapter().units(cls.root)
        cls.edges = graph.infer_edges(cls.units, None, cls.detected.autoloads)

    def pairs(self, kind: str) -> set:
        return {(edge.source, edge.target) for edge in self.edges if edge.kind == kind}

    def test_reads_autoloads_through_uid_sidecars(self):
        self.assertEqual(self.detected.autoloads, {"Bus": "godot/src/singletons/bus.gd"})

    def test_extends_edge(self):
        self.assertIn(
            ("godot/src/player/player.gd", "godot/src/player/base_player.gd"),
            self.pairs("extends"),
        )

    def test_loads_edge(self):
        self.assertIn(
            ("godot/src/player/player.gd", "godot/src/player/stats.gd"),
            self.pairs("loads"),
        )

    def test_uses_edge_on_a_type_annotation(self):
        self.assertIn(
            ("godot/src/player/jump.gd", "godot/src/player/player.gd"),
            self.pairs("uses"),
        )

    def test_uses_edge_to_an_autoload_that_declares_no_class_name(self):
        self.assertIn(
            ("godot/src/player/player.gd", "godot/src/singletons/bus.gd"),
            self.pairs("uses"),
        )

    def test_references_edge_from_a_scene_to_its_script(self):
        self.assertIn(
            ("godot/src/player/player.tscn", "godot/src/player/player.gd"),
            self.pairs("references"),
        )

    def test_doc_edge_from_a_wikilink(self):
        self.assertIn(
            ("docs/index.md", "docs/technical/notes.md"),
            self.pairs("doc"),
        )

    def test_an_edge_never_points_at_itself(self):
        self.assertTrue(all(edge.source != edge.target for edge in self.edges))


class GraphShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fixture.build_graph()
        cls.detected = detect(cls.root)
        cls.adapter = GenericAdapter()
        cls.units = cls.adapter.units(cls.root)
        cls.edges, cls.areas = graph.build(
            cls.root, cls.adapter, cls.units, cls.detected.source_root, cls.detected.autoloads
        )

    def test_package_graph_collapses_to_areas(self):
        found = graph.package_graph(self.units, self.edges, self.areas)
        self.assertEqual({node["id"] for node in found["nodes"]}, {"player", "singletons"})

    def test_package_graph_drops_edges_inside_one_area(self):
        found = graph.package_graph(self.units, self.edges, self.areas)
        self.assertEqual(found["edges"], [{"from": "player", "to": "singletons"}])

    def test_area_graph_keeps_only_files_in_that_area(self):
        found = graph.area_graph(self.units, self.edges, self.areas, "player")
        ids = {node["id"] for node in found["nodes"]}
        self.assertIn("godot/src/player/player.gd", ids)
        self.assertNotIn("godot/src/singletons/bus.gd", ids)

    def test_unknown_area_is_empty_not_an_error(self):
        found = graph.area_graph(self.units, self.edges, self.areas, "nope")
        self.assertEqual(found["nodes"], [])

    def test_hub_graph_links_an_area_to_the_autoload_it_uses(self):
        found = graph.hub_graph(self.units, self.edges, self.areas, self.detected.autoloads)
        self.assertIn(
            {"from": "area:player", "to": "autoload:Bus"},
            found["edges"],
        )

    def test_default_view_shows_source_files_only(self):
        found = graph.area_graph(self.units, self.edges, self.areas, "player")
        self.assertNotIn("godot/src/player/player.tscn", {node["id"] for node in found["nodes"]})

    def test_scene_edges_bring_scenes_back(self):
        found = graph.area_graph(
            self.units, self.edges, self.areas, "player", graph.ALL_KINDS
        )
        self.assertIn("godot/src/player/player.tscn", {node["id"] for node in found["nodes"]})

    def test_mermaid_is_deterministic(self):
        found = graph.package_graph(self.units, self.edges, self.areas)
        self.assertEqual(graph.mermaid(found), graph.mermaid(found))
        self.assertTrue(graph.mermaid(found).startswith("%% area dependency graph"))


if __name__ == "__main__":
    unittest.main()
