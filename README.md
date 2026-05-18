# Fiber Trades — Economic Calendar Alert Bot

Posts high-impact economic events to Discord 0–10 minutes before they release.

## What it does

Every 5 minutes (via GitHub Actions cron), this bot:

1. Downloads this week's economic calendar from the public Forex Factory mirror
2. Filters to **High-impact** USD / EUR / GBP events
3. Finds events releasing in the next 10 minutes
4. Posts an alert to your Discord channel via webhook
5. Tracks which events it has already posted so re-runs never double-post

**Example alert in Discord:**

```
🔴 USD · Core CPI m/m
⏰ 13:30 London  ·  High-impact
Forecast: 0.3%  ·  Prior: 0.4%
```

## Setup — one-time

### Step 1 — Create the Discord channel and webhook

1. In your Discord server, create a text channel: **🔔economic-calendar** (or use an existing one)
2. Right-click the channel → **Edit Channel** → **Integrations** → **Webhooks**
3. Click **New Webhook** → name it "Fiber Trades · Calendar"
4. Click **Copy Webhook URL**
5. Save the URL somewhere — you'll need it in Step 4

### Step 2 — Create a GitHub repo for the bot

1. Go to https://github.com → **New repository**
2. Name it `fiber-trades-calendar-bot`
3. Choose **Public** (required for free GitHub Actions)
4. Initialize with a README
5. Create

### Step 3 — Upload the bot files

Upload both of these to the new repo:

- `calendar_bot.py` (the bot itself)
- `.github/workflows/calendar.yml` (the schedule)

**To upload via GitHub web:**

1. `calendar_bot.py` → **Add file** → **Upload files** → drag the file → Commit
2. `.github/workflows/calendar.yml` requires a folder. Click **Add file** → **Create new file** → in the filename field, type `.github/workflows/calendar.yml` (the slashes create the folders automatically). Paste the contents → Commit.

### Step 4 — Add the Discord webhook as a secret

1. In the repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `DISCORD_WEBHOOK_URL`
4. Value: paste the webhook URL from Step 1
5. Click **Add secret**

### Step 5 — Test it

1. Go to the **Actions** tab in your repo
2. Click **Economic Calendar Alerts** in the left sidebar
3. Click **Run workflow** → **Run workflow** (this triggers it manually)
4. Wait ~30 seconds, refresh, and click the run that just appeared to see logs
5. If everything's set up correctly, the logs show the bot fetched the calendar and either posted alerts (if any events are imminent) or said "Nothing to alert"

### Step 6 — Verify in Discord

If a high-impact USD/EUR/GBP event is within the next 10 minutes, an alert appears in your channel. Otherwise, wait until one is. You can verify on https://www.forexfactory.com/calendar what's coming up.

## Customising

Edit `calendar_bot.py` at the top to change behaviour:

- **`IMPACT_FILTER`** — set to `{"High", "Medium"}` to include medium-impact events too
- **`CURRENCY_FILTER`** — add `"JPY"`, `"AUD"`, etc. for more pairs
- **`ALERT_WINDOW_MAX`** — change `10` to `5` for tighter alerts, or `15` for more lead-time

After editing, just commit — the next scheduled run uses the new settings.

## Costs

**£0 forever** as long as:
- The repo stays public (private repos have limited Actions minutes)
- Each run completes in under 30 seconds (they do — typically 5-10 sec)
- You don't add other workflows that consume the free tier

GitHub gives public repos unlimited Actions minutes.

## How GitHub Actions cron works

The 5-minute schedule is **best-effort**. Under load, GitHub may delay runs by 5-15 minutes. For economic event alerts this is acceptable since:

1. Most market participants already know events are coming (the calendar is public)
2. The 10-minute alert window has buffer for delayed runs
3. You shouldn't be trading the actual release anyway

If you want guaranteed sub-minute precision, you'd need a paid cron service like Cloudflare Workers or a small VPS (~£3/month).

## Files

- `calendar_bot.py` — the bot
- `.github/workflows/calendar.yml` — GitHub Actions schedule
- `alerted.json` — auto-generated state file (do not edit manually; the bot commits this)
- `README.md` — this file

## Troubleshooting

**Bot runs but nothing posts to Discord:**
- Check the Actions log — does it say "Eligible events in alert window: 0"? That means no high-impact events are imminent. Wait until one is.
- Does the log say "✗ No webhook URL set"? You missed Step 4 — add the secret.
- Does Discord show the webhook is created? Check Server Settings → Integrations → Webhooks.

**Bot fails with HTTP error fetching calendar:**
- The mirror site may be temporarily down. Wait 10-15 minutes and retry.

**Bot posts duplicates:**
- The `alerted.json` file may have failed to commit. Check the Actions log for git push errors. Usually resolves itself on next run.

**Cron is delayed:**
- Normal — GitHub Actions cron is best-effort. Tolerable for this use case.

