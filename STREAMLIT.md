# Grain Marketing Desk on Streamlit Community Cloud

Branch `streamlit-port`. Same page (`grain-desk.html`), same access rules, same nightly jobs as the Docker
version in `hosted/`. What changes: Streamlit serves it, Snowflake stores it, sign-in is a shared password plus
an emailed code. Nothing under `hosted/` behaves differently when `STORE_BACKEND` is unset (it stays SQLite).

## How it fits together

| Piece | File |
| --- | --- |
| Entry point, navigation (admins also get a People page) | `streamlit_app.py` |
| Sign-in: app password, work email, 6-digit code, 30-day cookie | `desk/auth.py`, `desk/mailer.py` (Microsoft Graph) |
| The page in an iframe, calls to Python as one batch at a time | `desk/component.py`, `desk/shim.js` |
| The page's data calls (same rules as `main.py`) | `desk/ops.py` + `hosted/server/app/access.py` |
| Storage in Snowflake `JSA.GRAIN_DESK` | `hosted/server/app/store_sf.py`, `sql/grain_desk_setup.sql` |
| Nightly futures, bids, weather | `hosted/server/app/jobs/` on the droplet cron (below) |

Who a person is comes only from their account behind the code or cookie. The page cannot claim to be someone else.

## Setup, in order

1. **Snowflake.** Run `sql/grain_desk_setup.sql` (SYSADMIN, then SECURITYADMIN). Create `GRAIN_DESK_SVC` with a key pair
   (TYPE = SERVICE, `GRAIN_DESK_ROLE`). That one role runs the app and the jobs.
2. **First admin.** After step 1: `INSERT INTO JSA.GRAIN_DESK.USERS(ID,EMAIL,NAME,ROLE,ACTIVE,CREATED_AT) SELECT 'u_megan','mbrott@jpsi.com','Megan Brott','admin',TRUE,CURRENT_TIMESTAMP();`
   After that, people are added on the app's People page.
3. **Streamlit Cloud secrets** (never committed):
   ```
   SNOWFLAKE_ACCOUNT = "JSA-ANALYTICS"
   SNOWFLAKE_USER = "GRAIN_DESK_SVC"
   SNOWFLAKE_ROLE = "GRAIN_DESK_ROLE"
   SNOWFLAKE_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----..."""
   APP_PASSWORD = "<shared password for the people who need access>"
   ANTHROPIC_API_KEY = "<JSA key, contract scanning>"
   GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET / GRAPH_SENDER   (sign-in code emails)
   ```
   Without the four GRAPH values nobody can receive a code (set `GRAIN_DEV_SHOW_CODE = "1"` only for testing).
4. **ZIP codes.** `python scripts/build_zips.py` writes `static/zips/zips-0..9.csv` (Census gazetteer, public domain); commit them.
5. **Nightly jobs on the droplet.** Clone the repo, `pip install -r hosted/server/requirements.txt`, and run with
   `STORE_BACKEND=snowflake` and the same Snowflake variables: `python -m app.jobs.scheduler futures|bids|weather`
   (cron: futures 18:10 and bids 18:15 CT weekdays, weather 05:30 CT daily; the scheduler module can also stay running).
   The Docker `app`/`caddy` containers are not used.
6. **Move Megan's data.** `python -m app.manage import <folder>` with `STORE_BACKEND=snowflake`.

## Known limits

- The app is public, so the password is the outer gate; the emailed code and account list are what protect farm data.
- Every page action is one round trip through Streamlit (about 0.2 to 1.5 s). Reads are cached for 4 s.
- Contract photos are stored in Snowflake and are limited to 7 MB each.
- A first visit after the app sleeps takes longer while Streamlit restarts it.
