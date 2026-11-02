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
        self.assertEqual(kernel.tokenize("how does the trap work"), ["trap"])

    def test_keeps_short_words_that_mean_something(self):
        self.assertIn("ui", kernel.tokenize("ui hover"))

    def test_splits_the_plural(self):
        self.assertEqual(kernel.tokenize("traps"), ["traps", "trap"])

    def test_empty_query_falls_back_to_the_raw_string(self):
        self.assertEqual(kernel.tokenize("the of"), [])


class PhraseTest(unittest.TestCase):
    def test_keeps_the_whole_query_as_a_phrase(self):
        self.assertIn("chicken trap", kernel.phrases("chicken trap"))

    def test_keeps_underscored_identifiers(self):
        self.assertIn("snap_time", kernel.phrases("snap_time reset"))


class ThresholdTest(unittest.TestCase):
    def test_two_word_query_matching_one_word_scores_zero(self):
        item = Item(path="a.md", line=1, text="Unrelated entry about traps", kind="open")
        self.assertEqual(kernel.score_item(item, ["trap", "immunity"]), 0)

    def test_two_word_query_matching_both_scores(self):
        item = Item(path="a.md", line=1, text="Trap immunity", kind="open")
        self.assertGreater(kernel.score_item(item, ["trap", "immunity"]), 0)

    def test_phrase_match_survives_the_threshold(self):
        item = Item(path="a.md", line=1, text="immune to traps", kind="open")
        self.assertGreater(kernel.score_item(item, ["trap", "immunity"], ["immune to traps"]), 0)


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
        named = Unit(path="a/thing.gd", kind="source", fields={"class_name": "Trap"}, text="")
        buried = Unit(path="b/other.gd", kind="source", fields={}, text="trap " * 50)
        self.assertGreater(
            kernel.score_unit(named, ["trap"]),
            kernel.score_unit(buried, ["trap"]),
        )


if __name__ == "__main__":
    unittest.main()
