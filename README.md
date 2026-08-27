# 🏠 Canada Housing Price Tracker

Track housing price trends across Canada, forecast where they're headed
(short/medium/long term), and get emailed automatically when a location
hits the price you're waiting for. **Runs entirely on free tiers — $0/month.**

This README assumes you don't code. Every step below is click-by-click.
It'll take about 30–45 minutes the first time, one time only.

---

## What this app actually does (read this first)

There is no free source of real, live, per-city, per-property-type
(condo vs. detached, etc.) resale housing prices in Canada — that data
is sold commercially (CREA/MLS boards, Repliers, etc.). So this app
combines two data sources, both wired up and both labeled honestly in
the app itself:

- **Statistics Canada's New Housing Price Index (NHPI)** — real, free,
  government data, updated monthly. Two catches: it tracks what
  *builders* charge for *new* houses, not resale/MLS averages, and it
  only splits into "Total (house and land)", "House only", and "Land
  only" — not condo vs. detached vs. townhouse. It also only breaks
  down to **Canada + a few provinces/regions** — StatCan doesn't
  publish a separate series per city within this table, so there's no
  "Toronto" or "Vancouver" option here (see
  `backend/data_sources/statcan_cache.py` for the exact list, and why
  it's a hardcoded set rather than looked up dynamically — StatCan's
  metadata-lookup API proved too unreliable to call live). This is the
  **real backbone** of the app.
- **Repliers MLS API** — real per-property-type, per-city sold-price
  data (condo, detached, townhouse, etc.) *if* you pay for a
  production API key. On the **free/sandbox key** it returns
  realistic-looking **sample data, not real listings** — the app
  labels this clearly every time it's shown. It's wired up and ready
  to go fully real the moment you're willing to pay for a key (see
  "Going further" at the bottom).

Forecasts (short ≈ 3 months, medium ≈ 12 months, long ≈ 3 years) are
generated from whichever series you're looking at, using a few
different statistical methods (Holt-Winters, ARIMA, linear trend,
moving average) shown side by side so you can see how much they agree.

A third tab, **🏘️ Best Places to Live**, is a separate tool in the same
app: it ranks the 21 Metro Vancouver municipalities + Electoral Area A
against each other on safety, affordability, walkability, transit
access, green space, population density, and household income, with
sliders to weight what matters to you. See the dedicated section below
for exactly what each of those criteria measures (and doesn't).

---

## 🏘️ Best Places to Live (Metro Vancouver) — what it measures

Scored at the **municipality** level (Vancouver, Burnaby, Surrey,
Richmond, etc.) — the coarsest granularity, but the only one where
every criterion below has real, free, region-wide data. Every number
is free public data, no API key or signup required:

| Criterion | Source | What it actually measures |
|---|---|---|
| Safety | Statistics Canada, Crime Severity Index by police service (table 35-10-0063-01) | Lower = safer. A few municipalities share one police service (e.g. Maple Ridge + Pitt Meadows both under Ridge Meadows RCMP) and get the same value — noted in the detail view. A handful of smaller municipalities don't have a confidently-mapped police service yet and show "not available" rather than a guessed number (see `backend/data_sources/livability_geography.py`). |
| Affordability | CMHC Rental Market Survey data tables | **Average rent, not resale/purchase price** — no free, structured, per-municipality source of resale housing prices exists (same gap as the NHPI section above). This is, by a wide margin, this tab's shakiest data source — see `backend/data_sources/livability_cmhc.py`. |
| Walkability | OpenStreetMap (Overpass API) | Density of grocery stores, restaurants, cafes, and pharmacies. An **amenity-density proxy**, explicitly not a real Walk Score (which would require a paid or signup API key, out of scope for this free-only build). |
| Transit access | OpenStreetMap (Overpass API) | Density of bus stops, transit platforms, and rail stations. |
| Green space | OpenStreetMap (Overpass API) | Density of parks, gardens, and nature reserves. |
| Population density | Statistics Canada, 2021 Census (table 98-10-0002-01) | People per km². Shown, but **not counted in the ranking by default** — "denser is better" is a matter of taste, not something to score objectively. Turn its weight up and pick a direction if you disagree. |
| Household income | Statistics Canada, 2021 Census (table 98-10-0057-01) | Median household income, shown as context next to rent — also not counted by default. |

**Honest caveat about this tab specifically**: the "Best Places to
Live" data sources were originally wired up in an environment with no
live network access to StatCan, CMHC, or OpenStreetMap, so the first
real `POST /livability/refresh-cache` on Render doubled as their actual
verification step — the same process this repo's own StatCan
integration went through before it was solid. That first run caught
and fixed two real bugs (StatCan's crime/census tables were being
fetched via an unreliable WDS endpoint — switched to a direct static
CSV download; OpenStreetMap calls were failing with Render's known
IPv6 routing issue — switched to the same forced-IPv4 fix StatCan
already uses). **Rent is still unresolved**: the guessed CMHC download
URL returned a 403 (it's the HTML page that *links to* the real
workbook, not the workbook itself). To fix it: open
[CMHC's Rental Market Report Data Tables page](https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/housing-data/data-tables/rental-market/rental-market-report-data-tables)
in a real browser, find the current Metro Vancouver / Vancouver CMA
data table download link (should end in `.xlsx`), and set it as
`CMHC_RENT_XLSX_URL` in Render's environment variables — no redeploy
or code change needed, just re-run the refresh workflow after. Until
then, the "Affordability" criterion shows "not available" for every
municipality; everything else should be populated. Weighting/ranking
math itself is fully tested (see the "Compute rankings" logic in
`frontend/app.py`) — it was always the raw data fetches that needed
that real-world check.

---

## How it's built (so you know what you're setting up)

| Piece | What it does | Where it lives (free tier) |
|---|---|---|
| **Backend** | Pulls housing data, runs forecasts, stores your alerts, sends alert emails | [Render](https://render.com) (Free web service) |
| **Frontend** | The web page you actually use | [Streamlit Community Cloud](https://streamlit.io/cloud) |
| **Database** | Stores your saved price alerts + a daily-refreshed cache of StatCan data | [Supabase](https://supabase.com) (Free Postgres) |
| **Email** | Sends the "your price target was hit" email | Your free Gmail account |
| **Daily scheduler** | Refreshes the StatCan cache, then checks every alert | GitHub Actions (free for public repos) |

Nothing here needs a credit card.

---

## Setup — one time only

### 1. Create a free Supabase database (stores your alerts)

Supabase changes this screen fairly often, so go by what you see rather
than exact menu names — the important part is which connection string
to grab.

1. Go to [supabase.com](https://supabase.com) → **Start your project** → sign in with GitHub.
2. Click **New project**. Pick any name/region, set a database password (save it somewhere — you'll need it below).
3. Once the project's finished setting up, look for a **Connect** button
   (usually near the top of the project's main page, sometimes with a
   plug icon) — that's where the connection string lives now, not
   under Settings. Click it.
4. That panel offers a few connection types — **don't use "Direct
   connection."** Pick **Session pooler** instead. Two reasons: Render
   (where the backend lives) can't reach Supabase's direct-connection
   address, and the pooler is the option meant for exactly this case
   (an app hosted elsewhere connecting in).
5. What you see next depends on which layout Supabase gives you:

   - **A ready-made string** — copy the **URI** shown under Session
     pooler. It looks like:
     `postgresql://postgres.xxxxxxxxxxxx:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`
     Replace the literal text `[YOUR-PASSWORD]` (brackets and all)
     with the database password from step 2 — leave everything else
     in the string untouched.
   - **Separate fields instead** (a "Connection parameters" card
     showing `host`, `port`, `database`, `user` one by one, with no
     single string to copy) — assemble it yourself in this shape:
     `postgresql://USER:PASSWORD@HOST:PORT/DATABASE`
     using the `user`, `host`, `port`, and `database` values shown
     (typically `user` looks like `postgres.xxxxxxxxxxxx` and
     `database` is `postgres`), and the password from step 2 for
     `PASSWORD`. There's a **"Reset database password"** button right
     on that same card if you don't remember it — click it, set a new
     one, and use that.

   Either way, save the finished string — you'll paste it into Render
   in step 3 below, as `DATABASE_URL`.

   If your password has characters like `@ : / # ?` in it, Supabase's
   own note ("percent-encode them") is correct but fiddly — simpler
   to just reset the password to letters-and-numbers-only (e.g.
   `Sunflower92`) via that same button, so you can skip encoding
   entirely.

If you truly can't find a "Connect" button anywhere: open any table in
the **Table Editor**, and Supabase shows a "Connect via" shortcut
there too — same panel, different door in.

### 2. Create a free Gmail "app password" (sends your alert emails)

1. Use an existing Gmail account, or create a free one at [accounts.google.com/signup](https://accounts.google.com/signup).
2. Turn on 2-Step Verification (required for app passwords): [myaccount.google.com/security](https://myaccount.google.com/security).
3. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), sign in again if asked, name it "Housing Tracker", click **Create**.
4. Copy the 16-character password shown (spaces don't matter). This is **not** your normal Gmail password — save it for step 3.

### 3. Deploy the backend to Render

1. Go to [render.com](https://render.com) → sign up with GitHub → **New → Web Service**.
2. Connect this GitHub repo, and when asked for the root directory, enter `backend`.
3. Render should auto-detect the build/start commands from `backend/render.yaml`:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   If it doesn't auto-fill, paste those in yourself. Pick the **Free** instance type.
4. Before clicking Create, add these **Environment Variables** (Render's "Environment" section):

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the Supabase connection string from step 1 |
   | `GMAIL_ADDRESS` | your Gmail address |
   | `GMAIL_APP_PASSWORD` | the 16-character app password from step 2 |
   | `ALERTS_SECRET` | make up any long random string (e.g. mash your keyboard) — this protects the daily alert-check endpoint from strangers on the internet |
   | `REPLIERS_API_KEY` | *(optional)* leave unset for now — see "Going further" below |

5. Click **Create Web Service**. First deploy takes a few minutes. When it's live, copy its URL (something like `https://housing-tracker-backend.onrender.com`).

   Note: on the free tier, Render puts the backend to sleep after ~15 minutes of no traffic. The next request takes 30-60 seconds to wake it up — that's normal, not broken.

### 4. Deploy the frontend to Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub → **New app**.
2. Pick this repo, branch, and set the main file path to `frontend/app.py`.
3. Before/after deploying, open **Settings → Secrets** for the app and add:
   ```toml
   API_URL = "https://your-backend-url-from-step-3.onrender.com"
   ```
4. Deploy. You'll get a public URL like `https://your-app.streamlit.app` — that's the site you actually use and can share.

### 5. Turn on the daily alert check

1. In this GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `ALERTS_URL` = your backend URL + `/run-alerts`, e.g. `https://housing-tracker-backend.onrender.com/run-alerts`
   - `ALERTS_SECRET` = the exact same random string you set on Render in step 3
2. That's it — three workflows now run automatically using those same
   two secrets:
   - `.github/workflows/refresh-statcan-cache.yml` (12:00 UTC daily)
     refreshes a cache of real StatCan data in the background, so the
     app doesn't depend on StatCan's API being reachable and fast at
     the exact moment someone opens the page — StatCan's API has
     turned out to be real but flaky to call live (see the comments in
     `backend/data_sources/statcan_http.py` if you're curious why).
   - `.github/workflows/daily-alerts.yml` (13:00 UTC daily, an hour
     later) checks every saved alert and emails anyone whose target
     was hit.
   - `.github/workflows/refresh-livability-cache.yml` (12:00 UTC on
     the 1st of each month) refreshes the **🏘️ Best Places to Live**
     tab's data — monthly, not daily, since census/crime/rent data
     doesn't change nearly as often as housing prices do.
3. To test any of these right now instead of waiting: go to the
   **Actions** tab → pick the workflow → **Run workflow**. Do this once
   for `refresh-livability-cache.yml` after your first deploy —
   see the caveat above about this tab's data sources needing a real
   first check.

### 6. Try it out

1. Open your Streamlit app URL.
2. On **📈 Explore & Forecast**, pick a location and see the real trend + forecast.
3. On **🔔 My Alerts**, enter your email and save an alert (e.g. Toronto, "below", some target).
4. Manually run the Actions workflow (step 5.3) once to confirm you get the email.
5. On **🏘️ Best Places to Live**, adjust a few sliders and confirm the ranking updates — if it says no data is cached yet, go run `refresh-livability-cache.yml` from step 5.3.

---

## Running it on your own computer (optional, for testing)

```bash
# backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload

# frontend, in a second terminal
cd frontend
pip install -r requirements.txt
streamlit run app.py
```

Without `DATABASE_URL` set, the backend uses a local `watches.db` SQLite
file automatically — fine for testing, but it won't persist on Render's
free tier (its disk is wiped on restart), which is why step 1 above
uses Supabase for anything you actually want to keep.

---

## Going further

- **Real MLS prices by property type**: sign up at
  [repliers.io](https://docs.repliers.io) for a production API key,
  add it as `REPLIERS_API_KEY` on Render, and the "Repliers MLS" data
  source in the app switches from sample data to real listings
  automatically — no code changes needed.
- **More forecasting variables** (interest rates, population growth,
  housing starts, etc.): StatCan publishes many other free series
  (mortgage rates, building permits, population). Each can be pulled
  the same way `statcan.py` already does, and blended into the
  forecast — ask for this as a follow-up if you want it built out.
- **SMS instead of email**: swap `notify.py` for a free-tier
  Twilio/Telegram integration.
- **A real Walk Score** on the Best Places to Live tab instead of the
  OpenStreetMap amenity-density proxy: sign up at
  [walkscore.com](https://www.walkscore.com/professional/api.php) for
  a free API key and wire it into `livability_osm.py`.
- **Finer-than-municipality detail inside Vancouver itself**: the City
  of Vancouver publishes richer open data broken down into its 22
  official local areas (Kitsilano, Mount Pleasant, etc.) — none of the
  other Metro Vancouver municipalities publish anything comparable, so
  this would only ever cover Vancouver, as an added drill-down rather
  than a replacement for the region-wide municipality view.
- **Air quality and school quality** as additional criteria: left out
  because neither has a clean free per-municipality source — Metro
  Vancouver's air quality monitoring stations are too sparse to cover
  every municipality, and there's no free, structured school-ratings
  dataset.
