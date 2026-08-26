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
2. That's it — two workflows now run automatically every day using
   those same two secrets:
   - `.github/workflows/refresh-statcan-cache.yml` (12:00 UTC) refreshes
     a cache of real StatCan data in the background, so the app doesn't
     depend on StatCan's API being reachable and fast at the exact
     moment someone opens the page — StatCan's API has turned out to be
     real but flaky to call live (see the comments in
     `backend/data_sources/statcan_http.py` if you're curious why).
   - `.github/workflows/daily-alerts.yml` (13:00 UTC, an hour later)
     checks every saved alert and emails anyone whose target was hit.
3. To test either one right now instead of waiting: go to the **Actions**
   tab → pick the workflow → **Run workflow**.

### 6. Try it out

1. Open your Streamlit app URL.
2. On **📈 Explore & Forecast**, pick a location and see the real trend + forecast.
3. On **🔔 My Alerts**, enter your email and save an alert (e.g. Toronto, "below", some target).
4. Manually run the Actions workflow (step 5.3) once to confirm you get the email.

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
