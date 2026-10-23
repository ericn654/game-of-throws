"""Pull league history from Yahoo into data/seasons/<year>.json.

Finished seasons are cached and never refetched unless --full is passed.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from yahoo import Yahoo, as_list, unwrap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "seasons"


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def renew_to_key(renew):
    """'423_123456' -> '423.l.123456'"""
    if not renew or "_" not in str(renew):
        return None
    game, league = str(renew).split("_", 1)
    return f"{game}.l.{league}"


def parse_team(t):
    managers = []
    for m in unwrap(t.get("managers"), "manager"):
        managers.append({
            "guid": m.get("guid") or f"id{m.get('manager_id')}",
            "nickname": m.get("nickname") or "Unknown",
            "is_commissioner": str(m.get("is_commissioner", "0")) == "1",
        })
    st = t.get("team_standings") or {}
    ot = st.get("outcome_totals") or {}
    return {
        "team_key": t.get("team_key"),
        "name": t.get("name"),
        "managers": managers,
        "rank": _int(st.get("rank"), None) if st.get("rank") not in (None, "") else None,
        "playoff_seed": _int(st.get("playoff_seed"), None) if st.get("playoff_seed") not in (None, "") else None,
        "wins": _int(ot.get("wins")),
        "losses": _int(ot.get("losses")),
        "ties": _int(ot.get("ties")),
        "points_for": _float(st.get("points_for")),
        "points_against": _float(st.get("points_against")),
        "moves": _int(t.get("number_of_moves")),
        "trades": _int(t.get("number_of_trades")),
    }


def parse_matchups(scoreboard_league):
    sb = scoreboard_league.get("scoreboard") or {}
    out = []
    for m in unwrap(sb.get("matchups"), "matchup"):
        teams = unwrap(m.get("teams"), "team")
        if len(teams) != 2:
            continue
        sides = []
        for t in teams:
            pts = (t.get("team_points") or {}).get("total")
            sides.append({"team_key": t.get("team_key"), "points": _float(pts)})
        out.append({
            "week": _int(m.get("week")),
            "status": m.get("status"),
            "is_playoffs": str(m.get("is_playoffs", "0")) == "1",
            "is_consolation": str(m.get("is_consolation", "0")) == "1",
            "is_tied": str(m.get("is_tied", "0")) == "1",
            "winner_team_key": m.get("winner_team_key"),
            "teams": sides,
        })
    return out


def fetch_season(y, league_key, log=print):
    meta = y.get(f"league/{league_key}/settings").get("league", {})
    settings = meta.get("settings") or {}
    season = _int(meta.get("season"))
    start_week = _int(meta.get("start_week"), 1)
    end_week = _int(meta.get("end_week"), 17)
    current_week = _int(meta.get("current_week"), end_week)
    finished = str(meta.get("is_finished", "0")) == "1"
    log(f"  {season}: {meta.get('name')} ({league_key}), weeks {start_week}-{end_week}"
        f"{'' if finished else f', current week {current_week}'}")

    standings = y.get(f"league/{league_key}/standings").get("league", {})
    teams = [parse_team(t) for t in unwrap(
        (standings.get("standings") or {}).get("teams"), "team")]

    last_week = end_week if finished else current_week
    matchups = []
    for w in range(start_week, last_week + 1):
        sb = y.get(f"league/{league_key}/scoreboard;week={w}").get("league", {})
        week_matchups = parse_matchups(sb)
        if not finished and w == current_week:
            for m in week_matchups:
                m["in_progress"] = m.get("status") != "postevent"
        matchups.extend(week_matchups)

    return {
        "season": season,
        "league_key": league_key,
        "name": meta.get("name"),
        "num_teams": _int(meta.get("num_teams"), len(teams)),
        "start_week": start_week,
        "end_week": end_week,
        "current_week": current_week,
        "is_finished": finished,
        "playoff_start_week": _int(settings.get("playoff_start_week"), end_week - 2),
        "num_playoff_teams": _int(settings.get("num_playoff_teams"), 6),
        "renew": renew_to_key(meta.get("renew")),
        "teams": teams,
        "matchups": matchups,
    }


def current_league_key(y, league_id):
    """Find this season's league key among the signed-in user's NFL leagues."""
    try:
        fc = y.get("users;use_login=1/games;game_codes=nfl/leagues")
        found = []
        for u in unwrap(fc.get("users"), "user"):
            for g in unwrap(u.get("games"), "game"):
                for lg in unwrap(g.get("leagues"), "league"):
                    if str(lg.get("league_id")) == str(league_id):
                        found.append((_int(g.get("season") or lg.get("season")), lg["league_key"]))
        if found:
            return max(found)[1]
        print(f"League {league_id} not found among your leagues; trying game/nfl")
    except RuntimeError as e:
        print(f"League lookup failed ({e}); trying game/nfl")
    game = y.get("game/nfl").get("game", {})
    return f"{game['game_key']}.l.{league_id}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="refetch finished seasons")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "league.config.json").read_text())
    league_id = os.environ.get("YAHOO_LEAGUE_ID") or str(cfg.get("yahoo_league_id", ""))
    if not league_id:
        raise SystemExit("Set yahoo_league_id in league.config.json")

    token = os.environ["YAHOO_REFRESH_TOKEN"]
    y = Yahoo(os.environ["YAHOO_CLIENT_ID"], os.environ["YAHOO_CLIENT_SECRET"], token)

    DATA.mkdir(parents=True, exist_ok=True)
    cached = {}
    for f in DATA.glob("*.json"):
        s = json.loads(f.read_text())
        cached[s["league_key"]] = s

    key = cfg.get("current_league_key") or current_league_key(y, league_id)
    print(f"Syncing league chain starting at {key}")
    seen = set()
    while key and key not in seen:
        seen.add(key)
        old = cached.get(key)
        if old and old.get("is_finished") and not args.full:
            print(f"  {old['season']}: cached")
            key = old.get("renew")
            continue
        season = fetch_season(y, key)
        (DATA / f"{season['season']}.json").write_text(json.dumps(season, indent=1))
        key = season.get("renew")

    print(f"Done. {y.calls} API calls.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out and y.refresh_token != token:
        with open(out, "a") as fh:
            fh.write("token_rotated=true\n")
        Path(os.environ.get("NEW_TOKEN_FILE", "/tmp/new_refresh_token")).write_text(
            y.refresh_token)


if __name__ == "__main__":
    main()
