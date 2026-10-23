// Reads one season of a Yahoo fantasy football league from Yahoo's own league
// pages, as the signed-in user sees them. Run in a football.fantasysports.yahoo.com
// tab (e.g. the Claude browser panel), then feed the result to
// sync/import_web.py. Used while Yahoo API access is pending approval.
//
//   const out = await yahooSeason(2026, 267485, 470, true);  // current season
//   JSON.stringify(out)
//
// Records exclude Yahoo's weekly "vs median" games: matchups only.
async function yahooSeason(year, lid, gameKey, current) {
  const base = current ? `/f1/${lid}` : `/${year}/f1/${lid}`;
  // Yahoo answers 999 when pages are requested too quickly; go slowly.
  const go = async u => {
    await new Promise(r => setTimeout(r, 2500));
    const res = await fetch(u, { credentials: "include" });
    if (res.status === 999) throw new Error("Yahoo rate limit (999); wait and retry");
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${u}`);
    return new DOMParser().parseFromString(await res.text(), "text/html");
  };
  const num = s => parseFloat(String(s).replace(/[^0-9.\-]/g, "")) || 0;
  const tid = h => { const m = (h || "").match(/\/f1\/\d+\/(\d+)(?:$|[?#\/])/); return m ? +m[1] : null; };

  const set = await go(`${base}/settings`);
  const stxt = (set.querySelector("#yspmaincontent") || set.body).innerText;
  const pm = stxt.match(/Playoffs:\s*(\d+)\s*teams\s*-\s*Weeks?\s*([\d,\sand]+)/i);
  const poWeeks = pm ? (pm[2].match(/\d+/g) || []).map(Number) : [];

  const teams = {};
  const tp = await go(`${base}/teams`);
  const head = [...tp.querySelectorAll("table thead th")].map(th => th.innerText.trim().toLowerCase());
  const col = name => head.indexOf(name);
  for (const tr of tp.querySelectorAll("table tbody tr")) {
    const a = [...tr.querySelectorAll("a")].find(x => tid(x.getAttribute("href")) && x.innerText.trim());
    if (!a) continue;
    const t = tid(a.getAttribute("href"));
    let mgrs = [...tr.querySelectorAll("span.user-id a")].map(m =>
      ((m.getAttribute("href") || "").split("/profile/")[1] || m.innerText.trim()) + "~" + m.innerText.trim());
    if (!mgrs.length) mgrs = [`hidden-${t}~${((tr.querySelector("td.user-id") || {}).innerText || "Unknown").trim()}`];
    const cells = [...tr.querySelectorAll("td")].map(td => td.innerText.trim());
    teams[t] = [t, a.innerText.trim(), mgrs.join("^"), null, 0, 0, 0, 0, 0, 0,
      col("moves") >= 0 ? num(cells[col("moves")]) : 0, col("trades") >= 0 ? num(cells[col("trades")]) : 0];
  }

  const sp = await go(`${base}?lhst=stand`);
  for (const tr of sp.querySelectorAll("#standingstable tbody tr")) {
    const a = tr.querySelector('a[href*="/f1/"]');
    const t = tid(a && a.getAttribute("href"));
    if (!t || !teams[t]) continue;
    const c = [...tr.querySelectorAll("td")].map(td => td.innerText.replace(/\s+/g, " ").trim());
    const [w, l, ti] = (c[2] || "0-0-0").split("-").map(Number);
    Object.assign(teams[t], { 3: parseInt(c[0].replace("*", "")) || null, 4: c[0].includes("*") ? 1 : 0,
      5: w, 6: l, 7: ti || 0, 8: num(c[3]), 9: num(c[4]) });
  }

  const home = await go(`${base}?module=matchups&lhst=matchups`);
  const opts = [...home.querySelectorAll("#matchupweek select option")];
  const wk = o => +((o.value.match(/matchup_week=(\d+)/) || [])[1]);
  const weeks = opts.map(wk).filter(Boolean);
  const sel = opts.find(o => o.selected);
  const curWeek = sel ? wk(sel) : Math.max(...weeks);
  const endWeek = poWeeks.length ? Math.max(...poWeeks) : Math.max(...weeks);
  const poStart = poWeeks.length ? Math.min(...poWeeks) : endWeek - 2;
  const inPO = new Set(Object.values(teams).filter(t => t[4]).map(t => t[0]));

  const games = [];
  for (let w = 1; w <= (current ? curWeek : endWeek); w++) {
    const d = await go(`${base}?matchup_week=${w}&module=matchups&lhst=matchups`);
    const final = /final/i.test((d.querySelector("#matchupweek .Bg-shade") || {}).innerText || "");
    for (const li of d.querySelectorAll("#matchupweek li[data-target]")) {
      const m = li.dataset.target.match(/mid1=(\d+)&mid2=(\d+)/);
      const sc = [...li.querySelectorAll(".Fz-lg")].map(e => num(e.innerText));
      if (!m || sc.length < 2) continue;
      const a = +m[1], b = +m[2], po = w >= poStart;
      games.push([w, (po ? 1 : 0) + (po && !(inPO.has(a) && inPO.has(b)) ? 2 : 0) + (final ? 0 : 4), a, sc[0], b, sc[1]]);
    }
  }
  return { y: year, k: `${gameKey}.l.${lid}`, n: (home.title || "").split("|")[0].trim(),
    e: endWeek, c: current ? curWeek : endWeek, f: current ? 0 : 1, ps: poStart,
    np: pm ? +pm[1] : 6, t: Object.values(teams), m: games };
}
