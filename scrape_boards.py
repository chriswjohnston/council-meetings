#!/usr/bin/env python3
"""
scrape_boards.py — council.chriswjohnston.ca
=============================================
Scrapes board/committee meeting documents AND community events from the
Township of Nipissing website.

PAGE STRUCTURE NOTES
--------------------
All three boards format their pages differently. This scraper handles all of them:

MUSEUM (museum-services-and-information/):
  <p> blocks with <br>-separated lines. No year headings.
  Date text + links inline per line. Blank <p> separates years.
    March 4, 2026 <a>(Agenda)</a> <a>(Minutes)</a> <a>(Agenda Package)</a><br>
    April 1, 2026 <a>(Agenda)</a> (Minutes) – Cancelled<br>

RECREATION (recreation/):
  <h2> year headings ("2026 Meeting Dates...") above <p> blocks.
  ALL dates + links run together inline with NO <br> separators.
  CANCELLED appears as plain text after the links for that date.
    <h2>2026 Meeting Dates...</h2>
    <p>January 19, 2026 <a>(Agenda)</a> <a>(Minutes)</a> February 2, 2026 <a>(Agenda)</a>...</p>

CEMETERY (cemetery/):
  <ul><li> items, sometimes 2+ dates on one <li> with no separator.
    <li>July 25, 2025 <a>(Agenda)</a> <a>(Minutes)</a> February 10, 2025 <a>(Agenda)</a>...</li>
    <li>January 10, 2024 <a>(Agenda)</a> Postponed</li>

SOLUTION: A single universal parser that walks children in DOM order.
Every date encountered in any text node starts a new meeting bucket.
<a> tags are collected into the current bucket until the next date.
Works regardless of <br>, <li>, or inline formatting.

COMMUNITY CALENDAR
------------------
Minutes PDFs contain hidden events like:
  "Easter Event – April 4th, 2026 from 12:00-2:00 p.m., Ages 12 and under."
  "Soccer – May 7th – June 25th from 6:00 – 7:00 p.m."
  "Trivia – March 21st, 2026 starting at 7:00 p.m."

We use Claude Haiku to extract structured events from minutes text.
Events are stored in calendar.json and rendered as a community calendar page.

FUTURE MEETINGS
---------------
Dates in the future are shown with a distinct "Upcoming" style so residents
know when the next meeting is, not just what happened in the past.
"""

import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

# ── Configuration ─────────────────────────────────────────────────────────────

BOARDS = [
    {
        "id": "recreation",
        "name": "Recreation Committee",
        "url": "https://nipissingtownship.com/services/recreation/",
        "bylaw": "2023-09",
        "bylaw_url": "https://nipissingtownship.com/municipal-information/by-laws/2023-09-recreation-committee/",
        "description": "Management of recreational programming and the Community Centre at 2381 Highway 654.",
        "format": "inline",   # all dates run together inline per year-heading block
    },
    {
        "id": "museum",
        "name": "Museum Board",
        "url": "https://nipissingtownship.com/services/museum-services-and-information/",
        "bylaw": "2023-10",
        "bylaw_url": "https://nipissingtownship.com/municipal-information/by-laws/2023-10-museum-board/",
        "description": "Preservation and display of the history and heritage of Nipissing Township.",
        "format": "br",       # <br>-separated lines inside <p> blocks
    },
    {
        "id": "cemetery",
        "name": "Cemetery Committee",
        "url": "https://nipissingtownship.com/services/cemetery/",
        "bylaw": "2023-11",
        "bylaw_url": None,
        "description": "Administration of the Nipissing Union Cemetery, Commanda Cemetery and St. John's Alsace Cemetery.",
        "format": "li",       # <ul><li> items
    },
]

OUTPUT_DIR = Path("boards")
HEADERS = {"User-Agent": "council-archive-bot/1.0 (chriswjohnston.ca)"}
AI_API = "https://api.anthropic.com/v1/messages"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

TODAY = date.today()

DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),?\s+(\d{4})"
)


# ── Universal DOM-aware parser ─────────────────────────────────────────────────

def classify_link(link: dict) -> str | None:
    combined = (link["text"] + " " + link["href"]).lower()
    if "package" in combined:  return "package"
    if "minute" in combined:   return "minutes"
    if "agenda" in combined:   return "agenda"
    return None

def make_absolute(href: str, base: str = "https://nipissingtownship.com") -> str:
    if href.startswith("http"): return href
    return base + (href if href.startswith("/") else "/" + href)

def extract_meetings_from_node(node: Tag) -> list[dict]:
    """
    Walk any element's children in DOM order.
    Every date found in a text node starts a new meeting bucket.
    <a> tags are bucketed under the most recent date seen.

    Handles all three Township page formats:
    - <br>-separated (museum)
    - inline run-together (recreation)
    - <li> items with multiple dates (cemetery)
    """
    meetings: list[dict] = []
    current: dict | None = None

    def flush():
        nonlocal current
        if current:
            meetings.append(dict(current))
        current = None

    def start_meeting(month: str, day: str, year_str: str):
        nonlocal current
        flush()
        year = int(year_str)
        date_str = f"{year}-{datetime.strptime(month, '%B').month:02d}-{int(day):02d}"
        current = {
            "date_str":     date_str,
            "display_date": f"{month} {int(day)}, {year}",
            "year":         year,
            "is_future":    date_str > TODAY.isoformat(),
            "cancelled":    False,
            "rescheduled":  False,
            "postponed":    False,
            "links":        [],
        }

    def walk(node):
        nonlocal current
        if isinstance(node, NavigableString):
            text = str(node)
            last_end = 0
            for dm in DATE_RE.finditer(text):
                # Check text between previous match and this one for flags
                between = text[last_end:dm.start()].lower()
                if current:
                    if "cancel"    in between: current["cancelled"]   = True
                    if "reschedul" in between: current["rescheduled"] = True
                    if "postpon"   in between: current["postponed"]   = True
                start_meeting(dm.group(1), dm.group(2), dm.group(3))
                last_end = dm.end()
            # Flags after the last date on this text node
            tail = text[last_end:].lower()
            if current:
                if "cancel"    in tail: current["cancelled"]   = True
                if "reschedul" in tail: current["rescheduled"] = True
                if "postpon"   in tail: current["postponed"]   = True

        elif isinstance(node, Tag):
            if node.name == "br":
                return   # <br> is a visual separator; don't flush — dates may continue
            if node.name == "a":
                href  = node.get("href", "").strip()
                label = node.get_text(strip=True).lower()
                if href and current:
                    current["links"].append({"text": label, "href": make_absolute(href)})
                return
            # Recurse into any other tag
            for child in node.children:
                walk(child)

    for child in node.children:
        walk(child)
    flush()
    return meetings


def scrape_board(board: dict) -> list[dict]:
    print(f"  Scraping {board['name']} ({board['format']})...")
    r = requests.get(board["url"], headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    content = (
        soup.find("div", class_="entry-content")
        or soup.find("main")
        or soup.body
    )

    all_meetings: list[dict] = []
    seen_dates: set[str] = set()

    # Parse every block-level element that contains at least one date
    for elem in content.find_all(["p", "li", "div"]):
        if not DATE_RE.search(elem.get_text()):
            continue
        for m in extract_meetings_from_node(elem):
            if m["date_str"] in seen_dates:
                continue
            seen_dates.add(m["date_str"])

            # Assign typed link URLs
            agenda_url = minutes_url = package_url = None
            for link in m["links"]:
                kind = classify_link(link)
                if kind == "agenda"  and not agenda_url:  agenda_url  = link["href"]
                if kind == "minutes" and not minutes_url: minutes_url = link["href"]
                if kind == "package" and not package_url: package_url = link["href"]

            all_meetings.append({
                "date":         m["date_str"],
                "display_date": m["display_date"],
                "year":         m["year"],
                "is_future":    m["is_future"],
                "cancelled":    m["cancelled"],
                "rescheduled":  m["rescheduled"],
                "postponed":    m["postponed"],
                "agenda_url":   agenda_url,
                "minutes_url":  minutes_url,
                "package_url":  package_url,
                "board_id":     board["id"],
                "board_name":   board["name"],
                "summary":      None,
                "events":       [],   # community events extracted from this meeting's minutes
            })

    all_meetings.sort(key=lambda x: x["date"], reverse=True)
    future  = sum(1 for m in all_meetings if m["is_future"])
    past    = sum(1 for m in all_meetings if not m["is_future"])
    w_min   = sum(1 for m in all_meetings if m["minutes_url"])
    print(f"    {past} past + {future} upcoming | {w_min} with minutes")
    return all_meetings


# ── AI: meeting summaries + event extraction ──────────────────────────────────

def fetch_minutes_text(url: str) -> str | None:
    """Download a minutes PDF or HTML page and return its text content."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        text = r.content.decode("latin-1", errors="ignore")
        text = re.sub(r"[^\x20-\x7E\n\r\t]", " ", text)
        text = re.sub(r"\s{3,}", "\n", text)[:8000]
        return text if len(text) > 200 else None
    except Exception:
        return None


def ai_call(prompt: str, max_tokens: int = 400) -> str | None:
    if not ANTHROPIC_KEY:
        return None
    try:
        r = requests.post(
            AI_API,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"    AI error: {e}")
    return None


def generate_summary(meeting: dict, text: str) -> str | None:
    return ai_call(
        f"These are minutes from a {meeting['board_name']} meeting on "
        f"{meeting['display_date']} in Nipissing Township, Ontario.\n\n"
        f"{text}\n\n"
        "Write 2-3 plain-language sentences summarising what was discussed and decided. "
        "Focus on budgets, programs, facilities, votes. Skip procedural items. "
        "Write for a resident who just wants to know what happened.",
        max_tokens=300,
    )


def extract_events(meeting: dict, text: str) -> list[dict]:
    """
    Ask Claude to extract community events mentioned in the minutes.
    Returns a list of structured event dicts, or [] if none found.
    """
    result = ai_call(
        f"These are minutes from a {meeting['board_name']} meeting on "
        f"{meeting['display_date']} in Nipissing Township, Ontario.\n\n"
        f"{text}\n\n"
        "List any upcoming community events mentioned (programs, socials, fundraisers, "
        "registered activities, public events). For each event output ONLY a JSON array "
        "with this exact structure, no markdown, no prose:\n"
        '[{"title":"Event Name","date":"Month Day, Year or date range","time":"HH:MM AM–PM or empty","'
        'location":"place or empty","details":"1 sentence description","registration":"info or empty"}]\n'
        "If no events are mentioned, output exactly: []",
        max_tokens=600,
    )
    if not result:
        return []
    try:
        # Strip any accidental markdown fences
        clean = re.sub(r"```[a-z]*", "", result).strip()
        events = json.loads(clean)
        if isinstance(events, list):
            # Tag each event with its source meeting
            for ev in events:
                ev["source_board"]   = meeting["board_name"]
                ev["source_date"]    = meeting["display_date"]
                ev["source_minutes"] = meeting.get("minutes_url", "")
            return events
    except Exception as e:
        print(f"    Event parse error: {e} | raw: {result[:100]}")
    return []


def process_ai(meetings: list[dict], max_summaries: int = 8) -> list[dict]:
    """Generate summaries and extract events for recent past meetings."""
    processed = 0
    for m in meetings:
        if m["is_future"] or not m["minutes_url"]:
            continue
        if processed >= max_summaries:
            break
        if m.get("summary"):  # already done
            processed += 1
            continue

        print(f"    AI: {m['display_date']}...")
        text = fetch_minutes_text(m["minutes_url"])
        if not text:
            continue

        m["summary"] = generate_summary(m, text)
        m["events"]  = extract_events(m, text)
        processed += 1
        time.sleep(0.4)

    return meetings


# ── Community calendar builder ─────────────────────────────────────────────────

def build_calendar_html(all_boards_data: list[dict]) -> str:
    """Collect all events from all boards and render a community calendar page."""
    all_events: list[dict] = []
    for bd in all_boards_data:
        for m in bd["meetings"]:
            for ev in m.get("events", []):
                all_events.append(ev)

    if not all_events:
        event_html = '<p class="no-events">No upcoming events found yet. Check back after the next meeting minutes are published.</p>'
    else:
        event_html = ""
        for ev in all_events:
            reg = f'<div class="ev-reg">📋 {ev["registration"]}</div>' if ev.get("registration") else ""
            loc = f' · {ev["location"]}' if ev.get("location") else ""
            time_str = f' · {ev["time"]}' if ev.get("time") else ""
            src_url = ev.get("source_minutes", "")
            src_link = f'<a href="{src_url}" target="_blank">Minutes</a>' if src_url else ev["source_board"]
            event_html += f"""<div class="event-card">
  <div class="ev-when">{ev['date']}{time_str}{loc}</div>
  <div class="ev-title">{ev['title']}</div>
  <div class="ev-desc">{ev.get('details','')}</div>
  {reg}
  <div class="ev-source">Source: {ev['source_board']} · {ev['source_date']} · {src_link}</div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Community Events — council.chriswjohnston.ca</title>
<style>
:root{{--green:#2C4A3E;--pine:#3D6B5E;--pine-lt:#e8f0eb;--warm:#E8C98A;--rust:#C06830;
  --gold:#b8922a;--gold-lt:#fdf6e3;--bg:#FAF7F0;--white:#fff;--rule:#d8d0c8;--body:#3a3a3a;--muted:#6e6e6e;--ink:#1c1c1c}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--body);line-height:1.55}}
header{{background:var(--green);color:#fff;padding:1.5rem 2rem}}
.bc{{font-size:.72rem;margin-bottom:.5rem}}
.bc a{{color:var(--warm);text-decoration:none;opacity:.8}}
header h1{{font-size:1.4rem;font-weight:700;margin-bottom:.25rem}}
header p{{font-size:.86rem;color:rgba(255,255,255,.7);max-width:600px}}
.main{{max-width:860px;margin:0 auto;padding:1.5rem 2rem 4rem}}
.notice{{background:var(--gold-lt);border:1px solid #e0c87a;border-left:4px solid var(--gold);
  border-radius:6px;padding:.9rem 1.2rem;margin-bottom:1.5rem;font-size:.84rem;color:var(--body)}}
.event-card{{background:var(--white);border:1px solid var(--rule);border-left:4px solid var(--rust);
  border-radius:6px;padding:1rem 1.25rem;margin-bottom:.75rem}}
.ev-when{{font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:var(--rust);margin-bottom:.3rem}}
.ev-title{{font-size:1rem;font-weight:700;color:var(--ink);margin-bottom:.35rem}}
.ev-desc{{font-size:.86rem;line-height:1.62;color:var(--body);margin-bottom:.4rem}}
.ev-reg{{font-size:.82rem;background:var(--pine-lt);color:var(--pine);border-radius:4px;
  padding:3px 8px;display:inline-block;margin-bottom:.4rem}}
.ev-source{{font-size:.72rem;color:var(--muted)}}
.ev-source a{{color:var(--pine)}}
.no-events{{color:var(--muted);font-size:.9rem;padding:1rem 0}}
footer{{background:#1E2B2A;color:rgba(255,255,255,.35);text-align:center;padding:1.5rem;font-size:.78rem}}
footer a{{color:var(--warm);text-decoration:none}}
@media(max-width:600px){{header,.main{{padding-left:1rem;padding-right:1rem}}}}
</style>
</head>
<body>
<header>
  <div class="bc"><a href="/">← Council Archive</a></div>
  <h1>Community Events</h1>
  <p>Events extracted from board and committee meeting minutes. Updated automatically when new minutes are published.</p>
</header>
<div class="main">
  <div class="notice">
    These events are extracted by AI from meeting minutes and may not be complete. 
    Always confirm details with the <a href="https://nipissingtownship.com" target="_blank">Township of Nipissing</a>.
  </div>
  {event_html}
</div>
<footer>
  <a href="/">council.chriswjohnston.ca</a> · Updated {datetime.now().strftime('%B %d, %Y')}
</footer>
</body>
</html>"""


# ── Board page HTML ───────────────────────────────────────────────────────────

def status_for(m: dict) -> tuple[str, str]:
    """Return (css_class, label) for a meeting."""
    if m["is_future"]:
        if m["cancelled"]:    return "future-cancelled", "Cancelled"
        if m["postponed"]:    return "future-postponed", "Postponed"
        return "upcoming", "Upcoming"
    if m["cancelled"]:        return "cancelled", "Cancelled"
    if m["postponed"]:        return "postponed", "Postponed"
    if m["rescheduled"]:      return "rescheduled", "Rescheduled"
    if m["minutes_url"]:      return "has-minutes", "Minutes Available"
    if m["agenda_url"]:       return "agenda-only", "Agenda Only"
    return "past-no-docs", "No Documents"


def build_board_html(board: dict, meetings: list[dict]) -> str:
    years = sorted({m["year"] for m in meetings}, reverse=True)
    by_year: dict[int, list] = {}
    for m in meetings:
        by_year.setdefault(m["year"], []).append(m)

    rows = ""
    for year in years:
        rows += f'<div class="year-group" id="y{year}"><div class="year-label">{year}</div>\n'
        for m in by_year[year]:
            sc, st = status_for(m)
            lnks = ""
            if m.get("agenda_url"):
                lnks += f'<a href="{m["agenda_url"]}" target="_blank" class="dl agenda">Agenda</a>'
            if m.get("minutes_url"):
                lnks += f'<a href="{m["minutes_url"]}" target="_blank" class="dl minutes">Minutes</a>'
            if m.get("package_url"):
                lnks += f'<a href="{m["package_url"]}" target="_blank" class="dl package">Package</a>'

            summ = f'<div class="summary">{m["summary"]}</div>' if m.get("summary") else ""

            events_html = ""
            if m.get("events"):
                ev_items = "".join(
                    f'<li><strong>{e["title"]}</strong>'
                    f'{" · " + e["date"] if e.get("date") else ""}'
                    f'{" · " + e["time"] if e.get("time") else ""}'
                    f'{"<br><span class=ev-reg>"+e["registration"]+"</span>" if e.get("registration") else ""}'
                    f'</li>'
                    for e in m["events"]
                )
                events_html = f'<div class="events-block"><div class="events-label">Events mentioned</div><ul class="events-list">{ev_items}</ul></div>'

            # Future meetings get a dashed border and "Upcoming" badge
            future_attr = ' data-future="true"' if m["is_future"] else ""

            rows += f"""<div class="meeting-row {sc}"{future_attr}>
  <div class="mdate">{m["display_date"]}</div>
  <div class="minfo"><span class="badge {sc}">{st}</span>{summ}{events_html}</div>
  <div class="mlinks">{lnks}</div>
</div>\n"""
        rows += "</div>\n"

    total   = len(meetings)
    future  = sum(1 for m in meetings if m["is_future"])
    w_min   = sum(1 for m in meetings if m.get("minutes_url"))
    cancelled = sum(1 for m in meetings if m["cancelled"])
    bylaw_link = (
        f'<a href="{board["bylaw_url"]}" target="_blank">Read the governing by-law →</a>'
        if board.get("bylaw_url") else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{board['name']} — Nipissing Township Archive</title>
<style>
:root{{
  --green:#2C4A3E;--pine:#3D6B5E;--pine-lt:#e8f0eb;
  --gold:#b8922a;--gold-lt:#fdf6e3;--warm:#E8C98A;
  --rust:#C06830;--rust-lt:#fdf0e8;
  --sky:#A8D5E2;--sky-lt:#EAF5F8;
  --ink:#1c1c1c;--body:#3a3a3a;--muted:#6e6e6e;
  --rule:#d8d0c8;--bg:#FAF7F0;--white:#fff;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--body);line-height:1.55}}

header{{background:var(--green);color:#fff;padding:1.5rem 2rem}}
.bc{{font-size:.72rem;margin-bottom:.5rem}}
.bc a{{color:var(--warm);text-decoration:none;opacity:.8}}
.bc a:hover{{opacity:1}}
header h1{{font-size:1.4rem;font-weight:700;margin-bottom:.25rem}}
header p{{font-size:.86rem;color:rgba(255,255,255,.7);max-width:600px}}

.stats{{display:flex;gap:2rem;flex-wrap:wrap;padding:1rem 2rem;background:var(--white);border-bottom:1px solid var(--rule)}}
.stat strong{{font-size:1.3rem;display:block;color:var(--green);font-weight:700;font-family:Georgia,serif}}
.stat span{{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}}
.stat.upcoming-stat strong{{color:var(--sky);}}

.main{{max-width:960px;margin:0 auto;padding:1.5rem 2rem 4rem}}

/* Upcoming meetings banner */
.upcoming-banner{{
  background:var(--sky-lt);border:1px solid var(--sky);border-left:4px solid var(--sky);
  border-radius:6px;padding:.9rem 1.2rem;margin-bottom:1.5rem;font-size:.84rem;
}}
.upcoming-banner strong{{color:#1a5a6e}}

.bylaw-note{{background:var(--pine-lt);border:1px solid #b8d4c2;border-left:4px solid var(--pine);
  border-radius:6px;padding:.9rem 1.2rem;margin-bottom:1.5rem;font-size:.84rem}}
.bylaw-note a{{color:var(--pine)}}

.year-group{{margin-bottom:2.5rem}}
.year-label{{font-size:.65rem;font-weight:700;letter-spacing:.22em;text-transform:uppercase;
  color:var(--muted);padding:.4rem 0;border-bottom:2px solid var(--rule);margin-bottom:.5rem}}

/* ── Meeting rows ─────────────────────────── */
.meeting-row{{
  display:grid;grid-template-columns:150px 1fr auto;gap:1rem;
  align-items:start;padding:.7rem 0;border-bottom:1px solid var(--rule);
}}
.meeting-row:last-child{{border-bottom:none}}

/* FUTURE / UPCOMING — distinct dashed style, sky blue accent */
.meeting-row.upcoming{{
  background:var(--sky-lt);
  border:1px dashed var(--sky);
  border-radius:6px;
  padding:.7rem .9rem;
  margin-bottom:.4rem;
}}
.meeting-row.upcoming .mdate{{color:#1a5a6e;font-weight:700}}
.meeting-row.future-cancelled,.meeting-row.future-postponed{{
  background:#f8f8f8;border:1px dashed var(--rule);border-radius:6px;
  padding:.7rem .9rem;margin-bottom:.4rem;opacity:.7;
}}

.mdate{{font-size:.87rem;font-weight:600;color:var(--ink);padding-top:3px}}
.meeting-row.cancelled .mdate{{color:var(--muted);text-decoration:line-through}}
.meeting-row.postponed .mdate{{color:var(--muted);font-style:italic}}

/* Badges */
.badge{{display:inline-block;font-size:.6rem;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;padding:2px 8px;border-radius:3px;margin-bottom:4px}}
.badge.upcoming{{background:var(--sky);color:#fff}}
.badge.has-minutes{{background:var(--pine-lt);color:var(--pine)}}
.badge.agenda-only{{background:var(--gold-lt);color:var(--gold)}}
.badge.cancelled{{background:#f0f0f0;color:var(--muted)}}
.badge.postponed{{background:#f5f0ff;color:#6b52a0}}
.badge.rescheduled{{background:#fff3e0;color:#b86000}}
.badge.future-cancelled,.badge.future-postponed{{background:#f0f0f0;color:var(--muted)}}
.badge.past-no-docs{{background:var(--bg);color:var(--muted);border:1px solid var(--rule)}}

.summary{{font-size:.82rem;line-height:1.62;color:var(--body);margin-top:5px}}

/* Events extracted from minutes */
.events-block{{margin-top:.6rem;background:var(--rust-lt);border:1px solid #f0c8a8;
  border-left:3px solid var(--rust);border-radius:4px;padding:.5rem .75rem}}
.events-label{{font-size:.6rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--rust);margin-bottom:.35rem}}
.events-list{{list-style:none;display:flex;flex-direction:column;gap:.3rem;
  font-size:.82rem;line-height:1.55;color:var(--body)}}
.ev-reg{{font-size:.75rem;color:var(--pine);font-weight:600}}

.mlinks{{display:flex;flex-direction:column;gap:4px;min-width:82px}}
.dl{{font-size:.7rem;font-weight:700;letter-spacing:.04em;text-decoration:none;
  padding:3px 9px;border-radius:3px;text-align:center;white-space:nowrap}}
.dl.agenda{{background:var(--green);color:#fff}}
.dl.minutes{{background:var(--rust);color:#fff}}
.dl.package{{background:#555;color:#fff}}

footer{{background:#1E2B2A;color:rgba(255,255,255,.35);text-align:center;
  padding:1.5rem;font-size:.78rem;margin-top:2rem}}
footer a{{color:var(--warm);text-decoration:none}}

@media(max-width:600px){{
  .meeting-row{{grid-template-columns:1fr;gap:.4rem}}
  .mlinks{{flex-direction:row;flex-wrap:wrap}}
  header,.main,.stats{{padding-left:1rem;padding-right:1rem}}
}}
</style>
</head>
<body>

<header>
  <div class="bc">
    <a href="/">← Council Archive</a> /
    <a href="/boards/">Boards &amp; Committees</a>
  </div>
  <h1>{board['name']}</h1>
  <p>{board['description']}</p>
</header>

<div class="stats">
  <div class="stat upcoming-stat"><strong>{future}</strong><span>Upcoming</span></div>
  <div class="stat"><strong>{total - future}</strong><span>Past meetings</span></div>
  <div class="stat"><strong>{w_min}</strong><span>With minutes</span></div>
  <div class="stat"><strong>{cancelled}</strong><span>Cancelled</span></div>
</div>

<div class="main">

  {"<div class='upcoming-banner'><strong>Upcoming meetings are shown with a blue dashed border.</strong> Documents will appear here once published by the Township.</div>" if future > 0 else ""}

  <div class="bylaw-note">
    Governed by <strong>By-Law {board['bylaw']}</strong>. {bylaw_link}
    &nbsp;·&nbsp; Source: <a href="{board['url']}" target="_blank">Township of Nipissing</a>
    &nbsp;·&nbsp; Updated: {datetime.now().strftime('%B %d, %Y')}
  </div>

  {rows}
</div>

<footer>
  <a href="/">council.chriswjohnston.ca</a> ·
  <a href="/boards/">Boards &amp; Committees</a> ·
  Chris Johnston, Nipissing Township Council Candidate 2026
</footer>
</body>
</html>"""


def build_index_html(boards_data: list[dict]) -> str:
    cards = ""
    for b in boards_data:
        meetings = b["meetings"]
        next_m   = next((m for m in sorted(meetings, key=lambda x:x["date"]) if m["is_future"] and not m["cancelled"]), None)
        recent   = next((m for m in meetings if m.get("minutes_url")), None)
        next_str = f"Next: {next_m['display_date']}" if next_m else "No upcoming meetings listed"
        min_str  = f"Last minutes: {recent['display_date']}" if recent else "No minutes yet"
        future_count = sum(1 for m in meetings if m["is_future"])
        cards += f"""<a class="board-card" href="{b['id']}/index.html">
  <div class="board-name">{b['name']}</div>
  <div class="board-next">{next_str}</div>
  <div class="board-meta">{len(meetings)} meetings total · {future_count} upcoming · {min_str}</div>
</a>\n"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Boards &amp; Committees — council.chriswjohnston.ca</title>
<style>
:root{{--green:#2C4A3E;--pine:#3D6B5E;--sky:#A8D5E2;--warm:#E8C98A;
  --bg:#FAF7F0;--rule:#d8d0c8;--body:#3a3a3a;--muted:#6e6e6e;--ink:#1c1c1c}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--body);max-width:800px;margin:0 auto;padding:2rem}}
.back{{font-size:.8rem;margin-bottom:1.5rem}}
.back a{{color:var(--pine);text-decoration:none}}
h1{{font-size:1.5rem;color:var(--green);margin-bottom:.4rem}}
.intro{{font-size:.88rem;color:var(--muted);margin-bottom:2rem;line-height:1.65}}
.board-card{{display:block;background:#fff;border:1px solid var(--rule);border-left:4px solid var(--green);
  border-radius:6px;padding:1rem 1.25rem;margin-bottom:.75rem;text-decoration:none;
  transition:box-shadow .2s,border-left-color .2s}}
.board-card:hover{{box-shadow:0 3px 14px rgba(0,0,0,.1);border-left-color:var(--pine)}}
.board-name{{font-weight:700;color:var(--green);margin-bottom:.2rem;font-size:1rem}}
.board-next{{font-size:.86rem;font-weight:600;color:#1a5a6e;margin-bottom:.2rem}}
.board-meta{{font-size:.76rem;color:var(--muted)}}
.cal-link{{display:block;background:#fff;border:1px dashed var(--sky);border-left:4px solid var(--sky);
  border-radius:6px;padding:1rem 1.25rem;margin-bottom:.75rem;text-decoration:none;
  color:var(--ink);transition:box-shadow .2s}}
.cal-link:hover{{box-shadow:0 3px 14px rgba(0,0,0,.08)}}
.cal-link .cal-title{{font-weight:700;color:#1a5a6e;margin-bottom:.2rem}}
.cal-link .cal-desc{{font-size:.82rem;color:var(--muted)}}
footer{{font-size:.75rem;color:var(--muted);margin-top:2rem;text-align:center}}
</style>
</head>
<body>
<div class="back"><a href="/">← Back to Council Archive</a></div>
<h1>Boards &amp; Committees</h1>
<p class="intro">Meeting agendas and minutes for Nipissing Township boards and committees.
Updated automatically every day. Upcoming meetings shown with a blue border on each board's page.</p>

<a class="cal-link" href="calendar.html">
  <div class="cal-title">📅 Community Events Calendar</div>
  <div class="cal-desc">Events extracted from meeting minutes — programs, socials, fundraisers, registered activities</div>
</a>

{cards}
<footer>council.chriswjohnston.ca · Updated {datetime.now().strftime('%B %d, %Y')}</footer>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("Board & Committee Scraper — council.chriswjohnston.ca")
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M')} · Today: {TODAY}")
    print("=" * 55)

    OUTPUT_DIR.mkdir(exist_ok=True)
    boards_data = []

    for board in BOARDS:
        try:
            meetings = scrape_board(board)

            if ANTHROPIC_KEY:
                meetings = process_ai(meetings, max_summaries=8)

            board_dir = OUTPUT_DIR / board["id"]
            board_dir.mkdir(exist_ok=True)

            (board_dir / "data.json").write_text(
                json.dumps(
                    {"board": board, "meetings": meetings, "generated": datetime.now().isoformat()},
                    indent=2,
                )
            )
            (board_dir / "index.html").write_text(build_board_html(board, meetings))

            boards_data.append({"id": board["id"], "name": board["name"], "meetings": meetings})
            print(f"  ✓ {board['name']}")

        except Exception as e:
            print(f"  ✗ {board['name']}: {e}")
            import traceback; traceback.print_exc()

    (OUTPUT_DIR / "index.html").write_text(build_index_html(boards_data))
    (OUTPUT_DIR / "calendar.html").write_text(build_calendar_html(boards_data))
    print(f"\n✓ Done — {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
