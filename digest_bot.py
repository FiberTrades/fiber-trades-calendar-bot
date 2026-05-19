"""
═══════════════════════════════════════════════════════════════════════════════
FIBER TRADES — WEEKLY CALENDAR BRIEFING
═══════════════════════════════════════════════════════════════════════════════

Every Sunday 18:00 UTC: posts one Discord message per weekday (Mon–Fri),
each containing that day's events with forecasts, priors, and holidays.

This is a forward-looking pre-session briefing — it does NOT auto-update
with actuals after events release. The XML feed (faireconomy.media mirror
of Forex Factory) does not publish actuals. For real-time results, check
forexfactory.com or your charting platform directly.

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

WEBHOOK_URL     = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
CALENDAR_URL    = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
CURRENCY_FILTER = {"USD", "GBP", "CHF", "EUR"}
IMPACT_FILTER   = {"High", "Medium", "Low", "Holiday", "Non-Economic", ""}
STATE_FILE      = "digest_state.json"
BOT_USERNAME    = "Fiber Trades · Calendar"
BOT_AVATAR      = "https://cdn.discordapp.com/embed/avatars/0.png"

# User-Agent required by Discord for webhook POSTs from cloud IPs
USER_AGENT = "FiberTradesCalendarBot (https://github.com/FiberTrades/fiber-trades-calendar-bot, 1.0)"

# Days to include. Weekend usually has nothing, so Mon–Fri only.
DIGEST_WEEKDAYS = [0, 1, 2, 3, 4]  # 0=Mon, 4=Fri

FLAGS  = {"USD": "🇺🇸", "GBP": "🇬🇧", "CHF": "🇨🇭", "EUR": "🇪🇺"}
DAYS   = ["Monday", "Tuesday", "Wednesday", "Thursday",
          "Friday", "Saturday", "Sunday"]
IMPACT_EMOJI = {
    "High":         "🔴",
    "Medium":       "🟡",
    "Low":          "🟢",
    "Holiday":      "🏖️",
    "Non-Economic": "📌",
    "":             "⚪",
}


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}")


# ───────────────────────── CALENDAR FETCH + PARSE ────────────────────────────

def fetch_calendar():
    log(f"Fetching calendar from {CALENDAR_URL}")
    req = Request(
        CALENDAR_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/xml, text/xml, */*",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except URLError as e:
        log(f"  ✗ Failed: {e}")
        sys.exit(1)


def parse_calendar(xml_bytes):
    events = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log(f"  ✗ XML parse error: {e}")
        return events

    for ev in root.findall(".//event"):
        title    = (ev.findtext("title") or "").strip()
        country  = (ev.findtext("country") or "").strip()
        date_str = (ev.findtext("date") or "").strip()
        time_str = (ev.findtext("time") or "").strip()
        impact   = (ev.findtext("impact") or "").strip()
        forecast = (ev.findtext("forecast") or "").strip()
        previous = (ev.findtext("previous") or "").strip()

        if country not in CURRENCY_FILTER:
            continue
        if impact not in IMPACT_FILTER:
            continue

        m = re.match(r"(\d{2})-(\d{2})-(\d{4})", date_str)
        if not m:
            continue
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        event_date = datetime(year, month, day).date()

        sort_key = 0
        time_display = "All Day"
        if time_str and time_str.lower() not in ("all day", "tentative"):
            t = re.match(r"(\d{1,2}):(\d{2})(am|pm)", time_str.lower())
            if t:
                hour, minute, ampm = int(t.group(1)), int(t.group(2)), t.group(3)
                if ampm == "pm" and hour != 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0
                london_hour, london_minute = utc_to_london(
                    year, month, day, hour, minute
                )
                time_display = f"{london_hour:02d}:{london_minute:02d}"
                sort_key = london_hour * 60 + london_minute
        elif time_str.lower() == "tentative":
            time_display = "Tentative"
            sort_key = 9999

        events.append({
            "date":         event_date,
            "time_display": time_display,
            "sort_key":     sort_key,
            "currency":     country,
            "title":        title,
            "impact":       impact,
            "forecast":     forecast,
            "previous":     previous,
        })

    return events


def utc_to_london(year, month, day, hour, minute):
    """Convert ForexFactory XML time (which is in UTC) to London local time."""
    dt_naive = datetime(year, month, day, hour, minute)
    uk_dst = last_sunday(year, 3) <= dt_naive.date() < last_sunday(year, 10)
    delta_hours = 1 if uk_dst else 0
    dt_london = dt_naive + timedelta(hours=delta_hours)
    return dt_london.hour, dt_london.minute


def last_sunday(year, month):
    from calendar import monthrange
    d = datetime(year, month, monthrange(year, month)[1])
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d.date()


# ───────────────────────── MESSAGE BUILDING ──────────────────────────────────

def build_day_message(date, events_for_day, is_first_day=False):
    """Build the message content for a single day."""
    day_name = DAYS[date.weekday()]
    date_str = date.strftime(f"{day_name} %d %b")

    lines = []
    if is_first_day:
        lines.append("🗓️ **Week Ahead — Calendar Briefing**")
        lines.append("_Forecasts and priors only. Check Forex Factory for live actuals._")
        lines.append("")
    lines.append(f"━━━ **{date_str}** ━━━")

    if not events_for_day:
        lines.append("_No scheduled events._")
    else:
        holidays = [e for e in events_for_day
                    if e["impact"] == "Holiday"
                    or e["time_display"] == "All Day"
                    or "holiday" in e["title"].lower()]
        timed    = [e for e in events_for_day if e not in holidays]

        for h in holidays:
            flag = FLAGS.get(h["currency"], "")
            lines.append(f"🏖️ {flag} {h['currency']} — {h['title']}")

        timed.sort(key=lambda e: e["sort_key"])

        for e in timed:
            flag    = FLAGS.get(e["currency"], "")
            emoji   = IMPACT_EMOJI.get(e["impact"], "⚪")
            line    = f"{emoji} `{e['time_display']}` {flag} {e['currency']} — {e['title']}"

            parts = []
            if e["previous"]:
                parts.append(f"Prev {e['previous']}")
            if e["forecast"]:
                parts.append(f"Fcst {e['forecast']}")
            if parts:
                line += "  ·  " + "  ·  ".join(parts)

            lines.append(line)

    msg = "\n".join(lines)

    if len(msg) > 1990:
        msg = msg[:1980] + "\n…_(truncated)_"
    return msg


# ───────────────────────── STATE + DISCORD I/O ───────────────────────────────

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def post_new_message(content):
    payload = {
        "username":   BOT_USERNAME,
        "avatar_url": BOT_AVATAR,
        "content":    content,
    }
    url = WEBHOOK_URL + ("&" if "?" in WEBHOOK_URL else "?") + "wait=true"

    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent":   USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg_id = data.get("id")
            log(f"  ✓ Posted (id={msg_id})")
            return msg_id
    except (URLError, HTTPError) as e:
        log(f"  ✗ Failed to post: {e}")
        return None


# ───────────────────────── MAIN ──────────────────────────────────────────────

def main():
    if not WEBHOOK_URL:
        log("ERROR: DISCORD_WEBHOOK_URL not set.")
        sys.exit(1)

    xml_data = fetch_calendar()
    events   = parse_calendar(xml_data)
    log(f"Parsed {len(events)} eligible events")

    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    week_days = [monday + timedelta(days=i) for i in DIGEST_WEEKDAYS]
    current_week = monday.isoformat()

    by_date = {}
    for e in events:
        by_date.setdefault(e["date"], []).append(e)

    state = load_state()
    saved_week = state.get("week_start")

    # Skip if we've already posted for this week (prevents duplicate posts
    # if the workflow is manually re-run mid-week)
    if saved_week == current_week:
        log(f"Week {current_week} already posted. Skipping.")
        log("(To force a repost, delete digest_state.json from the repo and re-run.)")
        return

    log(f"Posting briefing for week starting {current_week}")
    posted_ids = []
    for idx, day in enumerate(week_days):
        day_events = by_date.get(day, [])
        content = build_day_message(day, day_events, is_first_day=(idx == 0))
        log(f"Posting {day.isoformat()}")
        msg_id = post_new_message(content)
        if msg_id:
            posted_ids.append(msg_id)
        time.sleep(0.5)  # avoid Discord rate limits

    save_state({
        "week_start": current_week,
        "posted":     posted_ids,
    })
    log(f"State saved: {len(posted_ids)} day messages posted")


if __name__ == "__main__":
    main()
