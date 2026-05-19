"""
═══════════════════════════════════════════════════════════════════════════════
FIBER TRADES — LIVING WEEKLY CALENDAR DIGEST (one message per day)
═══════════════════════════════════════════════════════════════════════════════

Sunday 18:00 UTC: posts one Discord message per weekday (Mon–Fri), each
containing that day's events with holidays at the top. Saves all message IDs.

Every 5 minutes Mon–Fri: rebuilds each day's content with `actual` values
filled in as events release, and edits each message in place by ID.

State file: digest_state.json
  {
    "week_start": "2026-05-18",
    "messages": {
      "2026-05-18": "1234567890",
      "2026-05-19": "1234567891",
      ...
    }
  }

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

# Days to include in the digest. Weekend usually has nothing, so Mon–Fri only.
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
        actual   = (ev.findtext("actual") or "").strip()

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
                london_hour, london_minute = eastern_to_london(
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
            "actual":       actual,
        })

    return events


def eastern_to_london(year, month, day, hour, minute):
    dt_naive = datetime(year, month, day, hour, minute)
    us_dst = second_sunday(year, 3) <= dt_naive.date() < first_sunday(year, 11)
    uk_dst = last_sunday(year, 3) <= dt_naive.date() < last_sunday(year, 10)
    et_offset = -4 if us_dst else -5
    uk_offset =  1 if uk_dst else  0
    delta_hours = uk_offset - et_offset
    dt_london = dt_naive + timedelta(hours=delta_hours)
    return dt_london.hour, dt_london.minute


def second_sunday(year, month):
    d = datetime(year, month, 1)
    first_sun_day = (6 - d.weekday()) % 7 + 1
    return datetime(year, month, first_sun_day + 7).date()


def first_sunday(year, month):
    d = datetime(year, month, 1)
    first_sun_day = (6 - d.weekday()) % 7 + 1
    return datetime(year, month, first_sun_day).date()


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
        lines.append("🗓️ **Week Ahead — Calendar**")
        lines.append("")
    lines.append(f"━━━ **{date_str}** ━━━")

    if not events_for_day:
        lines.append("_No scheduled events._")
    else:
        # Split holidays/all-day from timed
        holidays = [e for e in events_for_day
                    if e["impact"] == "Holiday"
                    or e["time_display"] == "All Day"
                    or "holiday" in e["title"].lower()]
        timed    = [e for e in events_for_day if e not in holidays]

        # Holidays first
        for h in holidays:
            flag = FLAGS.get(h["currency"], "")
            lines.append(f"🏖️ {flag} {h['currency']} — {h['title']}")

        # Sort timed by London time
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
            if e["actual"]:
                parts.append(f"**Act {e['actual']}** ✅")
            if parts:
                line += "  ·  " + "  ·  ".join(parts)

            lines.append(line)

    msg = "\n".join(lines)

    # Safety truncation — extremely unlikely with one day per message
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
    """POST a new message via the webhook. Returns the message ID."""
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


def edit_message(message_id, content):
    """PATCH an existing webhook message by ID."""
    url = f"{WEBHOOK_URL}/messages/{message_id}"
    payload = {"content": content}

    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent":   USER_AGENT,
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log(f"  ✓ Edited (id={message_id})")
            return True
    except HTTPError as e:
        if e.code == 404:
            log(f"  ✗ Message {message_id} not found")
            return False
        log(f"  ✗ HTTP {e.code} editing: {e}")
        return False
    except URLError as e:
        log(f"  ✗ Failed to edit: {e}")
        return False


# ───────────────────────── MAIN ──────────────────────────────────────────────

def week_start_for(date):
    """Returns the Monday of the week containing this date (ISO string)."""
    return (date - timedelta(days=date.weekday())).isoformat()


def main():
    if not WEBHOOK_URL:
        log("ERROR: DISCORD_WEBHOOK_URL not set.")
        sys.exit(1)

    xml_data = fetch_calendar()
    events   = parse_calendar(xml_data)
    log(f"Parsed {len(events)} eligible events")

    # Compute this week's Monday → Friday
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    week_days = [monday + timedelta(days=i) for i in DIGEST_WEEKDAYS]
    current_week = monday.isoformat()

    # Group events by date
    by_date = {}
    for e in events:
        by_date.setdefault(e["date"], []).append(e)

    # Load state
    state = load_state()
    saved_week = state.get("week_start")
    saved_messages = state.get("messages", {})

    # If a new week, clear out old message IDs and post all five fresh
    is_new_week = (saved_week != current_week)
    if is_new_week:
        log(f"New week ({current_week}) — posting all days fresh")
        saved_messages = {}

    new_messages = {}
    for idx, day in enumerate(week_days):
        day_key = day.isoformat()
        day_events = by_date.get(day, [])
        content = build_day_message(day, day_events, is_first_day=(idx == 0))

        existing_id = saved_messages.get(day_key)
        if existing_id and not is_new_week:
            # Edit in place
            log(f"Editing {day_key} (id={existing_id})")
            success = edit_message(existing_id, content)
            if success:
                new_messages[day_key] = existing_id
            else:
                # Edit failed — post fresh
                log(f"Reposting {day_key}")
                msg_id = post_new_message(content)
                if msg_id:
                    new_messages[day_key] = msg_id
        else:
            # Post fresh
            log(f"Posting {day_key}")
            msg_id = post_new_message(content)
            if msg_id:
                new_messages[day_key] = msg_id
            # Small delay between posts to avoid Discord rate limits
            time.sleep(0.5)

    save_state({
        "week_start": current_week,
        "messages":   new_messages,
    })
    log(f"State saved: {len(new_messages)} day messages tracked")


if __name__ == "__main__":
    main()
