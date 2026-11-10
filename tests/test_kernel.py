"""Kernel behaviour: the rules that must hold across every repo."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief import kernel  # noqa: E402
from repobrief.contract import Item, Unit  # noqa: E402


class TokenizeTest(unittest.TestCase):
    def test_drops_stopwords_and_short_words(self):
        self.assertEqual(kernel.tokenize("how does the barrier work"), ["barrier"])

    def test_keeps_short_words_that_mean_something(self):
        self.assertIn("ui", kernel.tokenize("ui rush"))

    def test_splits_the_plural(self):
        self.assertEqual(kernel.tokenize("barriers"), ["barriers", "barrier"])

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


if __name__ == "__main__":
    unittest.main()
