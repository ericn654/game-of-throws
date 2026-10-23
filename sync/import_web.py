"""Convert compact season dumps scraped from Yahoo's league pages
(see tools/yahoo_web_scrape.js) into data/seasons/<year>.json.

Usage: python3 sync/import_web.py dump.json [more.json ...]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "seasons"


def expand(c):
    game_league = c["k"]
    key = lambda t: f"{game_league}.t.{t}"
    teams = []
    for (tid, name, mgrs, rank, made, w, l, t, pf, pa, moves, trades) in c["t"]:
        managers = []
        for m in (mgrs or "").split("^"):
            if not m:
                continue
            guid, _, nick = m.partition("~")
            if guid.startswith("hidden-"):
                # Yahoo hides some profiles; key them by nickname, or by
                # season + team when even the nickname is hidden.
                guid = f"nick:{nick}" if nick and nick != "--hidden--" else f"hidden:{c['y']}:{tid}"
                nick = nick if nick != "--hidden--" else ""
            managers.append({"guid": guid, "nickname": nick or guid, "is_commissioner": False})
        teams.append({"team_key": key(tid), "name": name, "managers": managers, "rank": rank,
                      "playoff_seed": None, "made_playoffs": bool(made), "wins": w, "losses": l,
                      "ties": t, "points_for": pf, "points_against": pa, "moves": moves, "trades": trades})
    matchups = []
    for (week, flags, a, pa_, b, pb_) in c["m"]:
        m = {"week": week, "status": "midevent" if flags & 4 else "postevent",
             "is_playoffs": bool(flags & 1), "is_consolation": bool(flags & 2),
             "is_tied": pa_ == pb_,
             "winner_team_key": key(a) if pa_ > pb_ else key(b) if pb_ > pa_ else None,
             "teams": [{"team_key": key(a), "points": pa_}, {"team_key": key(b), "points": pb_}]}
        if flags & 4:
            m["in_progress"] = True
        matchups.append(m)
    return {"season": c["y"], "league_key": game_league, "name": c["n"], "num_teams": len(teams),
            "start_week": 1, "end_week": c["e"], "current_week": c["c"], "is_finished": bool(c["f"]),
            "playoff_start_week": c["ps"], "num_playoff_teams": c["np"], "renew": None,
            "source": "yahoo-web", "teams": teams, "matchups": matchups}


def main(paths):
    OUT.mkdir(parents=True, exist_ok=True)
    for p in paths:
        data = json.loads(Path(p).read_text())
        for c in (data if isinstance(data, list) else data.values() if "y" not in data else [data]):
            s = expand(c)
            (OUT / f"{s['season']}.json").write_text(json.dumps(s, indent=1))
            print(f"{s['season']}: {len(s['teams'])} teams, {len(s['matchups'])} matchups"
                  f"{'' if s['is_finished'] else ' (in progress)'}")


if __name__ == "__main__":
    main(sys.argv[1:])
