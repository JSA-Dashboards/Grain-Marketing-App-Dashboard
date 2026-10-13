# Grain Marketing Desk — hosted version

The same app as the Claude version (`grain-desk.html` in the repo root), served from JSA's server
with its own sign-in, so brokers can use it without Claude accounts. Producer access is planned for
the end of the year; later the data moves into Snowflake.

## What runs

| Container | What it does |
| --- | --- |
| `app` | Python (FastAPI). Email sign-in, the app page, and the data API. Data in SQLite on the `grain-data` volume (`/data/grain.db`, contract photos in `/data/assets`). |
| `jobs` | Nightly updates: futures + RMA prices and cash bids from Snowflake (6:10/6:15 PM Central, weekdays), weather from api.weather.gov (5:30 AM daily). |
| `caddy` | HTTPS for the web address, with automatic certificates. |

How the page talks to the server: `server/web/hosted.js` gives the unchanged page the same
`window.claude.use(...)` interface it has as a Claude app (`db`, `user`, `sample`, `assets`,
`downloads`), backed by `/api/*`. Access rules live on the server (`server/app/access.py`).

## Roles

| Role | Sees | Can change |
| --- | --- | --- |
| admin (Megan, JSA staff) | every farm | everything: farms, broker assignments, Intel posts, program deadlines, market data |
| broker | shared market data; only farms assigned to them | their own farms (new farms they add are theirs) |

A farm belongs to the broker account whose **name** matches the farm's Broker field (Farm details).
Brokers can't change that field; an admin can.

## First-time setup on the droplet

1. Install Docker (with the compose plugin) and clone the repo.
2. Point a DNS name (e.g. `grain.<JSA domain>`) at the server; open ports 80 and 443.
3. `cd hosted && cp .env.example .env`, then fill in `.env`:
   - `SITE_ADDRESS` and `PUBLIC_URL`
   - SMTP settings for sign-in emails (leave `SMTP_HOST` empty to print links to the log while testing)
   - `ANTHROPIC_API_KEY` for contract scanning
   - the Snowflake service user (below)
4. Snowflake: create a read-only service user with a key pair (`TYPE = SERVICE`, RSA public key set)
   and a role that can `SELECT` from `JSA.BASIS_TRACKER.FUTURES_PRICES`, `SNAPSHOTS`, `SNAPSHOT_ROWS`,
   `LOCATION_META`, `GRAIN_MAP` and `JSA.CME_FEEDER_CATTLE.FCI_DAILY`, and use the warehouse.
   Put the private key at `hosted/secrets/snowflake_key.p8` (`chmod 600`; not committed).
5. `docker compose up -d --build`
6. Add the first admin: `docker compose exec app python -m app.manage add-user mbrott@jpsi.com "Megan Brott" admin`
7. Bring the data over from the Claude app (Claude can save it as JSON files: `farms/*.json`, `market/*.json`),
   copy the folder to the server, then:
   `docker compose cp ./export app:/tmp/export && docker compose exec app python -m app.manage import /tmp/export`
8. Run the jobs once to check Snowflake and weather access:
   `docker compose exec jobs python -m app.jobs.scheduler futures` (also `bids`, `weather`)

## Day to day

- Add a broker: `docker compose exec app python -m app.manage add-user name@example.com "Tyler Smith" broker`
  (the name must match the Broker field on their farms; their farms link automatically)
- List people and their farm counts: `docker compose exec app python -m app.manage users`
- Turn someone off: `docker compose exec app python -m app.manage set-user name@example.com --active 0`
- Update after a change in GitHub: `git pull && docker compose up -d --build`
- Logs: `docker compose logs -f app` / `docker compose logs -f jobs`

## Backups

Everything that matters is in the `grain-data` volume. A nightly copy, for example from cron on the host:

```
docker compose exec -T app python -c "import sqlite3;s=sqlite3.connect('/data/grain.db');d=sqlite3.connect('/data/backup.db');s.backup(d)"
docker compose cp app:/data/backup.db ./backups/grain-$(date +%F).db
```

Keep `backups/` and the `assets` folder copied off the server (e.g. DigitalOcean Spaces).

## Later: Snowflake

`server/app/store.py` is the only module that touches the database; a move to Snowflake (or Postgres)
replaces it. The existing Snowflake export (tables in `GRAIN_MARKETING.MARKETING`, `sql/setup.sql`)
keeps working from the app's admin button in the meantime.
