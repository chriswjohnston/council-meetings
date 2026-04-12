#!/usr/bin/env python3
"""
scrape_boards.py — council.chriswjohnston.ca
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
        "format": "inline",
    },
    {
        "id": "museum",
        "name": "Museum Board",
        "url": "https://nipissingtownship.com/services/museum-services-and-information/",
        "bylaw": "2023-10",
        "bylaw_url": "https://nipissingtownship.com/municipal-information/by-laws/2023-10-museum-board/",
        "description": "Preservation and display of the history and heritage of Nipissing Township.",
        "format": "br",
    },
    {
        "id": "cemetery",
        "name": "Cemetery Committee",
        "url": "https://nipissingtownship.com/services/cemetery/",
        "bylaw": "2023-11",
        "bylaw_url": None,
        "description": "Administration of the Nipissing Union Cemetery, Commanda Cemetery and St. John's Alsace Cemetery.",
        "format": "li",
    },
]

DOCS_DIR   = Path("docs")
OUTPUT_DIR = Path("docs/boards")
HEADERS    = {"User-Agent": "council-archive-bot/1.0 (chriswjohnston.ca)"}
AI_API     = "https://api.anthropic.com/v1/messages"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

TODAY = date.today()

DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),?\s+(\d{4})"
)

# ── Error summary detection ───────────────────────────────────────────────────

_ERROR_PHRASES = [
    "I'm unable to read", "I apologize, but I'm unable",
    "I appreciate your request, but I'm unable",
    "I appreciate you sharing this", "unable to extract",
    "corrupted or", "compressed format", "unreadable",
    "improperly encoded", "I would need", "cannot decode",
    "cannot decompress", "doesn't convert", "not able to read",
    "unable to read the content",
]

def _is_error_summary(text):
    return bool(text and any(p in text for p in _ERROR_PHRASES))

def _inline_md(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         text)
    return text

def render_summary(raw):
    """Convert markdown summary to HTML. Returns None for errors/empty."""
    if not raw or not raw.strip() or _is_error_summary(raw):
        return None
    lines = raw.strip().split('\n')
    out, in_ul = [], False
    def close():
        nonlocal in_ul
        if in_ul:
            out.append('</ul>')
            in_ul = False
    for line in lines:
        s = line.strip()
        if not s:
            close(); continue
        if s in ('---', '***', '___'):
            close(); out.append('<hr>'); continue
        if s.startswith('### '): close(); out.append(f'<h5>{_inline_md(s[4:])}</h5>'); continue
        if s.startswith('## '):  close(); out.append(f'<h4>{_inline_md(s[3:])}</h4>'); continue
        if s.startswith('# '):   close(); out.append(f'<h3>{_inline_md(s[2:])}</h3>'); continue
        m = re.match(r'^[-*•]\s+(.*)', s)
        if m:
            if not in_ul: out.append('<ul>'); in_ul = True
            out.append(f'<li>{_inline_md(m.group(1))}</li>'); continue
        m = re.match(r'^\d+\.\s+(.*)', s)
        if m:
            if not in_ul: out.append('<ul>'); in_ul = True
            out.append(f'<li>{_inline_md(m.group(1))}</li>'); continue
        close()
        out.append(f'<p>{_inline_md(s)}</p>')
    close()
    return '\n'.join(out)

# ── Universal DOM-aware parser ─────────────────────────────────────────────────

def classify_link(link):
    combined = (link["text"] + " " + link["href"]).lower()
    if "package" in combined:  return "package"
    if "minute" in combined:   return "minutes"
    if "agenda" in combined:   return "agenda"
    return None

def make_absolute(href, base="https://nipissingtownship.com"):
    if href.startswith("http"): return href
    return base + (href if href.startswith("/") else "/" + href)

def extract_meetings_from_node(node):
    meetings = []
    current  = None

    def flush():
        nonlocal current
        if current:
            meetings.append(dict(current))
        current = None

    def start_meeting(month, day, year_str):
        nonlocal current
        flush()
        year     = int(year_str)
        date_str = f"{year}-{datetime.strptime(month, '%B').month:02d}-{int(day):02d}"
        current  = {
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
            text, last_end = str(node), 0
            for dm in DATE_RE.finditer(text):
                between = text[last_end:dm.start()].lower()
                if current:
                    if "cancel"    in between: current["cancelled"]   = True
                    if "reschedul" in between: current["rescheduled"] = True
                    if "postpon"   in between: current["postponed"]   = True
                start_meeting(dm.group(1), dm.group(2), dm.group(3))
                last_end = dm.end()
            tail = text[last_end:].lower()
            if current:
                if "cancel"    in tail: current["cancelled"]   = True
                if "reschedul" in tail: current["rescheduled"] = True
                if "postpon"   in tail: current["postponed"]   = True
        elif isinstance(node, Tag):
            if node.name == "br": return
            if node.name == "a":
                href  = node.get("href", "").strip()
                label = node.get_text(strip=True).lower()
                if href and current:
                    current["links"].append({"text": label, "href": make_absolute(href)})
                return
            for child in node.children:
                walk(child)

    for child in node.children:
        walk(child)
    flush()
    return meetings


def scrape_board(board):
    print(f"  Scraping {board['name']} ({board['format']})...")
    r = requests.get(board["url"], headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup    = BeautifulSoup(r.text, "html.parser")
    content = (soup.find("div", class_="entry-content") or soup.find("main") or soup.body)

    all_meetings = []
    seen_dates   = set()

    for elem in content.find_all(["p", "li", "div"]):
        if not DATE_RE.search(elem.get_text()):
            continue
        for m in extract_meetings_from_node(elem):
            if m["date_str"] in seen_dates:
                continue
            seen_dates.add(m["date_str"])
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
                "events":       [],
            })

    all_meetings.sort(key=lambda x: x["date"], reverse=True)
    future = sum(1 for m in all_meetings if m["is_future"])
    past   = sum(1 for m in all_meetings if not m["is_future"])
    w_min  = sum(1 for m in all_meetings if m["minutes_url"])
    print(f"    {past} past + {future} upcoming | {w_min} with minutes")
    return all_meetings


# ── AI ────────────────────────────────────────────────────────────────────────

def fetch_minutes_text(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200: return None
        text = r.content.decode("latin-1", errors="ignore")
        text = re.sub(r"[^\x20-\x7E\n\r\t]", " ", text)
        text = re.sub(r"\s{3,}", "\n", text)[:8000]
        return text if len(text) > 200 else None
    except Exception:
        return None

def ai_call(prompt, max_tokens=400):
    if not ANTHROPIC_KEY: return None
    try:
        r = requests.post(
            AI_API,
            headers={"Content-Type": "application/json",
                     "x-api-key": ANTHROPIC_KEY,
                     "anthropic-version": "2023-06-01"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"    AI error: {e}")
    return None

def generate_summary(meeting, text):
    return ai_call(
        f"These are minutes from a {meeting['board_name']} meeting on "
        f"{meeting['display_date']} in Nipissing Township, Ontario.\n\n{text}\n\n"
        "Write 2-3 plain-language sentences summarising what was discussed and decided. "
        "Focus on budgets, programs, facilities, votes. Skip procedural items. "
        "Write for a resident who just wants to know what happened.",
        max_tokens=300,
    )

def extract_events(meeting, text):
    result = ai_call(
        f"These are minutes from a {meeting['board_name']} meeting on "
        f"{meeting['display_date']} in Nipissing Township, Ontario.\n\n{text}\n\n"
        "List any upcoming community events mentioned. Output ONLY a JSON array:\n"
        '[{"title":"","date":"","time":"","location":"","details":"","registration":""}]\n'
        "If none, output: []",
        max_tokens=600,
    )
    if not result: return []
    try:
        clean  = re.sub(r"```[a-z]*", "", result).strip()
        events = json.loads(clean)
        if isinstance(events, list):
            for ev in events:
                ev["source_board"]   = meeting["board_name"]
                ev["source_date"]    = meeting["display_date"]
                ev["source_minutes"] = meeting.get("minutes_url", "")
            return events
    except Exception as e:
        print(f"    Event parse error: {e}")
    return []

def process_ai(meetings, max_summaries=8):
    processed = 0
    for m in meetings:
        if m["is_future"] or not m["minutes_url"]: continue
        if processed >= max_summaries: break
        if m.get("summary"): processed += 1; continue
        print(f"    AI: {m['display_date']}...")
        text = fetch_minutes_text(m["minutes_url"])
        if not text: continue
        m["summary"] = generate_summary(m, text)
        m["events"]  = extract_events(m, text)
        processed += 1
        time.sleep(0.4)
    return meetings


# ── Community calendar ────────────────────────────────────────────────────────

def build_calendar_html(all_boards_data):
    all_events = [ev for bd in all_boards_data for m in bd["meetings"] for ev in m.get("events", [])]
    if not all_events:
        event_html = '<p class="no-events">No upcoming events found yet.</p>'
    else:
        event_html = ""
        for ev in all_events:
            reg      = f'<div class="ev-reg">📋 {ev["registration"]}</div>' if ev.get("registration") else ""
            loc      = f' · {ev["location"]}' if ev.get("location") else ""
            time_str = f' · {ev["time"]}' if ev.get("time") else ""
            src_url  = ev.get("source_minutes", "")
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
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Community Events — council.chriswjohnston.ca</title>
<style>
:root{{--green:#2C4A3E;--pine:#3D6B5E;--pine-lt:#e8f0eb;--warm:#E8C98A;--rust:#C06830;
  --gold:#b8922a;--gold-lt:#fdf6e3;--bg:#FAF7F0;--white:#fff;--rule:#d8d0c8;--body:#3a3a3a;--muted:#6e6e6e;--ink:#1c1c1c}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--body);line-height:1.55}}
header{{background:var(--green);color:#fff;padding:1.5rem 2rem}}
.bc{{font-size:.72rem;margin-bottom:.5rem}}.bc a{{color:var(--warm);text-decoration:none;opacity:.8}}
header h1{{font-size:1.4rem;font-weight:700;margin-bottom:.25rem}}
header p{{font-size:.86rem;color:rgba(255,255,255,.7);max-width:600px}}
.main{{max-width:860px;margin:0 auto;padding:1.5rem 2rem 4rem}}
.notice{{background:var(--gold-lt);border:1px solid #e0c87a;border-left:4px solid var(--gold);
  border-radius:6px;padding:.9rem 1.2rem;margin-bottom:1.5rem;font-size:.84rem}}
.event-card{{background:var(--white);border:1px solid var(--rule);border-left:4px solid var(--rust);
  border-radius:6px;padding:1rem 1.25rem;margin-bottom:.75rem}}
.ev-when{{font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--rust);margin-bottom:.3rem}}
.ev-title{{font-size:1rem;font-weight:700;color:var(--ink);margin-bottom:.35rem}}
.ev-desc{{font-size:.86rem;line-height:1.62;color:var(--body);margin-bottom:.4rem}}
.ev-reg{{font-size:.82rem;background:var(--pine-lt);color:var(--pine);border-radius:4px;padding:3px 8px;display:inline-block;margin-bottom:.4rem}}
.ev-source{{font-size:.72rem;color:var(--muted)}}.ev-source a{{color:var(--pine)}}
.no-events{{color:var(--muted);font-size:.9rem;padding:1rem 0}}
footer{{background:#1E2B2A;color:rgba(255,255,255,.35);text-align:center;padding:1.5rem;font-size:.78rem}}
footer a{{color:var(--warm);text-decoration:none}}
</style>
</head>
<body>
<header>
  <div class="bc"><a href="/">← Council Archive</a></div>
  <h1>Community Events</h1>
  <p>Events extracted from board and committee meeting minutes. Updated automatically.</p>
</header>
<div class="main">
  <div class="notice">Events are extracted by AI and may not be complete. Always confirm with the <a href="https://nipissingtownship.com" target="_blank">Township of Nipissing</a>.</div>
  {event_html}
</div>
<footer><a href="/">council.chriswjohnston.ca</a> · Updated {datetime.now().strftime('%B %d, %Y')}</footer>
</body>
</html>"""


# ── Board page HTML ───────────────────────────────────────────────────────────

def build_board_html(board, meetings):

    # Sort: upcoming soonest first, then past newest first
    upcoming = sorted([m for m in meetings if m["is_future"] and not m.get("cancelled")],
                      key=lambda x: x["date"])
    past     = sorted([m for m in meetings if not m["is_future"] or m.get("cancelled")],
                      key=lambda x: x["date"], reverse=True)
    ordered  = upcoming + past

    # Year pills
    years      = sorted({m["year"] for m in meetings}, reverse=True)
    year_pills = "".join(f'<a href="#{board["id"]}-y{y}" class="yr-pill">{y}</a>' for y in years)

    # Stats
    n_up     = sum(1 for m in meetings if m["is_future"] and not m.get("cancelled"))
    n_past   = len(meetings) - n_up
    n_min    = sum(1 for m in meetings if m.get("minutes_url"))
    n_cancel = sum(1 for m in meetings if m.get("cancelled"))

    SVG_DOC = ('<svg width="11" height="11" viewBox="0 0 24 24" fill="none" '
               'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
               '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
               '<polyline points="14 2 14 8 20 8"/></svg>')

    rows      = ""
    last_year = None

    for m in ordered:
        if m["year"] != last_year:
            rows += (f'<tr class="yr-head-row" id="{board["id"]}-y{m["year"]}">'
                     f'<td colspan="3">{m["year"]}</td></tr>\n')
            last_year = m["year"]

        is_up    = m["is_future"] and not m.get("cancelled")
        row_cls  = ("upcoming-row"  if is_up              else
                    "cancelled-row" if m.get("cancelled") else
                    "postponed-row" if m.get("postponed") else "")
        date_cls = "struck" if (m.get("cancelled") or m.get("postponed")) else ""

        if is_up:                  badge = '<span class="badge badge-up">Upcoming</span>'
        elif m.get("cancelled"):   badge = '<span class="badge badge-cancel">Cancelled</span>'
        elif m.get("postponed"):   badge = '<span class="badge badge-postpone">Postponed</span>'
        elif m.get("rescheduled"): badge = '<span class="badge badge-reschedule">Rescheduled</span>'
        elif m.get("minutes_url"): badge = '<span class="badge badge-min">Minutes</span>'
        elif m.get("agenda_url"):  badge = '<span class="badge badge-agenda">Agenda Only</span>'
        else:                      badge = ""

        summ      = render_summary(m.get("summary"))
        summ_html = f'<div class="summ">{summ}</div>' if summ else ""

        links = []
        if m.get("agenda_url"):
            links.append(f'<a href="{m["agenda_url"]}" target="_blank" rel="noopener" class="dl dl-agenda">{SVG_DOC} Agenda</a>')
        if m.get("minutes_url"):
            links.append(f'<a href="{m["minutes_url"]}" target="_blank" rel="noopener" class="dl dl-minutes">{SVG_DOC} Minutes</a>')
        if m.get("package_url"):
            links.append(f'<a href="{m["package_url"]}" target="_blank" rel="noopener" class="dl">{SVG_DOC} Package</a>')
        links_html = "".join(links) or '<span class="no-doc">&mdash;</span>'

        rows += (f'<tr class="{row_cls}">'
                 f'<td class="date-cell {date_cls}">{m["display_date"]}</td>'
                 f'<td class="info-cell">{badge}{summ_html}</td>'
                 f'<td class="doc-cell">{links_html}</td>'
                 f'</tr>\n')

    bylaw_link = (f'<a href="{board["bylaw_url"]}" target="_blank" rel="noopener">Read the governing by-law &rarr;</a>'
                  if board.get("bylaw_url") else "")
    updated    = datetime.now().strftime("%-d %B %Y")
    up_banner  = ("<div class='up-banner'><strong>Upcoming meetings shown first.</strong> "
                  "Documents will appear once published by the Township.</div>" if n_up > 0 else "")

    CSS = """*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
:root{--forest:#2C4A3E;--pine:#3D6B5E;--sky:#A8D5E2;--sky-lt:#EAF5F8;--sand:#F2EAD3;--warm:#E8C98A;--rust:#C06830;--charcoal:#1E2B2A;--cream:#FAF7F0;--white:#fff;--muted:#6e6e6e;--rule:#d8d0c8;--shadow:0 2px 16px rgba(30,43,42,.10)}
body{font-family:'Lato',Georgia,sans-serif;background:var(--cream);color:var(--charcoal);line-height:1.6;overflow-x:hidden}
nav{position:fixed;top:0;left:0;right:0;z-index:100;display:flex;align-items:center;justify-content:space-between;padding:1rem 2.5rem;background:rgba(44,74,62,.97);backdrop-filter:blur(8px);box-shadow:0 2px 20px rgba(0,0,0,.2)}
.nav-logo{font-family:'Playfair Display',serif;font-size:1.1rem;color:var(--warm);text-decoration:none}
.nav-logo span{color:var(--sky)}
.nav-links{display:flex;gap:1.6rem;list-style:none}
.nav-links a{color:rgba(255,255,255,.85);text-decoration:none;font-size:.75rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;transition:color .2s}
.nav-links a:hover{color:var(--warm)}
.page-hero{background:var(--forest);padding:7rem 2rem 3.5rem;border-bottom:4px solid var(--warm);position:relative;overflow:hidden}
.page-hero::before{content:'';position:absolute;inset:0;background:repeating-linear-gradient(-45deg,transparent,transparent 40px,rgba(255,255,255,.015) 40px,rgba(255,255,255,.015) 80px)}
.inner{max-width:1100px;margin:0 auto;position:relative}
.eyebrow{font-size:.7rem;font-weight:700;letter-spacing:.25em;text-transform:uppercase;color:var(--warm);margin-bottom:.8rem;opacity:0;animation:fadeUp .6s ease .1s forwards}
.page-hero h1{font-family:'Playfair Display',serif;font-size:clamp(1.8rem,4vw,3rem);font-weight:800;color:var(--white);line-height:1.1;margin-bottom:.5rem;opacity:0;animation:fadeUp .6s ease .25s forwards}
.sub{font-size:.95rem;font-weight:300;color:rgba(255,255,255,.72);max-width:580px;line-height:1.75;opacity:0;animation:fadeUp .6s ease .4s forwards}
.breadcrumb{display:flex;align-items:center;gap:.5rem;margin-top:1.4rem;font-size:.76rem;color:rgba(255,255,255,.42);opacity:0;animation:fadeUp .6s ease .5s forwards}
.breadcrumb a{color:var(--sky);text-decoration:none}.breadcrumb a:hover{color:var(--warm)}.breadcrumb .sep{opacity:.4}
.stats-bar{background:var(--white);border-bottom:1px solid var(--rule);padding:.85rem 2.5rem}
.stats-bar-inner{max-width:1100px;margin:0 auto;display:flex;gap:2.5rem;flex-wrap:wrap;align-items:center}
.stat strong{font-family:'Playfair Display',serif;font-size:1.25rem;color:var(--forest);font-weight:700;margin-right:.3rem}
.stat span{font-size:.72rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.stat.up-stat strong{color:var(--sky)}.stat-div{width:1px;height:22px;background:var(--rule)}
.upd{font-size:.72rem;color:var(--muted);margin-left:auto}
main{max-width:1100px;margin:2.5rem auto;padding:0 2rem 5rem}
.up-banner{background:var(--sky-lt);border-left:4px solid var(--sky);border-radius:0 6px 6px 0;padding:.85rem 1.25rem;margin-bottom:1.25rem;font-size:.86rem}
.up-banner strong{color:#1a5a6e}
.bylaw-note{background:#f0f5f2;border-left:4px solid var(--pine);border-radius:0 6px 6px 0;padding:.85rem 1.25rem;margin-bottom:2rem;font-size:.83rem;color:var(--muted)}
.bylaw-note a{color:var(--pine);font-weight:700;text-decoration:none}.bylaw-note a:hover{color:var(--forest)}
.section-label{font-size:.67rem;font-weight:700;letter-spacing:.25em;text-transform:uppercase;color:var(--rust);margin-bottom:.6rem}
.year-nav{display:flex;flex-wrap:wrap;gap:.45rem;margin-bottom:1.75rem}
.yr-pill{font-size:.78rem;font-weight:700;padding:.28rem .75rem;border-radius:4px;background:var(--white);border:1px solid rgba(44,74,62,.2);color:var(--forest);text-decoration:none;transition:background .15s,color .15s}
.yr-pill:hover,.yr-pill.active{background:var(--forest);color:var(--white);border-color:var(--forest)}
.table-wrapper{background:var(--white);border:1px solid rgba(44,74,62,.12);border-radius:10px;overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
.meetings-table{width:100%;border-collapse:collapse;font-size:.88rem}
.meetings-table thead tr{background:var(--forest);color:var(--white)}
.meetings-table thead th{padding:.72rem 1rem;text-align:left;font-size:.68rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;white-space:nowrap}
.yr-head-row td{background:var(--sand);padding:.38rem 1rem;font-size:.62rem;font-weight:700;letter-spacing:.22em;text-transform:uppercase;color:var(--rust);border-bottom:1px solid var(--rule)}
.meetings-table tbody tr{border-bottom:1px solid rgba(44,74,62,.08);transition:background .15s}
.meetings-table tbody tr:last-child{border-bottom:none}
.meetings-table tbody tr:hover{background:rgba(44,74,62,.03)}
.upcoming-row{background:var(--sky-lt) !important}.upcoming-row:hover{background:#d3edf5 !important}
.cancelled-row{opacity:.55}.postponed-row{opacity:.7}
.date-cell{font-family:'Playfair Display',serif;font-weight:700;color:var(--forest);white-space:nowrap;min-width:190px;padding:.82rem 1rem;vertical-align:top}
.date-cell.struck{text-decoration:line-through;color:var(--muted)}
.upcoming-row .date-cell{color:#1a5a6e}
.info-cell{padding:.82rem 1rem;vertical-align:top}
.doc-cell{padding:.82rem 1rem;vertical-align:top;display:flex;flex-direction:column;gap:5px;min-width:120px}
.badge{display:inline-block;font-size:.58rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:2px 8px;border-radius:3px;margin-bottom:5px}
.badge-up{background:var(--sky);color:#fff}.badge-min{background:#e8f0eb;color:var(--pine)}
.badge-agenda{background:var(--sand);color:#8a6820}.badge-cancel{background:#f0f0f0;color:var(--muted)}
.badge-postpone{background:#f5f0ff;color:#6b52a0}.badge-reschedule{background:#fff3e0;color:#b86000}
.summ{margin-top:.4rem;font-size:.82rem;line-height:1.65;color:#555}
.summ p{margin-bottom:.4rem}.summ p:last-child{margin-bottom:0}
.summ h3,.summ h4,.summ h5{font-family:'Playfair Display',serif;color:var(--forest);margin:.6rem 0 .25rem}
.summ h3{font-size:.95rem}.summ h4{font-size:.88rem;text-transform:uppercase;letter-spacing:.05em}
.summ ul{margin:.3rem 0 .5rem 1.1rem}.summ li{margin-bottom:.15rem}
.summ hr{border:none;border-top:1px solid var(--rule);margin:.6rem 0}
.summ strong{color:var(--forest)}
.dl{display:inline-flex;align-items:center;gap:.3rem;background:var(--cream);border:1px solid rgba(44,74,62,.18);border-radius:4px;padding:.28rem .65rem;font-size:.75rem;font-weight:700;color:var(--forest);text-decoration:none;white-space:nowrap;transition:background .15s,color .15s,border-color .15s}
.dl:hover{background:var(--forest);color:var(--white);border-color:var(--forest)}
.dl-minutes{background:rgba(192,104,48,.08);border-color:rgba(192,104,48,.25);color:var(--rust)}
.dl-minutes:hover{background:var(--rust);color:var(--white);border-color:var(--rust)}
.no-doc{color:rgba(44,74,62,.25);font-size:.82rem}
footer{background:var(--charcoal);padding:2.5rem 2rem;text-align:center}
.footer-inner{max-width:1100px;margin:0 auto;display:flex;flex-direction:column;align-items:center;gap:.6rem}
.footer-logo{font-family:'Playfair Display',serif;font-size:1.05rem;color:var(--warm)}
.footer-logo span{color:var(--sky)}
footer p{font-size:.78rem;color:rgba(255,255,255,.35)}
footer a{color:var(--warm);text-decoration:none}
@keyframes fadeUp{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:700px){
  nav{padding:1rem 1.25rem}.nav-links{gap:.8rem}.nav-links a{font-size:.65rem}
  main{padding:0 1.25rem 4rem}.page-hero{padding:5.5rem 1.25rem 2.5rem}
  .stats-bar{padding:.75rem 1.25rem}.stats-bar-inner{gap:1rem}
  .meetings-table thead{display:none}
  .meetings-table tbody tr{display:block;padding:.85rem 1rem;border-bottom:2px solid rgba(44,74,62,.1)}
  .meetings-table td{display:block;padding:.25rem 0;border:none}
  .date-cell{font-size:1rem;margin-bottom:.4rem}
  .doc-cell{flex-direction:row;flex-wrap:wrap}
  .yr-head-row td{display:block}}"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{board['name']} \u2013 Nipissing Township Archive</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700;800&family=Lato:wght@300;400;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<nav>
  <a class="nav-logo" href="/">Nipissing <span>Council Archive</span></a>
  <ul class="nav-links">
    <li><a href="/">Home</a></li>
    <li><a href="https://bylaw.chriswjohnston.ca">By-Law Archive</a></li>
  </ul>
</nav>
<div class="page-hero">
  <div class="inner">
    <p class="eyebrow">Nipissing Township \u00b7 {board['name']}</p>
    <h1>{board['name']}</h1>
    <p class="sub">{board['description']}</p>
    <div class="breadcrumb">
      <a href="/">All Years</a><span class="sep">/</span>
      <a href="/boards/">Boards &amp; Committees</a><span class="sep">/</span>
      <span>{board['name']}</span>
    </div>
  </div>
</div>
<div class="stats-bar">
  <div class="stats-bar-inner">
    <div class="stat up-stat"><strong>{n_up}</strong><span>Upcoming</span></div>
    <div class="stat-div"></div>
    <div class="stat"><strong>{n_past}</strong><span>Past</span></div>
    <div class="stat"><strong>{n_min}</strong><span>With minutes</span></div>
    <div class="stat"><strong>{n_cancel}</strong><span>Cancelled</span></div>
    <div class="upd">Updated {updated}</div>
  </div>
</div>
<main>
  {up_banner}
  <div class="bylaw-note">
    Governed by <strong>By-Law {board['bylaw']}</strong>. {bylaw_link}
    &nbsp;\u00b7&nbsp; Source: <a href="{board['url']}" target="_blank" rel="noopener">Township of Nipissing</a>
  </div>
  <p class="section-label">Browse by year</p>
  <nav class="year-nav" aria-label="Jump to year">{year_pills}</nav>
  <div class="table-wrapper">
    <table class="meetings-table">
      <thead><tr><th>Meeting Date</th><th>Status</th><th>Documents</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</main>
<footer>
  <div class="footer-inner">
    <div class="footer-logo">Chris <span>Johnston</span></div>
    <p>Candidate for Nipissing Township Council \u00b7 Municipal Election October 2026</p>
    <p><a href="/">council.chriswjohnston.ca</a> \u00b7 Sourced from <a href="https://nipissingtownship.com" target="_blank" rel="noopener">nipissingtownship.com</a></p>
  </div>
</footer>
<script>
function highlightPill(){{
  const hash=location.hash.slice(1);
  document.querySelectorAll('.yr-pill').forEach(el=>
    el.classList.toggle('active',el.getAttribute('href')==='#'+hash));
}}
highlightPill();
window.addEventListener('hashchange',highlightPill);
document.querySelectorAll('.yr-pill').forEach(pill=>{{
  pill.addEventListener('click',e=>{{
    e.preventDefault();
    const target=document.querySelector(pill.getAttribute('href'));
    if(target){{
      const top=target.getBoundingClientRect().top+window.scrollY-80;
      window.scrollTo({{top,behavior:'smooth'}});
      history.replaceState(null,'',pill.getAttribute('href'));
      highlightPill();
    }}
  }});
}});
</script>
</body>
</html>"""


# ── Boards index page ─────────────────────────────────────────────────────────

def build_index_html(boards_data):
    cards = ""
    for b in boards_data:
        meetings     = b["meetings"]
        next_m       = next((m for m in sorted(meetings, key=lambda x: x["date"]) if m["is_future"] and not m["cancelled"]), None)
        recent       = next((m for m in meetings if m.get("minutes_url")), None)
        next_str     = f"Next: {next_m['display_date']}" if next_m else "No upcoming meetings listed"
        min_str      = f"Last minutes: {recent['display_date']}" if recent else "No minutes yet"
        future_count = sum(1 for m in meetings if m["is_future"])
        cards += f"""<a class="board-card" href="{b['id']}/index.html">
  <div class="board-name">{b['name']}</div>
  <div class="board-next">{next_str}</div>
  <div class="board-meta">{len(meetings)} meetings total \u00b7 {future_count} upcoming \u00b7 {min_str}</div>
</a>\n"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Boards &amp; Committees \u2014 council.chriswjohnston.ca</title>
<style>
:root{{--green:#2C4A3E;--pine:#3D6B5E;--sky:#A8D5E2;--warm:#E8C98A;--bg:#FAF7F0;--rule:#d8d0c8;--body:#3a3a3a;--muted:#6e6e6e;--ink:#1c1c1c}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--body);max-width:800px;margin:0 auto;padding:2rem}}
.back{{font-size:.8rem;margin-bottom:1.5rem}}.back a{{color:var(--pine);text-decoration:none}}
h1{{font-size:1.5rem;color:var(--green);margin-bottom:.4rem}}
.intro{{font-size:.88rem;color:var(--muted);margin-bottom:2rem;line-height:1.65}}
.board-card{{display:block;background:#fff;border:1px solid var(--rule);border-left:4px solid var(--green);border-radius:6px;padding:1rem 1.25rem;margin-bottom:.75rem;text-decoration:none;transition:box-shadow .2s,border-left-color .2s}}
.board-card:hover{{box-shadow:0 3px 14px rgba(0,0,0,.1);border-left-color:var(--pine)}}
.board-name{{font-weight:700;color:var(--green);margin-bottom:.2rem;font-size:1rem}}
.board-next{{font-size:.86rem;font-weight:600;color:#1a5a6e;margin-bottom:.2rem}}
.board-meta{{font-size:.76rem;color:var(--muted)}}
.cal-link{{display:block;background:#fff;border:1px dashed var(--sky);border-left:4px solid var(--sky);border-radius:6px;padding:1rem 1.25rem;margin-bottom:.75rem;text-decoration:none;color:var(--ink);transition:box-shadow .2s}}
.cal-link:hover{{box-shadow:0 3px 14px rgba(0,0,0,.08)}}
.cal-link .cal-title{{font-weight:700;color:#1a5a6e;margin-bottom:.2rem}}
.cal-link .cal-desc{{font-size:.82rem;color:var(--muted)}}
footer{{font-size:.75rem;color:var(--muted);margin-top:2rem;text-align:center}}
</style>
</head>
<body>
<div class="back"><a href="/">\u2190 Back to Council Archive</a></div>
<h1>Boards &amp; Committees</h1>
<p class="intro">Meeting agendas and minutes for Nipissing Township boards and committees. Updated automatically every day.</p>
<a class="cal-link" href="calendar.html">
  <div class="cal-title">\U0001f4c5 Community Events Calendar</div>
  <div class="cal-desc">Events extracted from meeting minutes \u2014 programs, socials, fundraisers, registered activities</div>
</a>
{cards}
<footer>council.chriswjohnston.ca \u00b7 Updated {datetime.now().strftime('%B %d, %Y')}</footer>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("Board & Committee Scraper \u2014 council.chriswjohnston.ca")
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M')} \u00b7 Today: {TODAY}")
    print("=" * 55)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)
    boards_data = []

    for board in BOARDS:
        try:
            meetings = scrape_board(board)
            if ANTHROPIC_KEY:
                meetings = process_ai(meetings, max_summaries=8)

            board_dir = OUTPUT_DIR / board["id"]
            board_dir.mkdir(exist_ok=True)
            (board_dir / "data.json").write_text(
                json.dumps({"board": board, "meetings": meetings,
                            "generated": datetime.now().isoformat()}, indent=2)
            )
            (board_dir / "index.html").write_text(
                build_board_html(board, meetings), encoding="utf-8"
            )
            boards_data.append({"id": board["id"], "name": board["name"], "meetings": meetings})
            print(f"  \u2713 {board['name']}")

        except Exception as e:
            print(f"  \u2717 {board['name']}: {e}")
            import traceback; traceback.print_exc()

    combined = {"boards": boards_data, "generated": datetime.now().isoformat()}
    (DOCS_DIR / "boards-data.json").write_text(json.dumps(combined, indent=2))
    print(f"  \u2713 docs/boards-data.json written ({sum(len(b['meetings']) for b in boards_data)} meetings)")

    (OUTPUT_DIR / "index.html").write_text(build_index_html(boards_data), encoding="utf-8")
    (OUTPUT_DIR / "calendar.html").write_text(build_calendar_html(boards_data), encoding="utf-8")

    try:
        import subprocess
        result = subprocess.run(["python3", "build_index.py"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  \u2713 docs/index.html rebuilt")
        else:
            print(f"  \u26a0 build_index.py failed: {result.stderr[:200]}")
    except Exception as e:
        print(f"  \u26a0 Could not run build_index.py: {e}")

    print(f"\n\u2713 Done \u2014 {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
