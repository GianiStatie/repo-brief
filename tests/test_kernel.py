"""Kernel behaviour: the rules that must hold across every repo."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief import kernel  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402
from repobrief.contract import Item, Unit  # noqa: E402


class TokenizeTest(unittest.TestCase):
    def test_drops_stopwords_and_short_words(self):
        self.assertEqual(kernel.tokenize("how does the barrier work"), ["barrier"])

    def test_keeps_short_words_that_mean_something(self):
        self.assertIn("ui", kernel.tokenize("ui rush"))

    def test_splits_the_plural(self):
        self.assertEqual(kernel.tokenize("barriers"), ["barriers", "barrier"])

    def test_an_ies_plural_keeps_both_singulars(self):
        # `abilities` is `ability`; `cookies` would be `cookie`.
        self.assertEqual(kernel.tokenize("abilities"), ["abilities", "ability", "abilitie"])

    def test_a_word_that_is_rarely_a_plural_is_left_alone(self):
        self.assertEqual(kernel.tokenize("status boss"), ["status", "boss"])

    def test_splits_letters_from_digits(self):
        self.assertEqual(kernel.tokenize("part4diamond"), ["part4diamond", "part", "diamond"])

    def test_splits_camel_case_and_keeps_the_whole_name(self):
        self.assertEqual(
            kernel.tokenize("StarPlatinum"), ["starplatinum", "star", "platinum"]
        )

    def test_splits_a_camel_case_method_before_lowercasing(self):
        self.assertEqual(
            kernel.tokenize("getStandCry"), ["getstandcry", "get", "stand", "cry"]
        )

    def test_leaves_the_pieces_of_a_word_the_corpus_knows_whole(self):
        def known(whole: str) -> bool:
            return whole in {"goldexperience"}

        self.assertNotIn("experience", kernel.tokenize("GoldExperience", known=known))
        self.assertIn("star", kernel.tokenize("StarPlatinum", known=known))

    def test_splits_a_leading_acronym_from_the_word_after_it(self):
        self.assertEqual(
            kernel.tokenize("SPWFoundation"), ["spwfoundation", "spw", "foundation"]
        )

    def test_empty_query_falls_back_to_the_raw_string(self):
        self.assertEqual(kernel.tokenize("the of"), [])


class PhraseTest(unittest.TestCase):
    def test_keeps_the_whole_query_as_a_phrase(self):
        self.assertIn("hierophant barrier", kernel.phrases("hierophant barrier"))

    def test_keeps_underscored_identifiers(self):
        self.assertIn("stop_time", kernel.phrases("stop_time reset"))


class ThresholdTest(unittest.TestCase):
    def test_two_word_query_matching_one_word_scores_zero(self):
        item = Item(path="a.md", line=1, text="Unrelated entry about barriers", kind="open")
        self.assertEqual(kernel.score_item(item, ["barrier", "piercing"]), 0)

    def test_two_word_query_matching_both_scores(self):
        item = Item(path="a.md", line=1, text="Barrier piercing", kind="open")
        self.assertGreater(kernel.score_item(item, ["barrier", "piercing"]), 0)

    def test_phrase_match_survives_the_threshold(self):
        item = Item(path="a.md", line=1, text="pierces barriers", kind="open")
        self.assertGreater(kernel.score_item(item, ["barrier", "piercing"], ["pierces barriers"]), 0)


class BestLineTest(unittest.TestCase):
    TEXT = "\n".join(
        [
            "## A comment mentioning cooldown",
            "class_name Thing",
            "func reset_cooldown():",
            "\tpass",
        ]
    )

    def test_prefers_a_code_line(self):
        line, snippet = kernel.best_line(self.TEXT, ["cooldown"], skip_comments=True)
        self.assertEqual(line, 3)
        self.assertIn("reset_cooldown", snippet)

    def test_falls_back_to_a_comment_rather_than_nothing(self):
        line, snippet = kernel.best_line(self.TEXT, ["comment"], skip_comments=True)
        self.assertEqual(line, 1)
        self.assertIn("comment", snippet)

    def test_returns_the_first_line_when_nothing_matches(self):
        line, snippet = kernel.best_line("hello\nworld", ["absent"], skip_comments=True)
        self.assertEqual((line, snippet), (1, "hello"))


class ScoreUnitTest(unittest.TestCase):
    def test_class_name_outranks_a_body_mention(self):
        named = Unit(path="a/thing.gd", kind="source", fields={"class_name": "Barrier"}, text="")
        buried = Unit(path="b/other.gd", kind="source", fields={}, text="barrier " * 50)
        self.assertGreater(
            kernel.score_unit(named, ["barrier"]),
            kernel.score_unit(buried, ["barrier"]),
        )

    def test_a_word_inside_a_camel_case_file_name_counts(self):
        unit = Unit(path="src/StandServiceImpl.java", kind="source", fields={}, text="")
        self.assertGreaterEqual(kernel.score_unit(unit, ["service"]), 5)

    def test_a_rare_word_weighs_more_than_a_common_one(self):
        units = [Unit(path=f"src/services{i}/Service.java") for i in range(20)]
        units.append(Unit(path="src/Requiem.java"))
        weights = kernel.query_weights(units, ["service", "requiem"])
        self.assertGreater(weights["requiem"], weights["service"])
        self.assertGreater(weights["requiem"], 1.0)
        self.assertLess(weights["service"], 2.0)

    def test_one_common_word_does_not_answer_a_multi_word_query(self):
        common = Unit(path="src/HamonType.java")
        weights = {"bizarre": 1.1, "requiem": 4.0}
        self.assertEqual(kernel.score_unit(common, ["bizarre", "requiem"], weights), 0)

    def test_one_rare_word_survives_the_multi_word_threshold(self):
        rare = Unit(path="src/RequiemArrowServiceImpl.java")
        weights = {"bizarre": 1.1, "requiem": 4.0}
        self.assertGreater(kernel.score_unit(rare, ["bizarre", "requiem"], weights), 0)

    def test_phrase_order_beats_the_same_words_reversed(self):
        exact = Unit(path="src/KillerQueen.java")
        reversed_ = Unit(path="src/QueenKiller.java")
        self.assertGreater(
            kernel.score_unit(exact, ["killer", "queen"]),
            kernel.score_unit(reversed_, ["killer", "queen"]),
        )

    def test_a_plural_query_word_still_forms_a_phrase(self):
        # `stands` also yields `stand`; the extra token must not break the run.
        tokens = kernel.tokenize("stands rush")
        self.assertEqual(kernel._longest_phrase(tokens, ["StandsRush"]), 2)
        self.assertEqual(kernel._longest_phrase(tokens, ["StandRush"]), 2)
        self.assertGreater(
            kernel.score_unit(Unit(path="src/StandsRush.java"), tokens),
            kernel.score_unit(Unit(path="src/RushStands.java"), tokens),
        )

    def test_symbol_names_count_as_evidence(self):
        with_symbols = Unit(
            path="src/StandStatsDto.java",
            symbols=[{"name": "getStarFinger"}, {"name": "getNextStarFinger"}],
        )
        without = Unit(path="src/StandStatsDto.java")
        self.assertGreater(
            kernel.score_unit(with_symbols, ["star"]),
            kernel.score_unit(without, ["star"]),
        )


class AlsoMatchedTest(unittest.TestCase):
    def test_the_ranking_continues_after_the_plan(self):
        root = Path(tempfile.mkdtemp(prefix="repo-brief-also-"))
        (root / "project.godot").write_text(
            '[application]\nconfig/name="also"\n', encoding="utf-8"
        )
        src = root / "godot" / "src"
        src.mkdir(parents=True)
        for index in range(12):
            (src / ("stand_%02d.gd" % index)).write_text(
                "class_name Stand%02d\nextends Node\n\n"
                "## A stand.\nfunc rush() -> void:\n\tpass\n" % index,
                encoding="utf-8",
            )
        context = kernel.build_context(root, GenericAdapter(), "stand", limit=4)
        self.assertEqual(len(context["also_matched"]), kernel.ALSO_MATCHED_LIMIT)
        plan_paths = {item["path"] for item in context["read_plan"]}
        self.assertFalse(plan_paths & set(context["also_matched"]))
        self.assertTrue(all(":" not in path for path in context["also_matched"]))


class NameWordsTest(unittest.TestCase):
    def test_splits_camel_case(self):
        self.assertTrue(
            {"jojo", "api", "v2", "stand", "controller"}
            <= kernel.name_words("JojoApiV2StandController")
        )

    def test_an_acronym_is_one_word(self):
        self.assertTrue({"xml", "parser", "xmlparser"} <= kernel.name_words("XMLParser"))

    def test_snake_case_is_unchanged(self):
        self.assertEqual(kernel.name_words("base_ability"), {"base", "ability"})

    def test_splits_letters_from_digits_and_keeps_the_run(self):
        self.assertTrue({"part4", "part", "4", "diamond"} <= kernel.name_words("Part4Diamond"))


class BodyRankingTest(unittest.TestCase):
    @staticmethod
    def rank(texts: dict, query: str) -> dict:
        units = [Unit(path=path, kind="source", text=text) for path, text in texts.items()]
        tokens = kernel.tokenize(query)
        return kernel.body_scores(kernel.TextBodies(units, tokens), tokens, set(texts))

    def test_a_rare_word_outweighs_a_repeated_common_one(self):
        texts = {f"src/F{i}.java": "service" for i in range(8)}
        texts.update({"src/A.java": "service requiem", "src/B.java": "service service"})
        scores = self.rank(texts, "service requiem")
        self.assertGreater(scores["src/A.java"], scores["src/B.java"])

    def test_the_forms_of_one_query_word_add_up(self):
        scores = self.rank({"src/A.java": "stand stands", "src/B.java": "stand other"}, "stands")
        self.assertGreater(scores["src/A.java"], scores["src/B.java"])

    def test_a_singular_query_finds_the_plural_in_the_code(self):
        scores = self.rank({"src/A.java": "List<Stands> all", "src/B.java": "Hamon"}, "stand")
        self.assertIn("src/A.java", scores)
        self.assertNotIn("src/B.java", scores)

    def test_a_word_and_its_plural_rank_a_file_alike(self):
        texts = {"src/A.java": "stands stand", "src/B.java": "Hamon"}
        self.assertEqual(self.rank(texts, "stands"), self.rank(texts, "stand"))

    def test_a_word_is_found_beside_digits(self):
        scores = self.rank({"src/A.java": "stand2user", "src/B.java": "Hamon"}, "user")
        self.assertEqual(list(scores), ["src/A.java"])

    def test_a_long_file_is_discounted(self):
        texts = {"src/A.java": "menacing", "src/B.java": "menacing " + "filler " * 50}
        scores = self.rank(texts, "menacing")
        self.assertGreater(scores["src/A.java"], scores["src/B.java"])

    def test_a_word_is_whole_not_a_prefix(self):
        scores = self.rank({"src/A.java": "getStarFinger()", "src/B.java": "stardust"}, "star")
        self.assertIn("src/A.java", scores)
        self.assertNotIn("src/B.java", scores)

    def test_fusion_prefers_what_both_rankings_place_high(self):
        # b is 2nd and 1st; a is 1st and 3rd; d and c appear once each.
        fused = kernel.fuse([["a", "b", "c"], ["b", "d", "a"]])
        self.assertEqual(sorted(fused, key=lambda path: -fused[path]), ["b", "a", "d", "c"])


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repo-brief-search-"))
        (self.root / "project.godot").write_text('[application]\nconfig/name="search"\n', encoding="utf-8")
        src = self.root / "godot" / "src"
        src.mkdir(parents=True)
        for name, text in {
            "za_warudo.gd": (
                "class_name ZaWarudo\nextends Node\n\n## Stop time for everyone else.\n"
                "func stop_time() -> void:\n\tvar seconds := 5\n\tbus.emit(\"za_warudo\", seconds)\n"
            ),
            "star_platinum.gd": (
                "class_name StarPlatinum\nextends Node\n\nfunc ora_rush() -> void:\n\tpass\n\n"
                "func stop_time() -> void:\n\tpass\n"
            ),
            "hamon.gd": "class_name Hamon\nextends Node\n\nfunc overdrive() -> void:\n\tpass\n",
        }.items():
            (src / name).write_text(text, encoding="utf-8")

    def search(self, query, **options):
        return kernel.search(self.root, GenericAdapter(), query, **options)

    def test_the_files_that_use_the_words_come_first_with_their_lines(self):
        found = self.search("ZaWarudo stop_time")
        paths = [item["path"] for item in found["results"]]
        self.assertEqual(paths, ["godot/src/za_warudo.gd", "godot/src/star_platinum.gd"])
        lines = found["results"][0]["lines"]
        self.assertLessEqual(len(lines), kernel.SEARCH_LINES)
        self.assertEqual([line["line"] for line in lines], sorted(line["line"] for line in lines))
        self.assertIn("func stop_time() -> void:", [line["text"] for line in lines])

    def test_a_word_nothing_uses_finds_nothing(self):
        self.assertEqual(self.search("requiem")["results"], [])

    def test_the_limit_holds(self):
        self.assertEqual(len(self.search("extends node", limit=1)["results"]), 1)

    def test_the_index_ranks_the_same(self):
        from repobrief import index

        self.write_other_files()
        units, state, _stats = index.ensure(self.root, GenericAdapter(), build=True)
        try:
            for query in ("stop_time", "menacing", "speedwagon"):
                indexed = kernel.search(self.root, GenericAdapter(), query, units=units, usage_index=state)
                self.assertEqual(indexed["results"], self.search(query)["results"], query)
        finally:
            state.close()

    def write_other_files(self):
        """In the game's module: files no parser reads, a note, and two files search
        must leave out. Outside it, a note."""
        game = self.root / "godot"
        (game / "config").mkdir()
        (game / "config" / "stands.properties").write_text(
            "stand.menacing.label=Menacing\n", encoding="utf-8"
        )
        (game / "web").mkdir()
        (game / "web" / "menacing.ts").write_text(
            "export const menacing = () => 'gogogo';\n", encoding="utf-8"
        )
        (game / "notes").mkdir()
        (game / "notes" / "diary.md").write_text(
            "# Day 12\n\nSpeedwagon retreated with menacing style.\n", encoding="utf-8"
        )
        (game / "config" / "dump.bin").write_bytes(b"menacing\x00\x01\x02 menacing")
        (game / "web" / "stands_pb.ts").write_text(
            "// Code generated by stando. DO NOT EDIT.\nexport const menacing = 1;\n",
            encoding="utf-8",
        )
        (self.root / "docs").mkdir()
        (self.root / "docs" / "menacing.md").write_text(
            "# Menacing\n\nA page outside the game.\n", encoding="utf-8"
        )

    def test_every_file_with_words_in_the_module_is_searched(self):
        self.write_other_files()
        paths = [item["path"] for item in self.search("menacing")["results"]]
        self.assertEqual(
            sorted(paths),
            ["godot/config/stands.properties", "godot/notes/diary.md", "godot/web/menacing.ts"],
        )
        found = self.search("speedwagon")["results"]
        self.assertEqual([item["path"] for item in found], ["godot/notes/diary.md"])
        self.assertEqual(found[0]["lines"][0]["line"], 3)

    def test_a_repo_of_notes_is_searched_whole(self):
        notes = Path(tempfile.mkdtemp(prefix="repo-brief-notes-"))
        (notes / "2026").mkdir()
        (notes / "2026" / "day-12.md").write_text(
            "# Day 12\n\nThe arrow chose Koichi.\n", encoding="utf-8"
        )
        found = kernel.search(notes, GenericAdapter(), "arrow")
        self.assertEqual([item["path"] for item in found["results"]], ["2026/day-12.md"])


class CodeModulesTest(unittest.TestCase):
    def test_a_maven_module_is_the_folder_above_src(self):
        self.assertEqual(
            kernel.code_modules(["passione/web/src/main/java", "passione/bus/src/main/java"]),
            ("passione/web/", "passione/bus/"),
        )

    def test_a_code_folder_without_src_sits_at_the_top_of_its_module(self):
        self.assertEqual(kernel.code_modules(["godot/src"]), ("godot/",))
        self.assertEqual(kernel.code_modules(["games/stands"]), ("games/",))

    def test_code_at_the_top_or_none_means_the_whole_repo(self):
        self.assertEqual(kernel.code_modules(["stands"]), ())
        self.assertEqual(kernel.code_modules(["src"]), ())
        self.assertEqual(kernel.code_modules([]), ())


if __name__ == "__main__":
    unittest.main()
