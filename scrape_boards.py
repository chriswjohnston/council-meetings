#!/usr/bin/env python3
"""
scrape_boards.py — council.chriswjohnston.ca
=============================================
Scrapes Township of Nipissing board and committee meeting documents
(Recreation Committee, Museum Board, Cemetery Committee) from the
Township website and builds a searchable archive alongside the
existing council meeting archive.

Belongs in: council.chriswjohnston.ca repo root
Run via: .github/workflows/scrape-boards.yml (daily + on push)
"""

import re, json, time, os
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup

BOARDS = [
    {
        "id": "recreation",
        "name": "Recreation Committee",
        "url": "https://nipissingtownship.com/services/recreation/",
        "bylaw": "2023-09",
        "bylaw_url": "https://nipissingtownship.com/wp-content/uploads/2023/01/Recreation-Committee-By-Law.pdf",
        "description": "Responsible for management and conduct of recreational programming and the Community Centre at 2381 Highway 654.",
    },
    {
        "id": "museum",
        "name": "Museum Board",
        "url": "https://nipissingtownship.com/services/museum-services-and-information/",
        "bylaw": "2023-10",
        "bylaw_url": "https://nipissingtownship.com/wp-content/uploads/2023/01/By-Law-2023-10-Museum-Board.pdf",
        "description": "Board of management for the Nipissing Township Museum, preserving and displaying the history of the Township.",
    },
    {
        "id": "cemetery",
        "name": "Cemetery Committee",
        "url": "https://nipissingtownship.com/services/cemetery/",
        "bylaw": "2023-11",
        "bylaw_url": None,
        "description": "Administration of the Nipissing Union Cemetery, Commanda Cemetery and St. John's Alsace Cemetery.",
    },
]

OUTPUT_DIR = Path("boards")
HEADERS = {"User-Agent": "council-archive-bot/1.0 (chriswjohnston.ca civic tool)"}
AI_API = "https://api.anthropic.com/v1/messages"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def scrape_board_page(board: dict) -> list[dict]:
    print(f"  Scraping {board['name']}...")
    r = requests.get(board["url"], headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    content = soup.find("div", class_="entry-content") or soup.find("main") or soup.body

    meetings = []
    current_year = None
    date_pattern = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2}),?\s+(\d{4})"
    )
    year_pattern = re.compile(r"^\*?\*?(\d{4})\s*(Meeting Dates|Agendas|Minutes)?\*?\*?$")
    all_links = {a.get_text(strip=True): a["href"] for a in content.find_all("a", href=True)}

    for line in content.get_text(separator="\n").split("\n"):
        line = line.strip()
        ym = year_pattern.match(line)
        if ym:
            current_year = int(ym.group(1))
            continue
        dm = date_pattern.search(line)
        if dm and current_year:
            month, day, year = dm.group(1), dm.group(2), dm.group(3)
            date_str = f"{year}-{datetime.strptime(month, '%B').month:02d}-{int(day):02d}"
            cancelled = "CANCELLED" in line.upper()
            agenda_url = minutes_url = package_url = None
            date_variants = [
                f"{month[:3].lower()}-{int(day):02d}-{year}",
                f"{month.lower()}-{int(day):02d}-{year}",
                f"{year}-{datetime.strptime(month, '%B').month:02d}-{int(day):02d}",
                f"{month.lower()}-{year}",
            ]
            for _, href in all_links.items():
                lower = href.lower()
                if not any(v in lower for v in date_variants):
                    continue
                if "agenda" in lower and "package" not in lower:
                    agenda_url = href
                elif "minute" in lower:
                    minutes_url = href
                elif "package" in lower:
                    package_url = href
            meetings.append({
                "date": date_str,
                "display_date": f"{month} {int(day)}, {year}",
                "year": int(year),
                "cancelled": cancelled,
                "agenda_url": agenda_url,
                "minutes_url": minutes_url,
                "package_url": package_url,
                "board_id": board["id"],
                "board_name": board["name"],
                "summary": None,
            })

    seen, unique = set(), []
    for m in meetings:
        if m["date"] not in seen:
            seen.add(m["date"])
            unique.append(m)
    unique.sort(key=lambda x: x["date"], reverse=True)
    print(f"    Found {len(unique)} meetings ({sum(1 for m in unique if m['minutes_url'])} with minutes)")
    return unique


def generate_summary(meeting: dict) -> str | None:
    if not ANTHROPIC_KEY or not meeting.get("minutes_url"):
        return None
    try:
        r = requests.get(meeting["minutes_url"], headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        text = r.content.decode("latin-1", errors="ignore")
        text = re.sub(r"[^\x20-\x7E\n\r\t]", " ", text)
        text = re.sub(r"\s{3,}", "\n", text)[:6000]
        if len(text) < 200:
            return None
        resp = requests.post(
            AI_API,
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": (
                    f"This is the minutes of a {meeting['board_name']} meeting "
                    f"held on {meeting['display_date']} for the Township of Nipissing, Ontario.\n\n"
                    f"Minutes text:\n{text}\n\n"
                    "Write a 2-3 sentence plain-language summary of what was discussed and decided. "
                    "Focus on substantive matters — budgets, programs, facilities, decisions. "
                    "Skip procedural items (approval of minutes, adjournment). "
                    "Write as if for a resident who wants to know what happened."
                )}],
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"    Summary error: {e}")
    return None


def build_board_html(board: dict, meetings: list[dict]) -> str:
    years = sorted(set(m["year"] for m in meetings), reverse=True)
    by_year = {}
    for m in meetings:
        by_year.setdefault(m["year"], []).append(m)

    rows = ""
    for year in years:
        rows += f'<div class="year-group" id="y{year}"><div class="year-label">{year}</div>\n'
        for m in by_year[year]:
            sc = "cancelled" if m["cancelled"] else ("has-minutes" if m["minutes_url"] else "agenda-only")
            st = "Cancelled" if m["cancelled"] else ("Minutes Available" if m["minutes_url"] else "Agenda Only")
            lnks = ""
            if m.get("agenda_url"): lnks += f'<a href="{m["agenda_url"]}" target="_blank" class="dl agenda">Agenda</a>'
            if m.get("minutes_url"): lnks += f'<a href="{m["minutes_url"]}" target="_blank" class="dl minutes">Minutes</a>'
            if m.get("package_url"): lnks += f'<a href="{m["package_url"]}" target="_blank" class="dl package">Package</a>'
            summ = f'<div class="summary">{m["summary"]}</div>' if m.get("summary") else ""
            rows += f'''<div class="meeting-row {sc}">
  <div class="mdate">{m["display_date"]}</div>
  <div class="minfo"><span class="badge {sc}">{st}</span>{summ}</div>
  <div class="mlinks">{lnks}</div>
</div>\n'''
        rows += "</div>\n"

    total = len(meetings)
    w_min = sum(1 for m in meetings if m.get("minutes_url"))
    cancelled = sum(1 for m in meetings if m["cancelled"])

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{board['name']} — Nipissing Township Archive · council.chriswjohnston.ca</title>
<style>
:root{{--green:#1a4d2e;--gold:#b8922a;--bg:#f9f7f4;--white:#fff;--rule:#d8d0c8;--body:#3a3a3a;--muted:#6e6e6e;--red:#b83232}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--body)}}
header{{background:var(--green);color:#fff;padding:1.5rem 2rem}}
header h1{{font-size:1.3rem;font-weight:700;margin-bottom:.25rem}}
header p{{font-size:.88rem;color:rgba(255,255,255,.7)}}
.bc{{font-size:.75rem;margin-bottom:.5rem}}.bc a{{color:rgba(255,255,255,.65);text-decoration:none}}
.stats{{display:flex;gap:2rem;padding:1rem 2rem;background:var(--white);border-bottom:1px solid var(--rule);font-size:.82rem}}
.stat strong{{font-size:1.2rem;display:block;color:var(--green);font-weight:700}}
.main{{max-width:960px;margin:0 auto;padding:1.5rem 2rem}}
.bylaw-note{{background:#e8f0eb;border:1px solid #b8d4c2;border-left:4px solid var(--green);border-radius:6px;padding:.9rem 1.2rem;margin-bottom:1.5rem;font-size:.84rem}}
.bylaw-note a{{color:var(--green)}}
.year-group{{margin-bottom:2rem}}
.year-label{{font-size:.68rem;font-weight:700;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);padding:.5rem 0;border-bottom:2px solid var(--rule);margin-bottom:.5rem}}
.meeting-row{{display:grid;grid-template-columns:130px 1fr auto;gap:1rem;align-items:start;padding:.75rem 0;border-bottom:1px solid var(--rule)}}
.meeting-row:last-child{{border-bottom:none}}
.mdate{{font-size:.85rem;font-weight:600;padding-top:2px}}
.meeting-row.cancelled .mdate{{color:var(--muted);text-decoration:line-through}}
.badge{{display:inline-block;font-size:.62rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:2px 7px;border-radius:3px;margin-bottom:4px}}
.badge.has-minutes{{background:#e8f0eb;color:var(--green)}}
.badge.agenda-only{{background:#fdf6e3;color:var(--gold)}}
.badge.cancelled{{background:#f0f0f0;color:var(--muted)}}
.summary{{font-size:.82rem;line-height:1.6;color:var(--body);margin-top:4px}}
.mlinks{{display:flex;flex-direction:column;gap:4px;min-width:80px}}
.dl{{font-size:.72rem;font-weight:700;text-decoration:none;padding:3px 8px;border-radius:3px;text-align:center}}
.dl.agenda{{background:var(--green);color:#fff}}
.dl.minutes{{background:var(--gold);color:#fff}}
.dl.package{{background:#555;color:#fff}}
@media(max-width:600px){{.meeting-row{{grid-template-columns:1fr}}.stats{{flex-wrap:wrap;gap:1rem}}}}
</style></head><body>
<header>
  <div class="bc"><a href="/">← Council Archive</a> / Boards &amp; Committees</div>
  <h1>{board['name']}</h1>
  <p>{board['description']}</p>
</header>
<div class="stats">
  <div class="stat"><strong>{total}</strong>Meetings scheduled</div>
  <div class="stat"><strong>{w_min}</strong>Minutes available</div>
  <div class="stat"><strong>{cancelled}</strong>Cancelled</div>
  <div class="stat"><strong>{len(years)}</strong>Years covered</div>
</div>
<div class="main">
  <div class="bylaw-note">
    Governed by <strong>By-Law {board['bylaw']}</strong>.
    {"<a href='" + board['bylaw_url'] + "' target='_blank'>Read the governing by-law →</a>" if board['bylaw_url'] else ""}
    &nbsp;·&nbsp; Source: <a href="{board['url']}" target="_blank">Township of Nipissing website</a>
    &nbsp;·&nbsp; Updated: {datetime.now().strftime('%B %d, %Y')}
  </div>
  {rows}
</div></body></html>"""


def build_index_html(boards_data: list[dict]) -> str:
    cards = ""
    for b in boards_data:
        meetings = b["meetings"]
        recent = next((m for m in meetings if m.get("minutes_url")), None)
        recent_text = f"Last minutes: {recent['display_date']}" if recent else "No minutes yet"
        cards += f'''<a class="board-card" href="{b['id']}/index.html">
  <div class="board-name">{b['name']}</div>
  <div class="board-meta">{len(meetings)} meetings · {recent_text}</div>
</a>\n'''
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Boards &amp; Committees — Nipissing Township · council.chriswjohnston.ca</title>
<style>
body{{font-family:system-ui,sans-serif;background:#f9f7f4;color:#3a3a3a;max-width:800px;margin:0 auto;padding:2rem}}
h1{{font-size:1.5rem;margin-bottom:.5rem;color:#1a4d2e}}
.intro{{font-size:.9rem;color:#666;margin-bottom:2rem;line-height:1.65}}
.board-card{{display:block;background:#fff;border:1px solid #d8d0c8;border-left:4px solid #1a4d2e;border-radius:6px;padding:1rem 1.25rem;margin-bottom:.75rem;text-decoration:none;transition:box-shadow .2s}}
.board-card:hover{{box-shadow:0 3px 12px rgba(0,0,0,.1)}}
.board-name{{font-weight:700;color:#1a4d2e;margin-bottom:.2rem}}
.board-meta{{font-size:.8rem;color:#888}}
.back{{font-size:.82rem;margin-bottom:1.5rem}}.back a{{color:#1a4d2e}}
</style></head><body>
<div class="back"><a href="/">← Back to Council Archive</a></div>
<h1>Boards &amp; Committees</h1>
<p class="intro">Meeting agendas and minutes for Township of Nipissing boards and committees.
Documents sourced from the Township website. Updated automatically via GitHub Actions.</p>
{cards}
<p style="font-size:.75rem;color:#aaa;margin-top:2rem">Last updated: {datetime.now().strftime('%B %d, %Y')}</p>
</body></html>"""


def main():
    print("=" * 50)
    print("Board & Committee Scraper — council.chriswjohnston.ca")
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    OUTPUT_DIR.mkdir(exist_ok=True)
    boards_data = []

    for board in BOARDS:
        try:
            meetings = scrape_board_page(board)
            if ANTHROPIC_KEY:
                for m in meetings[:6]:
                    if m.get("minutes_url") and not m.get("summary"):
                        print(f"    Summarising {m['display_date']}...")
                        m["summary"] = generate_summary(m)
                        time.sleep(0.5)

            board_dir = OUTPUT_DIR / board["id"]
            board_dir.mkdir(exist_ok=True)
            (board_dir / "data.json").write_text(json.dumps({"board": board, "meetings": meetings, "generated": datetime.now().isoformat()}, indent=2))
            (board_dir / "index.html").write_text(build_board_html(board, meetings))
            boards_data.append({"id": board["id"], "name": board["name"], "meetings": meetings})
            print(f"  ✓ {board['name']}")
        except Exception as e:
            print(f"  ✗ {board['name']}: {e}")

    (OUTPUT_DIR / "index.html").write_text(build_index_html(boards_data))
    print(f"\n✓ Done — {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
