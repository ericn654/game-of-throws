"""Tests against Yahoo-shaped JSON (collections keyed "0","1"... + count,
objects split into fragment lists), mirroring the real API layout."""
import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sync"))

from yahoo import normalize, unwrap  # noqa: E402
from fetch import parse_team, parse_matchups, renew_to_key  # noqa: E402
from stats import build  # noqa: E402


def y_collection(items, key):
    d = {str(i): {key: item} for i, item in enumerate(items)}
    d["count"] = len(items)
    return d


def y_team(key, name, guid, nick, rank=None, seed=None, w=0, l=0, pf=0.0, pa=0.0, pts=None):
    frags = [{"team_key": key}, {"team_id": key.split(".")[-1]}, {"name": name}, [],
             {"url": "https://example"},
             {"team_logos": [{"team_logo": {"size": "large", "url": "x"}}]}, [],
             {"number_of_moves": "12"}, {"number_of_trades": 2},
             {"managers": [{"manager": {"manager_id": "1", "nickname": nick, "guid": guid,
                                        "is_commissioner": "1" if nick == "Eric" else "0"}}]}]
    team = [frags]
    if pts is not None:
        team.append({"team_points": {"coverage_type": "week", "week": "1", "total": str(pts)}})
        team.append({"team_projected_points": {"total": "100.00"}})
    else:
        team.append({"team_points": {"coverage_type": "season", "total": str(pf)}})
        team.append({"team_standings": {
            "rank": rank if rank is not None else "", "playoff_seed": str(seed) if seed else "",
            "outcome_totals": {"wins": w, "losses": l, "ties": 0, "percentage": ".5"},
            "points_for": str(pf), "points_against": pa}})
    return team


class NormalizeTests(unittest.TestCase):
    def test_standings_shape(self):
        raw = {"fantasy_content": {"xml:lang": "en-US", "league": [
            {"league_key": "449.l.1", "name": "Game of Throws", "season": "2024",
             "renew": "423_99", "is_finished": 1},
            {"standings": [{"teams": y_collection([
                y_team("449.l.1.t.1", "Americaboys", "G1", "Eric", 1, 1, 10, 4, 1800.5, 1600),
                y_team("449.l.1.t.2", "Dropouts", "G2", "Abdul", 2, 3, 8, 6, 1700, 1650),
            ], "team")}]}]}}
        fc = normalize(raw)["fantasy_content"]
        league = fc["league"]
        self.assertEqual(league["name"], "Game of Throws")
        teams = [parse_team(t) for t in unwrap(league["standings"]["teams"], "team")]
        self.assertEqual(len(teams), 2)
        self.assertEqual(teams[0]["name"], "Americaboys")
        self.assertEqual(teams[0]["managers"][0]["nickname"], "Eric")
        self.assertTrue(teams[0]["managers"][0]["is_commissioner"])
        self.assertEqual(teams[0]["wins"], 10)
        self.assertEqual(teams[0]["rank"], 1)
        self.assertAlmostEqual(teams[0]["points_for"], 1800.5)
        self.assertEqual(teams[1]["trades"], 2)
        self.assertEqual(renew_to_key(league["renew"]), "423.l.99")

    def test_co_managers_stay_a_list(self):
        team = y_team("1.l.1.t.1", "X", "G1", "Eric", 1)
        team[0][-1] = {"managers": [{"manager": {"guid": "A", "nickname": "a"}},
                                    {"manager": {"guid": "B", "nickname": "b"}}]}
        t = parse_team(normalize({"team": team})["team"])
        self.assertEqual([m["guid"] for m in t["managers"]], ["A", "B"])

    def test_scoreboard_shape(self):
        matchup = {"week": "15", "status": "postevent", "is_playoffs": "1",
                   "is_consolation": "0", "is_tied": 0, "winner_team_key": "449.l.1.t.1",
                   "stat_winners": [{"stat_winner": {"stat_id": "1"}}],
                   "0": {"teams": y_collection([
                       y_team("449.l.1.t.1", "A", "G1", "Eric", pts=117.9),
                       y_team("449.l.1.t.2", "B", "G2", "james", pts=98.62)], "team")}}
        raw = {"fantasy_content": {"league": [
            {"league_key": "449.l.1"},
            {"scoreboard": {"0": {"matchups": y_collection([matchup], "matchup")}, "week": "15"}}]}}
        league = normalize(raw)["fantasy_content"]["league"]
        ms = parse_matchups(league)
        self.assertEqual(len(ms), 1)
        m = ms[0]
        self.assertEqual(m["week"], 15)
        self.assertTrue(m["is_playoffs"])
        self.assertFalse(m["is_consolation"])
        self.assertEqual(m["teams"][0]["points"], 117.9)
        self.assertEqual(m["teams"][1]["team_key"], "449.l.1.t.2")

    def test_settings_shape(self):
        raw = {"fantasy_content": {"league": [
            {"league_key": "449.l.1", "season": "2024", "start_week": "1", "end_week": "17"},
            {"settings": [{"draft_type": "live", "num_playoff_teams": "6",
                           "playoff_start_week": "15",
                           "roster_positions": [{"roster_position": {"position": "QB"}}],
                           "stat_categories": {"stats": [{"stat": {"stat_id": 4}}]}}]}]}}
        league = normalize(raw)["fantasy_content"]["league"]
        self.assertEqual(league["settings"]["playoff_start_week"], "15")
        self.assertEqual(league["end_week"], "17")


def synthetic_league(seed=7):
    """Two finished seasons + one in progress, with a renamed/merged manager."""
    rnd = random.Random(seed)
    people = [("G%d" % i, n) for i, n in enumerate(
        ["Eric", "Abdul", "Mohit", "Mr.Crow", "Rich", "james", "Saint", "Lawrence"])]
    seasons = []
    for year, finished in ((2024, True), (2025, True), (2026, False)):
        gk = {2024: 449, 2025: 461, 2026: 473}[year]
        teams, keys = [], []
        for i, (guid, nick) in enumerate(people):
            if year == 2025 and nick == "Rich":
                guid, nick = "G_ALT", "Richard"     # same person, new Yahoo account
            k = f"{gk}.l.1.t.{i+1}"
            keys.append(k)
            teams.append({"team_key": k, "name": f"{nick} FC", "managers": [
                {"guid": guid, "nickname": nick, "is_commissioner": nick == "Eric"}],
                "rank": None, "playoff_seed": None, "wins": 0, "losses": 0, "ties": 0,
                "points_for": 0, "points_against": 0, "moves": 5, "trades": 1})
        matchups = []
        last_week = 17 if finished else 6
        for wk in range(1, min(last_week, 14) + 1):
            order = keys[:]
            rnd.shuffle(order)
            for a, b in zip(order[::2], order[1::2]):
                pa, pb = round(rnd.uniform(70, 150), 2), round(rnd.uniform(70, 150), 2)
                matchups.append({"week": wk, "status": "postevent", "is_playoffs": False,
                                 "is_consolation": False, "is_tied": False,
                                 "winner_team_key": a if pa > pb else b,
                                 "teams": [{"team_key": a, "points": pa}, {"team_key": b, "points": pb}]})
        if finished:
            # simple 4-team bracket weeks 15-16, champion = keys[0]
            f4 = keys[:4]
            matchups += [
                {"week": 15, "is_playoffs": True, "is_consolation": False, "status": "postevent",
                 "teams": [{"team_key": f4[0], "points": 130}, {"team_key": f4[3], "points": 100}]},
                {"week": 15, "is_playoffs": True, "is_consolation": False, "status": "postevent",
                 "teams": [{"team_key": f4[1], "points": 120}, {"team_key": f4[2], "points": 110}]},
                {"week": 16, "is_playoffs": True, "is_consolation": False, "status": "postevent",
                 "teams": [{"team_key": f4[0], "points": 117.9}, {"team_key": f4[1], "points": 98.6}]},
                {"week": 15, "is_playoffs": True, "is_consolation": True, "status": "postevent",
                 "teams": [{"team_key": keys[4], "points": 200}, {"team_key": keys[5], "points": 90}]},
            ]
            for r, t in enumerate(teams, 1):
                t["rank"] = r
                t["playoff_seed"] = r
        seasons.append({"season": year, "league_key": f"{gk}.l.1", "name": "Game of Throws",
                        "num_teams": 8, "start_week": 1, "end_week": 16, "current_week": 16 if finished else 7,
                        "is_finished": finished, "playoff_start_week": 15, "num_playoff_teams": 4,
                        "renew": None, "teams": teams, "matchups": matchups})
    return seasons


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "league.config.json").read_text())
        self.cfg["merge"] = {"G_ALT": "G4"}
        self.cfg["names"] = {"G4": "Rich"}
        self.data = build(synthetic_league(), self.cfg)
        self.by_name = {m["name"]: m for m in self.data["managers"]}

    def test_merge_and_names(self):
        self.assertIn("Rich", self.by_name)
        self.assertNotIn("Richard", self.by_name)
        self.assertEqual(self.by_name["Rich"]["seasons"], 3)

    def test_titles_and_finals(self):
        eric = self.by_name["Eric"]
        self.assertEqual(eric["titles"], 2)
        self.assertEqual(self.by_name["Abdul"]["runner_ups"], 2)
        self.assertEqual(self.by_name["Lawrence"]["lasts"], 2)
        s25 = next(s for s in self.data["seasons"] if s["season"] == 2025)
        self.assertEqual(s25["champion"]["name"], "Eric")
        self.assertEqual(s25["title_game"]["winner_points"], 117.9)
        self.assertEqual(s25["title_game"]["loser_name"], "Abdul")
        self.assertIsNone(next(s for s in self.data["seasons"] if s["season"] == 2026)["champion"])

    def test_records_are_consistent(self):
        total_w = sum(m["w"] for m in self.data["managers"])
        total_l = sum(m["l"] for m in self.data["managers"])
        self.assertEqual(total_w + sum(m["t"] for m in self.data["managers"]) // 2 * 0, total_w)
        self.assertEqual(total_w, total_l)  # every regular-season game has one W and one L
        # 4 games/week, 14 weeks x2 seasons + 6 weeks
        self.assertEqual(total_w, 4 * (14 + 14 + 6))
        # consolation 200-pointer must not be the record high week
        self.assertLess(self.data["records"]["high_week"][0]["points"], 200)

    def test_h2h_symmetry(self):
        h = self.data["h2h"]
        for k, v in h.items():
            a, b = k.split("|")
            o = h[f"{b}|{a}"]
            self.assertEqual(v["w"], o["l"])
            self.assertEqual(v["l"], o["w"])

    def test_power_rankings(self):
        p = self.data["power"]["2026"]["weeks"]
        self.assertEqual(sorted(p, key=int), [str(i) for i in range(1, 7)])
        wk6 = p["6"]
        self.assertEqual(len(wk6), 8)
        self.assertEqual([r["rank"] for r in wk6], list(range(1, 9)))
        self.assertTrue(all(0 <= r["power"] <= 100 for r in wk6))
        self.assertIn("move", wk6[0])

    def test_playoff_apps_only_finished(self):
        self.assertEqual(self.by_name["Eric"]["playoff_apps"], 2)
        self.assertEqual(self.by_name["Eric"]["finished_seasons"], 2)


if __name__ == "__main__":
    unittest.main()
