"""
═══════════════════════════════════════════════════════════════════════════════
FIBER TRADES — ECONOMIC CALENDAR ALERT BOT
═══════════════════════════════════════════════════════════════════════════════

Runs every 5 minutes via GitHub Actions. Fetches the week's economic calendar
from the Faireconomy mirror of Forex Factory, finds events scheduled to release
in the next 5–10 minutes, filters to HIGH impact + EUR/USD currencies, and
posts an alert to Discord via webhook.

How it knows what's been alerted: it writes a small state file `alerted.txt`
in the repo with event IDs it has already posted, so re-runs don't double-post.
GitHub Actions commits the updated state file back to the repo automatically.

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# Discord webhook — set as a GitHub Actions secret named DISCORD_WEBHOOK_URL.
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# Calendar source: faireconomy.media mirror of Forex Factory (public, stable).
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

# Only post events with these impacts. "High" is recommended for signal-only.
IMPACT_FILTER = {"High"}

# Only post events for these currencies.
CURRENCY_FILTER = {"USD", "EUR", "GBP"}  # GBP included since London session

# How far ahead to look for events that should trigger an alert.
ALERT_WINDOW_MIN = 0      # events starting at or after now
ALERT_WINDOW_MAX = 10     # events starting within the next 10 minutes

# State file — tracks which events we've already alerted on.
STATE_FILE = "alerted.json"

# Username + avatar that the webhook posts as.
BOT_USERNAME = "Fiber Trades · Calendar"
BOT_AVATAR   = "https://cdn.discordapp.com/embed/avatars/0.png"

# ─────────────────────────────────────────────────────────────────────────────


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}")


def fetch_calendar():
    """Download the calendar XML."""
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
            data = resp.read()
        log(f"  → {len(data)} bytes")
        return data
    except URLError as e:
        log(f"  ✗ Failed: {e}")
        sys.exit(1)


def parse_calendar(xml_bytes):
    """Parse the Forex Factory calendar XML into a list of events."""
    events = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log(f"  ✗ XML parse error: {e}")
        return events

    # The schema is a flat list of <event> elements with child fields.
    for ev in root.findall(".//event"):
        title    = (ev.findtext("title") or "").strip()
        country  = (ev.findtext("country") or "").strip()
        date_str = (ev.findtext("date") or "").strip()
        time_str = (ev.findtext("time") or "").strip()
        impact   = (ev.findtext("impact") or "").strip()
        forecast = (ev.findtext("forecast") or "").strip()
        previous = (ev.findtext("previous") or "").strip()

        # Build a deterministic event ID for deduping
        event_id = f"{date_str}|{time_str}|{country}|{title}"

        # Parse the date/time into UTC. The XML uses US/Eastern by convention.
        dt_utc = parse_event_datetime(date_str, time_str)
        if dt_utc is None:
            continue

        events.append({
            "id":       event_id,
            "title":    title,
            "currency": country,  # FF "country" field is actually the currency code
            "impact":   impact,
            "forecast": forecast,
            "previous": previous,
            "dt_utc":   dt_utc,
        })
    return events


def parse_event_datetime(date_str, time_str):
    """
    Forex Factory XML dates look like '05-19-2026' (MM-DD-YYYY) and times like
    '8:30am' or '10:00pm' or 'All Day' or 'Tentative'. They're in US Eastern.
    Returns UTC datetime, or None if untimed.
    """
    if not date_str:
        return None
    if not time_str or time_str.lower() in ("all day", "tentative"):
        return None

    # Parse date: MM-DD-YYYY
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", date_str)
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))

    # Parse time: e.g. '8:30am', '10:00pm'
    t = re.match(r"(\d{1,2}):(\d{2})(am|pm)", time_str.lower())
    if not t:
        return None
    hour, minute, ampm = int(t.group(1)), int(t.group(2)), t.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    # Treat as US Eastern. FF observes DST. Rough approximation: EDT = UTC-4
    # March-Nov, EST = UTC-5 Nov-March. Close enough for ±1 hour bot precision.
    # The 5-min granularity bot tolerates DST mis-handling without failing.
    dt_naive = datetime(year, month, day, hour, minute)
    # DST: roughly second Sunday of March to first Sunday of November
    dst_start = second_sunday(year, 3)
    dst_end   = first_sunday(year, 11)
    if dst_start <= dt_naive.date() < dst_end:
        offset_hours = -4   # EDT
    else:
        offset_hours = -5   # EST
    dt_utc = dt_naive - timedelta(hours=offset_hours)  # subtracting a negative adds
    return dt_utc.replace(tzinfo=timezone.utc)


def second_sunday(year, month):
    """Date of the 2nd Sunday in the given month."""
    d = datetime(year, month, 1)
    # weekday(): Monday=0, Sunday=6
    first_sunday_day = (6 - d.weekday()) % 7 + 1
    return datetime(year, month, first_sunday_day + 7).date()


def first_sunday(year, month):
    """Date of the 1st Sunday in the given month."""
    d = datetime(year, month, 1)
    first_sunday_day = (6 - d.weekday()) % 7 + 1
    return datetime(year, month, first_sunday_day).date()


def load_alerted():
    """Load the set of event IDs we've already posted."""
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("alerted", []))
    except (OSError, json.JSONDecodeError):
        return set()


def save_alerted(alerted):
    """Save the updated set, dropping anything older than 7 days to keep it small."""
    with open(STATE_FILE, "w") as f:
        json.dump({"alerted": sorted(alerted), "updated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)


def post_to_discord(event):
    """POST a single event alert to the Discord webhook."""
    if not WEBHOOK_URL:
        log("  ✗ No webhook URL set, skipping post")
        return False

    impact_emoji = "🔴" if event["impact"] == "High" else "🟡" if event["impact"] == "Medium" else "🟢"

    # Convert UTC to London time for the message
    london_time = utc_to_london(event["dt_utc"])
    time_str = london_time.strftime("%H:%M")

    # Build the message lines
    lines = []
    lines.append(f"{impact_emoji} **{event['currency']} · {event['title']}**")
    lines.append(f"⏰ {time_str} London  ·  {event['impact']}-impact")
    if event["forecast"] or event["previous"]:
        fc = f"Forecast: {event['forecast']}" if event['forecast'] else ""
        pr = f"Prior: {event['previous']}"    if event['previous'] else ""
        meta = "  ·  ".join([s for s in [fc, pr] if s])
        lines.append(meta)
    body = "\n".join(lines)

    payload = {
        "username":   BOT_USERNAME,
        "avatar_url": BOT_AVATAR,
        "content":    body,
    }

    import urllib.request
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log(f"  ✓ Posted: {event['currency']} {event['title']} ({resp.status})")
            return True
    except URLError as e:
        log(f"  ✗ Failed to post {event['title']}: {e}")
        return False


def utc_to_london(dt_utc):
    """Convert UTC datetime to London local time (handles BST/GMT)."""
    year = dt_utc.year
    # BST: last Sunday of March → last Sunday of October (UTC+1)
    bst_start = last_sunday(year, 3)
    bst_end   = last_sunday(year, 10)
    # We compare just the date (BST switches at 01:00 UTC; close enough for alerts)
    if bst_start <= dt_utc.date() < bst_end:
        return dt_utc + timedelta(hours=1)
    return dt_utc


def last_sunday(year, month):
    """Date of the last Sunday in the given month."""
    from calendar import monthrange
    last_day = monthrange(year, month)[1]
    d = datetime(year, month, last_day)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d.date()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not WEBHOOK_URL:
        log("ERROR: DISCORD_WEBHOOK_URL environment variable not set.")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=ALERT_WINDOW_MIN)
    window_end   = now + timedelta(minutes=ALERT_WINDOW_MAX)
    log(f"Now: {now.isoformat()}")
    log(f"Alert window: {window_start.isoformat()} → {window_end.isoformat()}")

    xml_data = fetch_calendar()
    events = parse_calendar(xml_data)
    log(f"Parsed {len(events)} events from calendar")

    # Filter to upcoming, high-impact, relevant currencies
    eligible = [
        e for e in events
        if e["impact"] in IMPACT_FILTER
        and e["currency"] in CURRENCY_FILTER
        and window_start <= e["dt_utc"] <= window_end
    ]
    log(f"Eligible events in alert window: {len(eligible)}")

    if not eligible:
        log("Nothing to alert. Exiting.")
        # Save empty state file if missing so commits work cleanly
        if not os.path.exists(STATE_FILE):
            save_alerted(set())
        return

    alerted = load_alerted()
    new_alerts = [e for e in eligible if e["id"] not in alerted]
    log(f"New alerts to post: {len(new_alerts)} (skipped {len(eligible) - len(new_alerts)} already-posted)")

    for ev in new_alerts:
        if post_to_discord(ev):
            alerted.add(ev["id"])

    # Prune state: drop any IDs whose date is older than 7 days ago.
    pruned = set()
    cutoff = (now - timedelta(days=7)).strftime("%m-%d-%Y")
    for eid in alerted:
        # eid format: date|time|country|title  — keep if date >= cutoff
        date_part = eid.split("|")[0]
        if date_part >= cutoff or date_part == "":
            pruned.add(eid)
    save_alerted(pruned)
    log(f"State saved: {len(pruned)} active IDs")


if __name__ == "__main__":
    main()
