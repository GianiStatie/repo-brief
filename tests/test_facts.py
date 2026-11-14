"""Facts: the adapter hook, the cache, the search and the two tools."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief import cli, facts, render  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402

HAS_MCP = importlib.util.find_spec("mcp") is not None


def build() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-facts-"))
    (root / "app").mkdir()
    (root / "app" / "Stands.java").write_text("enum Stands {\n    GOLD_EXPERIENCE,\n}\n", encoding="utf-8")
    (root / "app" / "Mansion.java").write_text(
        "class Mansion { void check() { Stands.GOLD_EXPERIENCE.isAwakened(); } }\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def fact(
    name="GOLD_EXPERIENCE", summary="Gives life, JOJO-12", ticket="JOJO-12",
    where="Mansion.check", used_path="app/Mansion.java",
):
    return {
        "id": "flag:%s" % name,
        "kind": "flag",
        "name": name,
        "summary": summary,
        "tickets": [ticket] if ticket else [],
        "tags": ["stands"],
        "declared_at": {
            "path": "app/Stands.java", "line": 2, "text": "%s," % name,
            "where": "Stands", "how": "declared",
        },
        "used_at": [{
            "path": used_path, "line": 1, "text": "Stands.GOLD_EXPERIENCE.isAwakened();",
            "where": where, "how": "checks whether it is on for everyone",
        }],
        "mentioned_at": [{
            "path": "volumes/part5.sql", "line": 4, "text": "'GOLD_EXPERIENCE'",
            "where": "part5.sql", "how": "names the flag as text",
        }],
    }


class FlagAdapter(GenericAdapter):
    name = "flags"
    root_hint = ""

    def facts(self, root):
        return [
            fact(),
            fact("HAMON", "Hamon toggle", "JOJO-9", where="Hamon.check", used_path="app/Hamon.java"),
        ]

    def fact_notes(self, root):
        return {"flag": ["Whether a flag is on lives outside the repository."]}

    def fact_inputs(self, root):
        return ["app/Stands.java", "app/Mansion.java", "volumes/part5.sql"]


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.root = build()
        self.adapter = FlagAdapter()

    def test_an_adapter_without_facts_offers_none(self):
        self.assertFalse(facts.available(GenericAdapter()))
        self.assertEqual(facts.build(self.root, GenericAdapter())["facts"], [])

    def test_the_first_load_builds_and_the_second_reads_the_cache(self):
        first, loaded = facts.load(self.root, self.adapter)
        self.assertEqual((loaded, first["counts"]), ("build", {"flag": 2}))
        self.assertTrue(facts.path_for(self.root).is_file())
        _again, loaded = facts.load(self.root, self.adapter)
        self.assertEqual(loaded, "cache")

    def test_a_changed_input_rebuilds(self):
        facts.load(self.root, self.adapter)
        target = self.root / "app" / "Stands.java"
        target.write_text(target.read_text(encoding="utf-8") + "\n// note\n", encoding="utf-8")
        _data, loaded = facts.load(self.root, self.adapter)
        self.assertEqual(loaded, "build")

    def test_the_cache_is_kept_out_of_git(self):
        facts.load(self.root, self.adapter)
        ignore = (self.root / ".repo-brief" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("facts.json", ignore)
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.root,
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertNotIn("facts.json", status)


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.facts = [
            fact(),
            fact("HAMON", "Hamon toggle", "JOJO-9", where="Hamon.check", used_path="app/Hamon.java"),
        ]

    def test_a_word_in_the_name_wins(self):
        found, total = facts.search(self.facts, "hamon")
        self.assertEqual(([item["id"] for item in found], total), (["flag:HAMON"], 1))

    def test_a_word_only_in_the_code_that_uses_it_still_matches(self):
        found, _total = facts.search(self.facts, "mansion")
        self.assertEqual([item["id"] for item in found], ["flag:GOLD_EXPERIENCE"])

    def test_a_prefix_matches_and_stop_words_do_not(self):
        found, _total = facts.search(self.facts, "experi")
        self.assertEqual([item["id"] for item in found], ["flag:GOLD_EXPERIENCE"])
        self.assertEqual(facts.search(self.facts, "the")[1], 2)

    def test_find_and_render_one(self):
        found = facts.find(self.facts, "flag:GOLD_EXPERIENCE")
        text = facts.render_fact(found)
        self.assertIn("flag:GOLD_EXPERIENCE — Gives life, JOJO-12", text)
        self.assertIn("Tickets: JOJO-12", text)
        self.assertIn("app/Mansion.java:1  Mansion.check", text)
        self.assertIn("checks whether it is on for everyone", text)
        self.assertIn("Named as data (1):", text)

    def test_render_search_carries_the_notes_after_the_matches(self):
        data = {"facts": self.facts, "counts": {"flag": 2}, "built_at": "now",
                "notes": {"flag": ["state lives outside"]}}
        text = facts.render_search(data, "hamon", loaded="cache")
        self.assertIn("2 fact(s) (2 flag), built now (cache).", text)
        self.assertIn("state lives outside", text)
        self.assertIn("flag:HAMON", text)
        self.assertLess(text.index("flag:HAMON"), text.index("state lives outside"))

    def test_the_open_hint_names_the_callers_tool(self):
        data = {"facts": self.facts, "counts": {"flag": 2}, "built_at": "now"}
        self.assertIn("`repo_fact <id>`", facts.render_search(data, "hamon"))
        self.assertIn(
            "`repo_brief_fact <id>`",
            facts.render_search(data, "hamon", fact_tool="repo_brief_fact"),
        )

    def test_the_empty_message_names_both_adapter_locations(self):
        text = facts.render_search({"facts": []})
        self.assertIn("tools/brief_adapter.py", text)
        self.assertIn(".repo-brief/adapter.py", text)


class HintTest(unittest.TestCase):
    def setUp(self):
        self.root = build()
        self.adapter = FlagAdapter()

    def test_no_cache_means_no_hint(self):
        self.assertIsNone(facts.hint(self.root, self.adapter, "hamon"))

    def test_a_matching_query_names_the_matches(self):
        facts.load(self.root, self.adapter)
        hint = facts.hint(self.root, self.adapter, "hamon")
        self.assertEqual(hint["matches"], ["flag:HAMON"])
        self.assertEqual((hint["total"], hint["match_total"]), (2, 1))
        self.assertEqual((hint["kind"], hint["tool"]), ("flag", "repo_facts"))

    def test_a_kind_word_points_at_the_whole_set(self):
        facts.load(self.root, self.adapter)
        hint = facts.hint(self.root, self.adapter, "which feature flags exist?")
        self.assertEqual((hint["kind"], hint["matches"]), ("flag", []))
        self.assertEqual(hint["total"], 2)

    def test_an_unrelated_query_gets_no_hint(self):
        facts.load(self.root, self.adapter)
        self.assertIsNone(facts.hint(self.root, self.adapter, "wombat"))

    def test_a_generic_adapter_gets_no_hint(self):
        self.assertIsNone(facts.hint(self.root, GenericAdapter(), "flags"))

    def test_the_rendered_line_names_the_callers_tool(self):
        context = {
            "query": "hamon",
            "tokens": ["hamon"],
            "decisions": [],
            "open_work": [],
            "facts": {
                "total": 2,
                "matches": ["flag:HAMON"],
                "match_total": 1,
                "kind": "flag",
                "tool": "repo_brief_facts",
            },
        }
        text = render.render_brief(context)
        self.assertIn("2 flag facts; 1 match, e.g. flag:HAMON", text)
        self.assertIn("`repo_brief_facts <words>`", text)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.root = build()
        state = self.root / ".repo-brief"
        state.mkdir()
        (state / "adapter.py").write_text(
            "from repobrief.adapters.generic import GenericAdapter\n"
            "\n"
            "class Adapter(GenericAdapter):\n"
            "    name = 'fixture'\n"
            "    def facts(self, root):\n"
            "        return [{'id': 'flag:GOLD_EXPERIENCE', 'kind': 'flag', 'name': 'GOLD_EXPERIENCE',\n"
            "                 'summary': 'Gives life', 'tickets': ['JOJO-12'],\n"
            "                 'declared_at': {'path': 'app/Stands.java', 'line': 2}}]\n",
            encoding="utf-8",
        )

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--root", str(self.root), *argv])
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    def test_facts_lists_then_opens_one(self):
        listed = self.run_cli("facts")
        self.assertIn("flag:GOLD_EXPERIENCE", listed)
        self.assertIn("Gives life", listed)
        opened = self.run_cli("facts", "--id", "flag:GOLD_EXPERIENCE")
        self.assertIn("Declared:", opened)
        self.assertIn("app/Stands.java:2", opened)

    def test_json_is_the_whole_set(self):
        data = json.loads(self.run_cli("facts", "--json"))
        self.assertEqual(data["counts"], {"flag": 1})
        self.assertEqual(data["facts"][0]["id"], "flag:GOLD_EXPERIENCE")

    def test_update_refreshes_the_cache_it_finds(self):
        self.run_cli("facts")
        output = self.run_cli("update")
        self.assertIn("facts: 1 (cache)", output)

    def test_editing_the_adapter_rebuilds_the_cache(self):
        first = json.loads(self.run_cli("facts", "--json"))
        self.assertTrue(first["adapter"])
        path = self.root / ".repo-brief" / "adapter.py"
        path.write_text(
            path.read_text(encoding="utf-8").replace("Gives life", "Takes life"),
            encoding="utf-8",
        )
        second = json.loads(self.run_cli("facts", "--json"))
        self.assertNotEqual(second["adapter"], first["adapter"])
        self.assertEqual(second["facts"][0]["summary"], "Takes life")

    def test_a_repo_without_the_hook_says_so(self):
        (self.root / ".repo-brief" / "adapter.py").unlink()
        text = self.run_cli("facts")
        self.assertIn("no `facts()` hook", text)
        self.assertIn(".repo-brief/adapter.py", text)

    def test_context_points_at_the_facts_command(self):
        self.run_cli("facts")
        text = self.run_cli("context", "GOLD_EXPERIENCE")
        self.assertIn("Facts:", text)
        self.assertIn("flag:GOLD_EXPERIENCE", text)
        self.assertIn("`repo-brief facts <words>`", text)


@unittest.skipUnless(HAS_MCP, "needs repo-brief[mcp]")
class ServerTest(unittest.TestCase):
    def setUp(self):
        self.root = build()
        self.adapter = FlagAdapter()

    def test_the_server_offers_the_two_facts_tools(self):
        from repobrief.serve import server

        full = {tool.name for tool in asyncio.run(server.build_server(self.root, self.adapter).list_tools())}
        self.assertTrue({"repo_facts", "repo_fact"} <= full)

    def test_a_search_and_an_id_answer(self):
        from repobrief.serve import server

        built = server.build_server(self.root, self.adapter)
        found = asyncio.run(built.call_tool("repo_facts", {"query": "hamon"}))
        self.assertIn("flag:HAMON", found.content[0].text)
        one = asyncio.run(built.call_tool("repo_fact", {"id": "flag:GOLD_EXPERIENCE"}))
        self.assertIn("Mansion.check", one.content[0].text)

    def test_the_facts_tool_hints_at_the_opener(self):
        from repobrief.serve import server

        full = server.build_server(self.root, self.adapter)
        text = asyncio.run(full.call_tool("repo_facts", {"query": "hamon"}))
        self.assertIn("`repo_fact <id>`", text.content[0].text)

    def test_context_points_at_the_facts_tool(self):
        from repobrief.serve import server

        facts.load(self.root, self.adapter)
        full = server.build_server(self.root, self.adapter)
        text = asyncio.run(full.call_tool("repo_context", {"query": "hamon"}))
        self.assertIn("`repo_facts <words>`", text.content[0].text)


if __name__ == "__main__":
    unittest.main()
