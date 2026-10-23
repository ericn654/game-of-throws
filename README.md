# Game of Throws — league site

A self-hosted league hall of fame that syncs from the Yahoo Fantasy API and
publishes to GitHub Pages. No server, no database, no subscription.

**Pages:** all-time standings (sortable), weekly power rankings, every
championship banner, head-to-head rivalries, record book, and a career page
for each manager.

## How it works

```
GitHub Actions (Tue + Fri)
  sync/fetch.py   Yahoo API  ->  data/seasons/<year>.json   (finished seasons cached)
  sync/stats.py   seasons    ->  site/data.json
  deploy          site/      ->  GitHub Pages
```

## One-time setup

1. **Yahoo app** — https://developer.yahoo.com/apps/create
   - Application name: anything (e.g. `Game of Throws Site`)
   - Application type: **Web Application**
   - Redirect URI: `https://localhost:8080`
   - API permissions: **Fantasy Sports → Read**
   - Copy the **Client ID** and **Client Secret**.
2. **GitHub secrets** — repo **Settings → Secrets and variables → Actions**
   - Secrets: `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`
   - Variables tab: `YAHOO_LEAGUE_ID` = the number in your Yahoo league URL
     (`football.fantasysports.yahoo.com/f1/<this number>`)
3. **Pages** — **Settings → Pages → Source: GitHub Actions**
4. **Connect Yahoo** — **Actions → Connect Yahoo → Run workflow** (code empty).
   Open the link in the run summary, approve, and copy the `code=` value from the
   address bar of the page you land on (the page itself won't load; that's
   expected). Run the workflow again and paste the code.
5. **First sync** — **Actions → Sync league → Run workflow**. The site URL is
   shown on the deploy step.

## Customizing

`league.config.json`
- `names` — display names by Yahoo manager guid or nickname.
- `merge` — fold one manager into another, e.g. when someone switched Yahoo
  accounts: `{"<old guid or nickname>": "<guid to keep>"}`.
- `legacy_weights`, `power_weights` — scoring formulas shown on the site.

Add `?me=YourName` to the site URL (or tap "This is me" on your page) to
highlight your rows.

## Notes

- The refresh token is stored encrypted in `.yahoo/token.enc` (key = your
  client secret). Changing the client secret means re-running Connect Yahoo.
- The repo is public on the free GitHub plan, so league data (names, scores)
  is public too. The site is marked `noindex` so search engines skip it.
- `python3 -m unittest discover -s tests` runs the parser and stats tests.
