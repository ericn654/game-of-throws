"""Turn cached Yahoo seasons into the site's data.json."""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_LEGACY = {
    "title": 12,
    "runner_up": 5,
    "playoff_appearance": 1.5,
    "net_regular_season_win": 0.25,
    "last_place": -2,
}
DEFAULT_POWER = {"record": 0.30, "strength": 0.45, "form": 0.25, "form_weeks": 3}


# ---------------------------------------------------------------- identities

class People:
    """Maps Yahoo manager guids to one stable id and display name."""

    def __init__(self, cfg):
        self.merge = cfg.get("merge", {})            # guid or nickname -> guid or nickname
        self.names = cfg.get("names", {})            # guid or nickname -> display name
        self.latest_nick = {}

    def canon(self, team):
        mgrs = team.get("managers") or [{"guid": team.get("team_key"), "nickname": team.get("name")}]
        m = mgrs[0]
        key = m["guid"]
        nxt = self.merge.get(key) or self.merge.get(m["nickname"])
        seen = {key}
        while nxt and nxt not in seen:
            seen.add(nxt)
            key = nxt
            nxt = self.merge.get(key)
        return key, m["nickname"]

    def name(self, pid):
        return self.names.get(pid) or self.names.get(self.latest_nick.get(pid, "")) \
            or self.latest_nick.get(pid) or pid


# ---------------------------------------------------------------- helpers

def pct(w, l, t=0):
    g = w + l + t
    return round((w + 0.5 * t) / g, 4) if g else 0.0


def completed(m):
    return not m.get("in_progress") and any(s["points"] for s in m["teams"])


def result_for(m, team_key):
    a, b = m["teams"]
    me, opp = (a, b) if a["team_key"] == team_key else (b, a)
    if me["points"] > opp["points"]:
        return "W", me, opp
    if me["points"] < opp["points"]:
        return "L", me, opp
    return "T", me, opp


# ---------------------------------------------------------------- main build

def build(seasons, cfg):
    people = People(cfg)
    legacy_w = {**DEFAULT_LEGACY, **cfg.get("legacy_weights", {})}
    power_w = {**DEFAULT_POWER, **cfg.get("power_weights", {})}
    seasons = sorted(seasons, key=lambda s: s["season"])

    mgr = defaultdict(lambda: defaultdict(float))
    rows = defaultdict(list)
    h2h = defaultdict(lambda: defaultdict(float))
    h2h_last = {}
    season_out, power_out = [], {}
    weekly_scores = []          # for records
    season_lines = []           # for season records

    for s in seasons:
        owner = {}
        team_name = {}
        for t in s["teams"]:
            pid, nick = people.canon(t)
            people.latest_nick[pid] = nick
            owner[t["team_key"]] = pid
            team_name[t["team_key"]] = t["name"]

        reg_end = s["playoff_start_week"]
        reg = [m for m in s["matchups"] if not m["is_playoffs"] and m["week"] < reg_end and completed(m)]
        po = [m for m in s["matchups"] if m["is_playoffs"] and completed(m)]
        winners_po = [m for m in po if not m["is_consolation"]]

        # regular season W/L/PF from matchups (fallback: Yahoo standings)
        rec = defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "g": 0})
        for m in reg:
            for side in m["teams"]:
                r, me, opp = result_for(m, side["team_key"])
                x = rec[side["team_key"]]
                x[{"W": "w", "L": "l", "T": "t"}[r]] += 1
                x["pf"] += me["points"]
                x["pa"] += opp["points"]
                x["g"] += 1
        if not reg:
            for t in s["teams"]:
                rec[t["team_key"]] = {"w": t["wins"], "l": t["losses"], "t": t["ties"],
                                      "pf": t["points_for"], "pa": t["points_against"],
                                      "g": t["wins"] + t["losses"] + t["ties"]}

        # all-play by week
        allplay = defaultdict(lambda: [0, 0, 0])
        weekly = defaultdict(dict)
        for m in reg:
            for side in m["teams"]:
                weekly[m["week"]][side["team_key"]] = side["points"]
        for wk, scores in weekly.items():
            for tk, p in scores.items():
                for ok, q in scores.items():
                    if ok == tk:
                        continue
                    allplay[tk][0 if p > q else 1 if p < q else 2] += 1

        # playoffs
        po_teams = {side["team_key"] for m in winners_po for side in m["teams"]}
        for t in s["teams"]:
            if t.get("playoff_seed") and s["is_finished"] and t["playoff_seed"] <= s["num_playoff_teams"]:
                po_teams.add(t["team_key"])
        po_rec = defaultdict(lambda: [0, 0])
        for m in winners_po:
            for side in m["teams"]:
                r, _, _ = result_for(m, side["team_key"])
                if r == "W":
                    po_rec[side["team_key"]][0] += 1
                elif r == "L":
                    po_rec[side["team_key"]][1] += 1

        ranked = [t for t in s["teams"] if t.get("rank")]
        champ = runner = last = None
        title_game = None
        if s["is_finished"] and ranked:
            by_rank = sorted(ranked, key=lambda t: t["rank"])
            champ, runner, last = by_rank[0], by_rank[1] if len(by_rank) > 1 else None, by_rank[-1]
            final_week = max((m["week"] for m in winners_po), default=None)
            for m in winners_po:
                keys = {x["team_key"] for x in m["teams"]}
                if m["week"] == final_week and champ["team_key"] in keys:
                    _, me, opp = result_for(m, champ["team_key"])
                    title_game = {"week": m["week"], "winner_points": me["points"],
                                  "loser_points": opp["points"], "loser": owner.get(opp["team_key"])}
                    if runner is None or opp["team_key"] != runner["team_key"]:
                        runner = next((t for t in s["teams"] if t["team_key"] == opp["team_key"]), runner)

        # per-team season rows and career totals
        for t in s["teams"]:
            tk, pid = t["team_key"], owner[t["team_key"]]
            r = rec[tk]
            ap = allplay[tk]
            finish = None
            if champ and tk == champ["team_key"]:
                finish = "Champion"
            elif runner and tk == runner["team_key"]:
                finish = "Runner-up"
            elif tk in po_teams:
                finish = "Playoffs"
            elif last and tk == last["team_key"]:
                finish = "Last"
            rows[pid].append({
                "season": s["season"], "team": t["name"], "w": r["w"], "l": r["l"], "t": r["t"],
                "pf": round(r["pf"], 2), "pa": round(r["pa"], 2), "rank": t.get("rank"),
                "seed": t.get("playoff_seed"), "finish": finish, "in_progress": not s["is_finished"],
                "allplay_pct": pct(*ap[:2], ap[2]),
            })
            c = mgr[pid]
            c["seasons"] += 1
            c["w"] += r["w"]; c["l"] += r["l"]; c["t"] += r["t"]
            c["pf"] += r["pf"]; c["pa"] += r["pa"]; c["g"] += r["g"]
            c["ap_w"] += ap[0]; c["ap_l"] += ap[1]; c["ap_t"] += ap[2]
            c["trades"] += t.get("trades", 0); c["moves"] += t.get("moves", 0)
            if s["is_finished"]:
                c["finished_seasons"] += 1
                c["playoff_apps"] += tk in po_teams
                c["titles"] += finish == "Champion"
                c["runner_ups"] += finish == "Runner-up"
                c["lasts"] += bool(last and tk == last["team_key"])
            c["po_w"] += po_rec[tk][0]; c["po_l"] += po_rec[tk][1]
            if r["g"]:
                season_lines.append({"season": s["season"], "id": pid, "team": t["name"],
                                     "w": r["w"], "l": r["l"], "t": r["t"], "pf": round(r["pf"], 2),
                                     "ppg": round(r["pf"] / r["g"], 2), "pct": pct(r["w"], r["l"], r["t"]),
                                     "finished": s["is_finished"]})

        # head to head (regular + playoffs, not consolation)
        for m in reg + winners_po:
            a, b = m["teams"]
            pa_, pb_ = owner.get(a["team_key"]), owner.get(b["team_key"])
            if not pa_ or not pb_ or pa_ == pb_:
                continue
            for me_side, me_id, opp_id in ((a, pa_, pb_), (b, pb_, pa_)):
                r, me, opp = result_for(m, me_side["team_key"])
                k = f"{me_id}|{opp_id}"
                h2h[k]["w" if r == "W" else "l" if r == "L" else "t"] += 1
                h2h[k]["pf"] += me["points"]
                h2h[k]["pa"] += opp["points"]
                if m["is_playoffs"]:
                    h2h[k]["po_w" if r == "W" else "po_l"] += 1
                prev = h2h_last.get(k)
                if not prev or (s["season"], m["week"]) >= (prev["season"], prev["week"]):
                    h2h_last[k] = {"season": s["season"], "week": m["week"], "for": me["points"],
                                   "against": opp["points"], "playoffs": m["is_playoffs"]}

        # every weekly score, for records
        for m in reg + po:
            a, b = m["teams"]
            for me, opp in ((a, b), (b, a)):
                weekly_scores.append({
                    "season": s["season"], "week": m["week"], "id": owner.get(me["team_key"]),
                    "team": team_name.get(me["team_key"]), "points": me["points"],
                    "opp": owner.get(opp["team_key"]), "opp_points": opp["points"],
                    "playoffs": m["is_playoffs"], "consolation": m["is_consolation"]})

        # season summary
        def brief(t):
            return {"id": owner[t["team_key"]], "team": t["name"]} if t else None
        reg_leader = max(s["teams"], key=lambda t: (pct(rec[t["team_key"]]["w"], rec[t["team_key"]]["l"], rec[t["team_key"]]["t"]), rec[t["team_key"]]["pf"]), default=None)
        top_scorer = max(s["teams"], key=lambda t: rec[t["team_key"]]["pf"], default=None)
        season_out.append({
            "season": s["season"], "name": s["name"], "teams": len(s["teams"]),
            "finished": s["is_finished"], "current_week": s["current_week"],
            "champion": brief(champ), "runner_up": brief(runner), "last": brief(last),
            "title_game": title_game, "regular_season_leader": brief(reg_leader),
            "top_scorer": {**brief(top_scorer), "pf": round(rec[top_scorer["team_key"]]["pf"], 2)} if top_scorer else None,
        })

        power_out[str(s["season"])] = power_rankings(s, reg, owner, team_name, power_w)

    # ---- assemble managers
    managers = []
    for pid, c in mgr.items():
        net = c["w"] - c["l"]
        parts = {
            "titles": c["titles"] * legacy_w["title"],
            "runner_ups": c["runner_ups"] * legacy_w["runner_up"],
            "playoffs": c["playoff_apps"] * legacy_w["playoff_appearance"],
            "net_wins": net * legacy_w["net_regular_season_win"],
            "last_places": c["lasts"] * legacy_w["last_place"],
        }
        best = None
        for r in rows[pid]:
            if r["finish"] == "Champion":
                best = "Champion"
                break
        managers.append({
            "id": pid, "name": people.name(pid), "seasons": int(c["seasons"]),
            "first": min(r["season"] for r in rows[pid]), "latest": max(r["season"] for r in rows[pid]),
            "w": int(c["w"]), "l": int(c["l"]), "t": int(c["t"]), "pct": pct(c["w"], c["l"], c["t"]),
            "pf": round(c["pf"], 2), "pa": round(c["pa"], 2),
            "ppg": round(c["pf"] / c["g"], 2) if c["g"] else 0,
            "allplay_pct": pct(c["ap_w"], c["ap_l"], c["ap_t"]),
            "luck": round(pct(c["w"], c["l"], c["t"]) - pct(c["ap_w"], c["ap_l"], c["ap_t"]), 4),
            "playoff_apps": int(c["playoff_apps"]), "finished_seasons": int(c["finished_seasons"]),
            "po_w": int(c["po_w"]), "po_l": int(c["po_l"]),
            "titles": int(c["titles"]), "runner_ups": int(c["runner_ups"]), "lasts": int(c["lasts"]),
            "trades": int(c["trades"]), "moves": int(c["moves"]),
            "legacy": round(sum(parts.values()), 2),
            "legacy_parts": {k: round(v, 2) for k, v in parts.items()},
            "best_finish": best,
            "history": sorted(rows[pid], key=lambda r: r["season"]),
        })
    managers.sort(key=lambda m: (-m["legacy"], -m["titles"], -m["pct"]))
    for i, m in enumerate(managers, 1):
        m["legacy_rank"] = i

    names = {m["id"]: m["name"] for m in managers}
    for w in weekly_scores:
        w["name"] = names.get(w["id"], w["id"])
        w["opp_name"] = names.get(w["opp"], w["opp"])
    for l in season_lines:
        l["name"] = names.get(l["id"], l["id"])
    for s in season_out:
        for k in ("champion", "runner_up", "last", "regular_season_leader", "top_scorer"):
            if s.get(k):
                s[k]["name"] = names.get(s[k]["id"], s[k]["id"])
        if s.get("title_game"):
            s["title_game"]["loser_name"] = names.get(s["title_game"]["loser"], s["title_game"]["loser"])
    for season_power in power_out.values():
        for wk in season_power["weeks"].values():
            for r in wk:
                r["name"] = names.get(r["id"], r["id"])

    real = [w for w in weekly_scores if w["points"] > 0 and not w["consolation"]]
    records = {
        "high_week": sorted(real, key=lambda w: -w["points"])[:10],
        "low_week": sorted(real, key=lambda w: w["points"])[:10],
        "blowouts": sorted([w for w in real if w["points"] > w["opp_points"]],
                           key=lambda w: -(w["points"] - w["opp_points"]))[:10],
        "closest": sorted([w for w in real if w["points"] >= w["opp_points"] and w["opp_points"] > 0],
                          key=lambda w: w["points"] - w["opp_points"])[:10],
        "best_seasons": sorted([l for l in season_lines if l["finished"]],
                               key=lambda l: (-l["pct"], -l["pf"]))[:10],
        "highest_ppg_seasons": sorted([l for l in season_lines if l["finished"]],
                                      key=lambda l: -l["ppg"])[:10],
        "worst_seasons": sorted([l for l in season_lines if l["finished"]],
                                key=lambda l: (l["pct"], l["pf"]))[:10],
    }

    h2h_out = {}
    for k, v in h2h.items():
        h2h_out[k] = {kk: (round(v.get(kk, 0), 2) if kk in ("pf", "pa") else int(v.get(kk, 0)))
                      for kk in ("w", "l", "t", "pf", "pa", "po_w", "po_l")}
        h2h_out[k]["last"] = h2h_last.get(k)

    cur = seasons[-1] if seasons else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "league": {
            "name": cfg.get("display_name") or (cur["name"] if cur else "League"),
            "first_season": seasons[0]["season"] if seasons else None,
            "latest_season": cur["season"] if cur else None,
            "current_week": cur["current_week"] if cur else None,
            "seasons_played": len(seasons),
        },
        "managers": managers,
        "seasons": season_out,
        "records": records,
        "h2h": h2h_out,
        "power": power_out,
        "formulas": {"legacy": legacy_w, "power": power_w},
    }


def power_rankings(s, reg, owner, team_name, w):
    weeks = sorted({m["week"] for m in reg})
    out = {}
    for upto in weeks:
        games = [m for m in reg if m["week"] <= upto]
        rec = defaultdict(lambda: [0, 0, 0])
        scores = defaultdict(dict)
        for m in games:
            for side in m["teams"]:
                r, _, _ = result_for(m, side["team_key"])
                rec[side["team_key"]]["WLT".index(r)] += 1
                scores[m["week"]][side["team_key"]] = side["points"]

        def allplay(week_set):
            ap = defaultdict(lambda: [0, 0, 0])
            for wk in week_set:
                sc = scores.get(wk, {})
                for tk, p in sc.items():
                    for ok, q in sc.items():
                        if ok != tk:
                            ap[tk][0 if p > q else 1 if p < q else 2] += 1
            return ap

        ap_all = allplay(weeks[:weeks.index(upto) + 1])
        recent = [x for x in weeks if x <= upto][-int(w["form_weeks"]):]
        ap_form = allplay(recent)
        table = []
        for t in s["teams"]:
            tk = t["team_key"]
            ww, ll, tt = rec[tk]
            g = ww + ll + tt
            record = (ww + 0.5 * tt + 2) / (g + 4) * 100   # shrunk toward .500 early
            strength = pct(*ap_all[tk][:2], ap_all[tk][2]) * 100
            form = pct(*ap_form[tk][:2], ap_form[tk][2]) * 100
            power = w["record"] * record + w["strength"] * strength + w["form"] * form
            table.append({"id": owner[tk], "team": team_name[tk], "w": ww, "l": ll, "t": tt,
                          "record": round(record, 1), "strength": round(strength, 1),
                          "form": round(form, 1), "power": round(power, 1),
                          "luck": round((pct(ww, ll, tt) - strength / 100) * g, 1)})
        table.sort(key=lambda r: -r["power"])
        for i, r in enumerate(table, 1):
            r["rank"] = i
        out[str(upto)] = table
    # rank movement vs previous week
    keys = sorted(out, key=int)
    for a, b in zip(keys, keys[1:]):
        prev = {r["id"]: r["rank"] for r in out[a]}
        for r in out[b]:
            r["move"] = prev.get(r["id"], r["rank"]) - r["rank"]
    return {"season": s["season"], "weeks": out}


def main():
    cfg = json.loads((ROOT / "league.config.json").read_text())
    src = ROOT / "data" / "seasons"
    seasons = [json.loads(f.read_text()) for f in sorted(src.glob("*.json"))]
    if not seasons:
        sys.exit("No seasons in data/seasons yet — run sync/fetch.py first.")
    data = build(seasons, cfg)
    out = ROOT / "site" / "data.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))
    print(f"Wrote {out} — {len(data['managers'])} managers, {len(data['seasons'])} seasons")


if __name__ == "__main__":
    main()
