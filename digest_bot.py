"""
═══════════════════════════════════════════════════════════════════════════════
FIBER TRADES — LIVING WEEKLY CALENDAR DIGEST
═══════════════════════════════════════════════════════════════════════════════

Sunday 18:00 UTC: posts a full week-ahead calendar to Discord (every event,
every day, with holidays). Saves the message ID.

Every 5 minutes Mon–Fri: rebuilds the digest with `actual` values for events
that have already released, and edits the original message in place via the
Discord webhook PATCH endpoint. The pinned/top message stays current all week.

State file: digest_state.json
  {
    "message_id": "1234...",
    "week_start": "2026-05-19"
  }

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import re
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

        # Parse date
        m = re.match(r"(\d{2})-(\d{2})-(\d{4})", date_str)
        if not m:
            continue
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        event_date = datetime(year, month, day).date()

        # Parse time — keep as raw display string, also produce a sortable key
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
                # Convert from US Eastern to London for display
                london_hour, london_minute = eastern_to_london(
                    year, month, day, hour, minute
                )
                time_display = f"{london_hour:02d}:{london_minute:02d}"
                sort_key = london_hour * 60 + london_minute
        elif time_str.lower() == "tentative":
            time_display = "Tentative"
            sort_key = 9999  # tentatives sort last in the day

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
    """Convert US Eastern wall-clock time to London wall-clock time.
    Rough DST handling — accurate enough for a digest."""
    dt_naive = datetime(year, month, day, hour, minute)
    # US DST: 2nd Sun Mar → 1st Sun Nov
    us_dst = second_sunday(year, 3) <= dt_naive.date() < first_sunday(year, 11)
    # UK DST: last Sun Mar → last Sun Oct
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

def build_digest(events):
    """Build the full multi-day digest string. Splits across days, holidays
    on top of each day, then timed events sorted by London time."""
    if not events:
        return "🗓️ **Week Ahead — Calendar**\n\nNo events scheduled this week."

    # Group by date
    by_date = {}
    for e in events:
        by_date.setdefault(e["date"], []).append(e)

    lines = ["🗓️ **Week Ahead — Calendar**", ""]
    for date in sorted(by_date.keys()):
        day_name = DAYS[date.weekday()]
        date_str = date.strftime(f"{day_name} %d %b")
        lines.append(f"━━━ **{date_str}** ━━━")

        day_events = by_date[date]
        # Split holidays/all-day from timed
        holidays = [e for e in day_events
                    if e["impact"] == "Holiday"
                    or e["time_display"] == "All Day"
                    or "holiday" in e["title"].lower()]
        timed    = [e for e in day_events if e not in holidays]

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

            # Build the data line: prior · forecast · actual
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
        lines.append("")

    lines.append(f"_Last updated: {datetime.now(timezone.utc).strftime('%a %d %b %H:%M UTC')}_")

    msg = "\n".join(lines)

    # Discord hard limit: 2000 chars per message. Truncate gracefully if hit.
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
    # ?wait=true makes Discord return the message object including the ID
    url = WEBHOOK_URL + ("&" if "?" in WEBHOOK_URL else "?") + "wait=true"

    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg_id = data.get("id")
            log(f"  ✓ Posted new digest (id={msg_id})")
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
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log(f"  ✓ Edited digest in place ({resp.status})")
            return True
    except HTTPError as e:
        if e.code == 404:
            log(f"  ✗ Message {message_id} not found — will repost")
            return False
        log(f"  ✗ HTTP {e.code} editing message: {e}")
        return False
    except URLError as e:
        log(f"  ✗ Failed to edit: {e}")
        return False


# ───────────────────────── MAIN ──────────────────────────────────────────────

def week_start_for(date):
    """Returns the Monday of the week containing this date."""
    return (date - timedelta(days=date.weekday())).isoformat()


def main():
    if not WEBHOOK_URL:
        log("ERROR: DISCORD_WEBHOOK_URL not set.")
        sys.exit(1)

    xml_data = fetch_calendar()
    events   = parse_calendar(xml_data)
    log(f"Parsed {len(events)} eligible events")

    content = build_digest(events)
    state   = load_state()

    today = datetime.now(timezone.utc).date()
    current_week = week_start_for(today)

    saved_id   = state.get("message_id")
    saved_week = state.get("week_start")

    # If state is missing OR we've rolled over to a new week, post fresh.
    if not saved_id or saved_week != current_week:
        log(f"New week ({current_week}) — posting fresh digest")
        new_id = post_new_message(content)
        if new_id:
            save_state({"message_id": new_id, "week_start": current_week})
        return

    # Otherwise edit in place
    log(f"Editing existing digest (id={saved_id}) for week {saved_week}")
    success = edit_message(saved_id, content)
    if not success:
        # Edit failed (probably deleted) — repost
        log("Edit failed, posting fresh")
        new_id = post_new_message(content)
        if new_id:
            save_state({"message_id": new_id, "week_start": current_week})


if __name__ == "__main__":
    main()
