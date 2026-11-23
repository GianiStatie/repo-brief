"""The facts the generic adapter reads on its own: routes, settings, tables, jobs, client calls."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repobrief import extract, facts  # noqa: E402
from repobrief.adapters.generic import GenericAdapter  # noqa: E402

JAVA = "src/main/java/com/example/jojo/"

CONTROLLER = '''package com.example.jojo;

@RestController
@RequestMapping("/api/v1/stands")
@UserOnly(parts = {Part.STARDUST})
public class StandController extends BaseController {
    private static final Logger logger = Logger.of(StandController.class);
    @Autowired
    private StandService standService;
    private final Speedwagon speedwagon;

    // @GetMapping("/commented-out")
    @RequestMapping(value = "/{name}", method = RequestMethod.GET)
    @ApiOperation(value = "Returns one stand {by name}")
    @FoundationAccess(sections = {Section.ARROWS})
    public Stand find(@PathVariable("name") String name) {
        logger.debug("find {}", name);
        speedwagon.note(name);
        return standService.summon(name);
    }

    @PostMapping({"/awaken", "/requiem"})
    public void awaken(@RequestBody Arrow arrow) {
        standService.awaken(arrow);
        standService.awaken(arrow);
    }

    @RequestMapping(Paths.RANGE)
    public int range() { return 2; }

    @GetMapping("/users")
    @FoundationAccess(sections = {Section.ARROWS}, restrictJoestars = true)
    public Map<String, List<Stand>> users(@RequestParam("part") int part, Model model) { return null; }
}
'''

SERVICE = '''package com.example.jojo;
public interface StandService { Stand summon(String name); void awaken(Arrow arrow); }
'''

SERVICE_IMPL = '''package com.example.jojo;
public class StandServiceImpl implements StandService {
    @Value("${stand.range.metres:2}")
    private int range;
    @Value("${stand.arrow.secret}")
    private String secret;

    @Scheduled(cron = "${stand.sweep.cron}")
    public void sweep() { String name = environment.getProperty("stand.user.name"); }
}
'''

ENTITY = '''package com.example.jojo;
@Entity
@Table(name = "JOJO_STAND", schema = "MORIOH")
public class Stand { }
'''

REPOSITORY = '''package com.example.jojo;
public class StandRepository {
    Stand load() { return query("select * from jojo_stand where user_id = ?"); }
}
'''

YAML = '''stand:
  range:
    metres: 10   # close range
  arrow:
    secret: "requiem"
  notes: |
    range: not a key
  users:
    - name: jotaro
'''

CLIENT = '''import { environment } from './environment';

export class StandsService {
  private readonly baseUrl = `${environment.API_URL}/v1/stands`;

  constructor(private http: HttpClient) {}

  findStandByName(name: string): Observable<Stand> {
    return this.http.get<Stand>(`${this.baseUrl}/${name}?full=true`).pipe(
      map((stand) => {
        return stand;
      })
    );
  }

  awakenTheStand(arrow: Arrow): Observable<void> {
    const url = environment.mockData
      ? `${environment.MOCK_URL}/stands/awaken.json`
      : `${environment.API_URL}/v1/stands/awaken`;

    return this.http
      .post<void>(url, arrow);
  }

  listStandUsers(part: number): Observable<User[]> {
    let url;
    if (environment.mockData) {
      url = 'assets/users.json';
    } else {
      url = `${environment.API_URL}/v1/users`;
    }
    return this.http.get<User[]>(url);
  }
}
'''

COMPONENT = '''export class StandCardComponent {
  constructor(private stands: StandsService) {}

  ngOnInit(): void {
    this.stands.findStandByName('Star Platinum').subscribe();
  }
}
'''


def build() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repo-brief-extract-"))
    files = {
        JAVA + "StandController.java": CONTROLLER,
        JAVA + "StandService.java": SERVICE,
        JAVA + "StandServiceImpl.java": SERVICE_IMPL,
        JAVA + "Stand.java": ENTITY,
        JAVA + "Speedwagon.java": "package com.example.jojo;\npublic class Speedwagon { void note(String s) {} }\n",
        JAVA + "StandRepository.java": REPOSITORY,
        "src/main/resources/application.yml": YAML,
        "src/main/resources/messages.properties": "stand.title=Your stand\n",
        "deploy/morioh.properties": "# production\nstand.range.metres=50\nstand.arrow.secret=hunter2\nfoundation.clientKey=speedwagon\n",
        "db/V1__stands.sql": "create table JOJO_STAND (id int);\n",
        "pom.xml": "<project><version>${stand.build.version}</version></project>\n",
        "web/src/app/stands.service.ts": CLIENT,
        "web/src/app/stand-card.component.ts": COMPONENT,
        "web/src/app/stands.service.spec.ts": "it('asks', () => { http.get('/v1/never'); });\n",
    }
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


class ExtractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = build()
        cls.adapter = GenericAdapter()
        cls.facts = cls.adapter.facts(cls.root)
        cls.by_id = {fact["id"]: fact for fact in cls.facts}

    def test_a_route_joins_the_class_prefix_and_the_method_path(self):
        fact = self.by_id["route:GET /api/v1/stands/{name}"]
        self.assertEqual(fact["summary"], "StandController.find: Returns one stand {by name}")
        self.assertEqual(fact["declared_at"]["path"], JAVA + "StandController.java")
        self.assertEqual(fact["declared_at"]["line"], 13)

    def test_a_route_carries_the_other_annotations_of_the_method_and_the_class(self):
        tags = self.by_id["route:GET /api/v1/stands/{name}"]["tags"]
        self.assertEqual(
            tags, ["@FoundationAccess(sections = {Section.ARROWS})", "@UserOnly(parts = {Part.STARDUST})"]
        )

    def test_a_route_lists_what_the_handler_calls_on_its_fields(self):
        calls = self.by_id["route:GET /api/v1/stands/{name}"]["calls"]
        self.assertEqual([call["where"] for call in calls], ["Speedwagon.note", "StandService.summon"])
        self.assertEqual(
            calls[1]["how"],
            "in %sStandService.java, implemented by %sStandServiceImpl.java" % (JAVA, JAVA),
        )
        self.assertEqual(calls[1]["line"], 19)

    def test_each_path_of_a_mapping_is_a_route_and_a_repeated_call_is_listed_once(self):
        self.assertIn("route:POST /api/v1/stands/awaken", self.by_id)
        fact = self.by_id["route:POST /api/v1/stands/requiem"]
        self.assertEqual([call["where"] for call in fact["calls"]], ["StandService.awaken"])

    def test_a_constant_path_is_shown_as_written_and_a_comment_is_no_route(self):
        self.assertIn("route:ANY /api/v1/stands/{Paths.RANGE}", self.by_id)
        self.assertFalse([name for name in self.by_id if "commented-out" in name])

    def test_a_return_type_with_a_comma_is_still_a_handler(self):
        self.assertEqual(self.by_id["route:GET /api/v1/stands/users"]["declared_at"]["where"], "StandController.users")

    def test_words_of_an_annotation_find_only_the_routes_that_carry_all_of_them(self):
        found, total = facts.search(self.facts, "restrictJoestars arrows", kind="route")
        self.assertEqual(([fact["id"] for fact in found], total), (["route:GET /api/v1/stands/users"], 1))
        _found, total = facts.search(self.facts, "arrows", kind="route")
        self.assertEqual(total, 2)

    def test_a_compound_written_whole_finds_the_facts_that_carry_it_as_written(self):
        found, total = facts.search(self.facts, "restrictJoestars stand")
        self.assertEqual(([fact["id"] for fact in found], total), (["route:GET /api/v1/stands/users"], 1))
        found, _total = facts.search(self.facts, "stand.range.metres")
        self.assertEqual([fact["id"] for fact in found], ["setting:stand.range.metres"])

    def test_a_setting_has_its_value_in_each_file_and_each_place_that_reads_it(self):
        fact = self.by_id["setting:stand.range.metres"]
        self.assertEqual(
            fact["summary"], "`50` in morioh.properties; `10` in application.yml; read in 1 place(s)"
        )
        self.assertEqual(
            [(place["path"], place["line"]) for place in fact["set_at"]],
            [("deploy/morioh.properties", 2), ("src/main/resources/application.yml", 3)],
        )
        self.assertEqual(fact["used_at"][0]["how"], "read as a placeholder, default `2`")
        self.assertEqual(fact["used_at"][0]["line"], 3)

    def test_a_secret_is_hidden_and_a_bundle_of_texts_is_no_setting(self):
        fact = self.by_id["setting:stand.arrow.secret"]
        self.assertNotIn("requiem", str(fact))
        self.assertNotIn("hunter2", str(fact))
        self.assertIn("(hidden)", fact["summary"])
        self.assertNotIn("speedwagon", str(self.by_id["setting:foundation.clientKey"]))
        self.assertNotIn("setting:stand.title", self.by_id)
        self.assertNotIn("setting:stand.build.version", self.by_id)

    def test_a_key_the_code_reads_and_no_file_sets_is_still_a_setting(self):
        self.assertEqual(
            self.by_id["setting:stand.user.name"]["summary"], "not set in the repository; read in 1 place(s)"
        )
        self.assertEqual(self.by_id["setting:stand.user.name"]["used_at"][0]["how"], "read with getProperty")

    def test_yaml_lists_and_block_scalars_are_not_settings(self):
        self.assertNotIn("setting:stand.notes", self.by_id)
        self.assertNotIn("setting:stand.notes.range", self.by_id)
        self.assertNotIn("setting:stand.users.name", self.by_id)

    def test_a_table_has_who_names_its_entity_and_which_lines_name_it(self):
        fact = self.by_id["table:JOJO_STAND"]
        self.assertEqual(fact["tags"], ["schema MORIOH"])
        self.assertIn("mapped by the entity Stand", fact["summary"])
        self.assertIn(JAVA + "StandRepository.java", [place["path"] for place in fact["used_at"]])
        named = {(place["path"], place["how"]) for place in fact["mentioned_at"]}
        self.assertIn(("db/V1__stands.sql", "names the table"), named)
        self.assertIn((JAVA + "StandRepository.java", "names the table in a string"), named)

    def test_a_scheduled_method_is_a_job(self):
        self.assertEqual(self.by_id["job:StandServiceImpl.sweep"]["summary"], 'runs on cron = "${stand.sweep.cron}"')

    def test_a_client_call_drops_the_base_and_names_what_is_put_in(self):
        fact = self.by_id["call:GET /v1/stands/{name}"]
        self.assertEqual(fact["declared_at"]["where"], "StandsService.findStandByName")
        self.assertEqual(fact["declared_at"]["line"], 9)
        self.assertEqual(
            [place["path"] for place in fact["used_at"]], ["web/src/app/stand-card.component.ts"]
        )

    def test_a_client_call_through_a_variable_takes_the_real_branch_not_the_mock(self):
        self.assertEqual(
            self.by_id["call:POST /v1/stands/awaken"]["declared_at"]["where"], "StandsService.awakenTheStand"
        )
        self.assertEqual(self.by_id["call:GET /v1/users"]["declared_at"]["where"], "StandsService.listStandUsers")
        self.assertFalse([name for name in self.by_id if ".json" in name or "never" in name])

    def test_notes_cover_only_the_kinds_found(self):
        self.assertEqual(sorted(self.adapter.fact_notes(self.root)), ["call", "route", "setting", "table"])

    def test_the_facts_are_searched_and_rendered_like_an_adapters(self):
        data, loaded = facts.load(self.root, GenericAdapter())
        self.assertEqual(loaded, "build")
        found, _total = facts.search(data["facts"], "stands awaken", kind="route")
        self.assertEqual(found[0]["id"], "route:POST /api/v1/stands/awaken")
        text = facts.render_fact(self.by_id["setting:stand.range.metres"])
        self.assertIn("Set (2):", text)
        self.assertIn("Used (1):", text)
        self.assertIn("Calls (2):", facts.render_fact(self.by_id["route:GET /api/v1/stands/{name}"]))


class EmptyTest(unittest.TestCase):
    def test_a_repository_of_prose_has_no_facts(self):
        root = Path(tempfile.mkdtemp(prefix="repo-brief-extract-"))
        (root / "README.md").write_text("# Stands\n\n`GET /api/v1/stands`\n", encoding="utf-8")
        (root / "ci.yml").write_text("jobs:\n  build:\n    runs-on: morioh\n", encoding="utf-8")
        self.assertEqual(extract.facts(root, [Path("README.md"), Path("ci.yml")]), [])


if __name__ == "__main__":
    unittest.main()
