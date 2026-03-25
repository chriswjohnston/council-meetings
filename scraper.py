"""
Nipissing Township Council Meeting Scraper
==========================================
Scrapes https://nipissingtownship.com/council-meeting-dates-agendas-minutes/
Downloads new PDFs, organizes by year, and generates a static HTML archive site.

Output goes into docs/ which is served by GitHub Pages.
Design matches chriswjohnston.ca campaign site.
"""

import os
import re
import io
import json
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime
from collections import defaultdict
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

try:
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    PDF_EXTRACT = True
except ImportError:
    PDF_EXTRACT = False

SOURCE_URL         = "https://nipissingtownship.com/council-meeting-dates-agendas-minutes/"
YOUTUBE_CHANNEL    = "https://www.youtube.com/@townshipofnipissing505/streams"
YOUTUBE_CHANNEL_ID = "UC2XSMZqRNHbwVppelfKcEXw"
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

DOCS_DIR       = Path("docs")
STATE_FILE     = Path("state.json")
SUMMARIES_FILE = Path("summaries.json")

# Known YouTube video URLs — add manually for backfill
# e.g. "January 6, 2026": "https://www.youtube.com/watch?v=XXXXXXX"
YOUTUBE_VIDEOS = {
}


# ─────────────────────────────────────────────
#  YOUTUBE RSS
# ─────────────────────────────────────────────

def fetch_youtube_videos(state):
    """Fetch recent videos via RSS. Persists to state so URLs survive beyond 15-video window."""
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
    videos = {v["date"]: v["url"] for v in state.get("_youtube_videos", {}).values()}
    print(f"Fetching YouTube RSS ... ({len(videos)} previously saved)")

    DATE_RE = re.compile(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE
    )

    try:
        resp = requests.get(rss_url, timeout=15)
        resp.raise_for_status()
        NS = {
            "atom":  "http://www.w3.org/2005/Atom",
            "yt":    "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/",
        }
        root = ET.fromstring(resp.content)
        for entry in root.findall("atom:entry", NS):
            title_el = entry.find("atom:title", NS)
            link_el  = entry.find("atom:link",  NS)
            if title_el is None or link_el is None:
                continue
            title = title_el.text or ""
            url   = link_el.get("href", "")
            m = DATE_RE.search(title)
            if m:
                # Only match actual council meetings, not committee meetings
                title_lower = title.lower()
                is_council = "council meeting" in title_lower
                is_committee = any(word in title_lower for word in [
                    "committee", "adjustment", "conservation", "museum", "recreation"
                ])
                if not is_council or is_committee:
                    print(f"  YouTube: skipping non-council video: {title}")
                    continue
                month, day, year = m.group(1), m.group(2), m.group(3)
                date_key = f"{month.capitalize()} {int(day)}, {year}"
                if date_key not in videos:
                    videos[date_key] = url
                    print(f"  YouTube: {date_key} -> {url}")
                else:
                    print(f"  YouTube: {date_key} (duplicate skipped)")
    except Exception as e:
        print(f"  Could not fetch YouTube RSS: {e}")

    state["_youtube_videos"] = {
        date: {"date": date, "url": url} for date, url in videos.items()
    }
    print(f"  Found {len(videos)} dated video(s) total")
    return videos


# ─────────────────────────────────────────────
#  SCRAPING
# ─────────────────────────────────────────────

def fetch_links():
    print(f"Fetching {SOURCE_URL} ...")
    resp = requests.get(SOURCE_URL, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    meetings = defaultdict(list)
    current_year = str(datetime.now().year)
    current_date = "Unknown date"

    DATE_RE = re.compile(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2},?\s+\d{4}",
        re.IGNORECASE
    )

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b", "a", "br"]):
        if tag.name in ("h1", "h2", "h3", "h4", "strong", "b"):
            text = tag.get_text(strip=True)
            year_match = re.search(r"\b(20\d{2})\b", text)
            if year_match:
                current_year = year_match.group(1)

        if tag.name == "a" and tag.get("href", "").endswith(".pdf"):
            href = tag["href"]
            raw_label = tag.get_text(strip=True)
            label = re.sub(r"^\(+|\)+$", "", raw_label).strip()

            parent = tag.parent
            if parent:
                line_text = ""
                for sib in tag.previous_siblings:
                    if hasattr(sib, 'name') and sib.name == "br":
                        break
                    if hasattr(sib, 'get_text'):
                        line_text = sib.get_text(" ") + " " + line_text
                    elif isinstance(sib, str):
                        line_text = sib + " " + line_text

                date_match = DATE_RE.search(line_text)
                if date_match:
                    raw_date = date_match.group(0).strip()
                    raw_date = re.sub(
                        r"(January|February|March|April|May|June|July|August|"
                        r"September|October|November|December)\s+(\d{1,2})\s+(\d{4})",
                        r"\1 \2, \3", raw_date
                    )
                    current_date = raw_date
                    if re.search(r"special meeting", line_text, re.IGNORECASE):
                        current_date = "Special Meeting " + raw_date

            meetings[current_year].append({
                "date": current_date,
                "label": label,
                "url": href,
                "filename": os.path.basename(urlparse(href).path),
            })

    for year in meetings:
        seen = set()
        unique = []
        for item in meetings[year]:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)
        meetings[year] = unique

    total = sum(len(v) for v in meetings.values())
    print(f"  Found {total} PDF links across {len(meetings)} years")
    return meetings


# ─────────────────────────────────────────────
#  DOWNLOADING
# ─────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def download_pdfs(meetings, state):
    new_count = 0
    for year, docs in meetings.items():
        year_files_dir = DOCS_DIR / year / "files"
        year_files_dir.mkdir(parents=True, exist_ok=True)
        for doc in docs:
            url = doc["url"]
            dest = year_files_dir / doc["filename"]
            if url in state:
                state[url]["label"] = doc["label"]
                state[url]["date"]  = doc["date"]
                state[url]["year"]  = year
            if url in state and dest.exists():
                continue
            print(f"  Downloading: {doc['filename']} ...")
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                dest.write_bytes(r.content)
                state[url] = {
                    "filename": doc["filename"],
                    "year": year,
                    "label": doc["label"],
                    "date": doc["date"],
                    "downloaded_at": datetime.now().isoformat(),
                }
                new_count += 1
                print(f"    ✓ Saved")
            except Exception as e:
                print(f"    ✗ Failed: {e}")
    print(f"  {new_count} new file(s) downloaded")
    return new_count


# ─────────────────────────────────────────────
#  AI SUMMARY
# ─────────────────────────────────────────────

def extract_pdf_text(path, max_chars=40000):
    if not PDF_EXTRACT:
        return ""
    try:
        buf = io.StringIO()
        with open(path, "rb") as f:
            extract_text_to_fp(f, buf, laparams=LAParams(), output_type="text", codec="utf-8")
        return buf.getvalue().strip()[:max_chars]
    except Exception as e:
        print(f"    PDF extract error: {e}")
        return ""

def generate_ai_summary(date_text, slots, year_files_dir):
    if not ANTHROPIC_API_KEY:
        return None

    combined_text = ""
    for doc_type, label in [("package", "Agenda Package"), ("minutes", "Minutes")]:
        for doc in slots.get(doc_type, []):
            path = year_files_dir / doc["filename"]
            if path.exists():
                text = extract_pdf_text(path)
                if text:
                    combined_text += f"\n\n=== {label} ===\n{text}"

    if not combined_text.strip():
        print(f"    No PDF text extracted, skipping summary")
        return None

    prompt = f"""You are summarizing a Nipissing Township Council meeting for a public archive.

Meeting date: {date_text}

Documents:
{combined_text[:35000]}

Provide a concise public summary with these sections:
1. **Key Decisions** — motions passed or defeated (bullet points)
2. **Main Topics** — what was discussed (bullet points)
3. **Notable Items** — significant spending, unusual items, or items of public interest

Keep it factual, neutral, and under 400 words. Plain language a resident would understand."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"    AI summary error: {e}")
        try:
            print(f"    Response body: {resp.text[:500]}")
        except:
            pass
        return None

def render_summary_html(summary_md):
    if not summary_md:
        return ""
    lines = summary_md.split("\n")
    html_lines = []
    in_ul = False
    for line in lines:
        line = line.strip()
        if not line:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if line.startswith("- ") or line.startswith("• "):
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            html_lines.append(f"<li>{line[2:].strip()}</li>")
        else:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            html_lines.append(f"<p>{line}</p>")
    if in_ul:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


# ─────────────────────────────────────────────
#  SHARED STYLES
# ─────────────────────────────────────────────

SHARED_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700;800&family=Lato:wght@300;400;700&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }

:root {
  --forest: #2C4A3E;
  --pine: #3D6B5E;
  --water: #5B9FAF;
  --sky: #A8D5E2;
  --sand: #F2EAD3;
  --warm: #E8C98A;
  --rust: #C06830;
  --charcoal: #1E2B2A;
  --cream: #FAF7F0;
  --white: #FFFFFF;
  --shadow: 0 2px 16px rgba(30,43,42,0.10);
}

body {
  font-family: 'Lato', Georgia, sans-serif;
  background: var(--cream);
  color: var(--charcoal);
  line-height: 1.6;
  overflow-x: hidden;
}

nav {
  position: fixed;
  top: 0; left: 0; right: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 2.5rem;
  background: rgba(44,74,62,0.97);
  backdrop-filter: blur(8px);
  box-shadow: 0 2px 20px rgba(0,0,0,0.2);
}
.nav-logo {
  font-family: 'Playfair Display', serif;
  font-size: 1.1rem;
  color: var(--warm);
  text-decoration: none;
}
.nav-logo span { color: var(--sky); }
.nav-links { display: flex; gap: 1.6rem; list-style: none; }
.nav-links a {
  color: rgba(255,255,255,0.85);
  text-decoration: none;
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  transition: color 0.2s;
}
.nav-links a:hover { color: var(--warm); }

.page-hero {
  background: var(--forest);
  padding: 7rem 2rem 3.5rem;
  border-bottom: 4px solid var(--warm);
  position: relative;
  overflow: hidden;
}
.page-hero::before {
  content: '';
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(
    -45deg, transparent, transparent 40px,
    rgba(255,255,255,0.015) 40px, rgba(255,255,255,0.015) 80px
  );
}
.page-hero .inner { max-width: 1100px; margin: 0 auto; position: relative; }
.page-hero .eyebrow {
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.25em;
  text-transform: uppercase; color: var(--warm); margin-bottom: 0.8rem;
  opacity: 0; animation: fadeUp 0.6s ease 0.1s forwards;
}
.page-hero h1 {
  font-family: 'Playfair Display', serif;
  font-size: clamp(2rem, 4vw, 3.2rem); font-weight: 800;
  color: var(--white); line-height: 1.1; margin-bottom: 0.75rem;
  opacity: 0; animation: fadeUp 0.6s ease 0.25s forwards;
}
.page-hero h1 em { font-style: normal; color: var(--warm); }
.page-hero p {
  font-size: 1rem; font-weight: 300; color: rgba(255,255,255,0.75);
  max-width: 580px; line-height: 1.75;
  opacity: 0; animation: fadeUp 0.6s ease 0.4s forwards;
}
.breadcrumb {
  display: flex; align-items: center; gap: 0.5rem; margin-top: 1.5rem;
  font-size: 0.78rem; color: rgba(255,255,255,0.45);
  opacity: 0; animation: fadeUp 0.6s ease 0.5s forwards;
}
.breadcrumb a { color: var(--sky); text-decoration: none; transition: color 0.2s; }
.breadcrumb a:hover { color: var(--warm); }
.breadcrumb .sep { opacity: 0.4; }

main { max-width: 1100px; margin: 3rem auto; padding: 0 2rem 5rem; }

.section-label {
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.25em;
  text-transform: uppercase; color: var(--rust); margin-bottom: 0.5rem;
}

.year-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 1.2rem; margin-top: 1.5rem;
}
.year-card {
  background: var(--white); border: 1px solid rgba(44,74,62,0.15);
  border-radius: 10px; padding: 1.75rem 1.5rem; text-decoration: none;
  color: var(--charcoal); box-shadow: var(--shadow);
  transition: border-color 0.2s, transform 0.15s, box-shadow 0.2s;
  display: flex; flex-direction: column; gap: 0.4rem;
  border-top: 3px solid var(--forest);
}
.year-card:hover {
  border-color: var(--warm); border-top-color: var(--rust);
  transform: translateY(-3px); box-shadow: 0 8px 28px rgba(30,43,42,0.14);
}
.year-card .yr {
  font-family: 'Playfair Display', serif; font-size: 2rem;
  font-weight: 800; color: var(--forest); line-height: 1;
}
.year-card .count { font-size: 0.8rem; font-weight: 700; color: #888; }

.notice {
  background: var(--sand); border-left: 4px solid var(--warm);
  padding: 1rem 1.5rem; border-radius: 0 8px 8px 0;
  margin-bottom: 2.5rem; font-size: 0.88rem; color: #666; line-height: 1.65;
}
.notice a { color: var(--forest); font-weight: 700; text-decoration: none; }
.notice a:hover { color: var(--rust); }

.year-nav { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 2rem; }
.year-nav a {
  font-size: 0.78rem; font-weight: 700; padding: 0.3rem 0.8rem;
  border-radius: 4px; background: var(--white);
  border: 1px solid rgba(44,74,62,0.2); color: var(--forest);
  text-decoration: none; transition: background 0.15s, color 0.15s;
}
.year-nav a:hover, .year-nav a.active {
  background: var(--forest); color: var(--white); border-color: var(--forest);
}

.meetings-table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; font-size: 0.88rem; }
.meetings-table thead tr { background: var(--forest); color: var(--white); }
.meetings-table thead th {
  padding: 0.75rem 1rem; text-align: left; font-family: 'Lato', sans-serif;
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.12em;
  text-transform: uppercase; white-space: nowrap;
}
.meetings-table thead th:first-child { border-radius: 8px 0 0 0; }
.meetings-table thead th:last-child  { border-radius: 0 8px 0 0; }
.meetings-table tbody tr {
  border-bottom: 1px solid rgba(44,74,62,0.1); transition: background 0.15s;
}
.meetings-table tbody tr:last-child { border-bottom: none; }
.meetings-table tbody tr:hover { background: rgba(44,74,62,0.04); }
.meetings-table tbody tr.special-row { background: rgba(192,104,48,0.04); }
.meetings-table tbody tr.special-row:hover { background: rgba(192,104,48,0.08); }
.meetings-table td { padding: 0.85rem 1rem; vertical-align: middle; }
.meetings-table td.date-cell {
  font-family: 'Playfair Display', serif; font-weight: 700;
  color: var(--forest); white-space: nowrap; min-width: 180px;
}
.meetings-table td.doc-cell { min-width: 100px; }

.date-link {
  color: var(--forest); text-decoration: none;
  border-bottom: 2px solid #e8d5a3;
  transition: border-color 0.15s, color 0.15s;
}
.date-link:hover { color: var(--rust); border-color: var(--rust); }

.doc-link {
  display: inline-flex; align-items: center; gap: 0.3rem;
  background: var(--cream); border: 1px solid rgba(44,74,62,0.18);
  border-radius: 4px; padding: 0.3rem 0.65rem; font-size: 0.8rem;
  font-weight: 700; color: var(--forest); text-decoration: none;
  white-space: nowrap; transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.doc-link:hover { background: var(--forest); color: var(--white); border-color: var(--forest); }
.doc-link.youtube { background: rgba(255,0,0,0.07); border-color: rgba(255,0,0,0.2); color: #c00; }
.doc-link.youtube:hover { background: #c00; color: var(--white); border-color: #c00; }
.doc-link svg { flex-shrink: 0; }
.extra-docs { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.no-doc { color: rgba(44,74,62,0.25); font-size: 0.8rem; }
.table-wrapper {
  background: var(--white); border: 1px solid rgba(44,74,62,0.12);
  border-radius: 10px; overflow: hidden; box-shadow: var(--shadow); overflow-x: auto;
}
.special-badge {
  display: inline-block; background: var(--rust); color: var(--white);
  font-family: 'Lato', sans-serif; font-size: 0.62rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.1em;
  padding: 0.18rem 0.55rem; border-radius: 3px;
}

/* Meeting page */
.meeting-hero-meta {
  display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 1rem;
  opacity: 0; animation: fadeUp 0.6s ease 0.5s forwards;
}
.meeting-hero-meta a {
  display: inline-flex; align-items: center; gap: 0.35rem;
  background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.2);
  border-radius: 5px; padding: 0.4rem 0.9rem; font-size: 0.82rem;
  font-weight: 700; color: var(--white); text-decoration: none; transition: background 0.15s;
}
.meeting-hero-meta a:hover { background: rgba(255,255,255,0.22); }
.meeting-hero-meta a.yt-btn { background: rgba(192,0,0,0.25); border-color: rgba(255,80,80,0.3); }
.meeting-hero-meta a.yt-btn:hover { background: rgba(192,0,0,0.45); }

.meeting-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 2rem; }
.meeting-card {
  background: var(--white); border: 1px solid rgba(44,74,62,0.12);
  border-top: 3px solid var(--pine); border-radius: 0 0 10px 10px;
  padding: 1.25rem 1.5rem; box-shadow: var(--shadow);
}
.meeting-card h3 {
  font-family: 'Playfair Display', serif; font-size: 0.9rem; font-weight: 700;
  color: var(--forest); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.75rem;
}
.meeting-card .doc-links { display: flex; flex-direction: column; gap: 0.4rem; }
.meeting-card .doc-link { justify-content: flex-start; }

.summary-card {
  background: var(--white); border: 1px solid rgba(44,74,62,0.12);
  border-top: 3px solid var(--rust); border-radius: 0 0 10px 10px;
  padding: 1.75rem 2rem; box-shadow: var(--shadow); margin-bottom: 2rem;
}
.summary-card h2 {
  font-family: 'Playfair Display', serif; font-size: 1.3rem; color: var(--forest);
  margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;
}
.summary-card p { font-size: 0.93rem; line-height: 1.8; color: #444; margin-bottom: 0.75rem; }
.summary-card strong { color: var(--forest); }
.summary-card ul { margin: 0.4rem 0 0.75rem 1.25rem; }
.summary-card li { font-size: 0.91rem; line-height: 1.75; color: #444; margin-bottom: 0.25rem; }
.ai-badge {
  font-size: 0.65rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  background: rgba(192,104,48,0.1); color: var(--rust); padding: 0.2rem 0.5rem; border-radius: 3px;
}

footer { background: var(--charcoal); padding: 2.5rem 2rem; text-align: center; }
footer .footer-inner {
  max-width: 1100px; margin: 0 auto; display: flex;
  flex-direction: column; align-items: center; gap: 0.75rem;
}
footer .footer-logo { font-family: 'Playfair Display', serif; font-size: 1.1rem; color: var(--warm); }
footer .footer-logo span { color: var(--sky); }
footer p { font-size: 0.8rem; color: rgba(255,255,255,0.35); }
footer a { color: var(--warm); text-decoration: none; }

@keyframes fadeUp {
  from { opacity: 0; transform: translateY(20px); }
  to   { opacity: 1; transform: translateY(0); }
}

@media (max-width: 700px) {
  nav { padding: 1rem 1.25rem; }
  .nav-links { gap: 0.8rem; }
  .nav-links a { font-size: 0.65rem; }
  .year-grid { grid-template-columns: repeat(2, 1fr); }
  main { padding: 0 1.25rem 4rem; }
  .meeting-grid { grid-template-columns: 1fr; }
  .meetings-table thead { display: none; }
  .meetings-table tbody tr { display: block; padding: 1rem; border-bottom: 2px solid rgba(44,74,62,0.1); }
  .meetings-table td { display: block; padding: 0.3rem 0; border: none; }
  .meetings-table td.date-cell { font-size: 1rem; margin-bottom: 0.5rem; }
}
"""

PDF_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
YT_ICON   = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M23 7s-.3-2-1.2-2.8c-1.1-1.2-2.4-1.2-3-1.3C16.4 2.8 12 2.8 12 2.8s-4.4 0-6.8.1c-.6.1-1.9.1-3 1.3C1.3 5 1 7 1 7S.7 9.1.7 11.3v2c0 2.1.3 4.3.3 4.3s.3 2 1.2 2.8c1.1 1.2 2.6 1.1 3.3 1.2C7.5 21.7 12 21.7 12 21.7s4.4 0 6.8-.2c.6-.1 1.9-.1 3-1.3.9-.8 1.2-2.8 1.2-2.8s.3-2.1.3-4.3v-2C23.3 9.1 23 7 23 7zM9.7 15.5V8.3l8.1 3.6-8.1 3.6z"/></svg>'


def nav_html():
    return """<nav>
  <a class="nav-logo" href="https://chriswjohnston.ca">Chris <span>Johnston</span></a>
  <ul class="nav-links">
    <li><a href="https://chriswjohnston.ca#priorities">Priorities</a></li>
    <li><a href="https://chriswjohnston.ca#issues">Issues</a></li>
    <li><a href="https://chriswjohnston.ca#contact">Contact</a></li>
    <li><a href="https://council.chriswjohnston.ca" style="color:var(--warm);">Council Archive</a></li>
  </ul>
</nav>"""

def footer_html():
    return f"""<footer>
  <div class="footer-inner">
    <div class="footer-logo">Chris <span>Johnston</span></div>
    <p>Candidate for Nipissing Township Council · Municipal Election October 2026</p>
    <p>Council archive sourced from <a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a>
    &mdash; Last updated {datetime.now().strftime("%B %d, %Y")}</p>
  </div>
</footer>"""


# ─────────────────────────────────────────────
#  HTML HELPERS
# ─────────────────────────────────────────────

def sort_date_key(date_str):
    for fmt in ("%B %d, %Y", "%B %d %Y", "Special Meeting %B %d, %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except:
            pass
    return datetime.min

def classify_doc(label):
    l = label.lower()
    if "agenda package" in l or "package" in l: return "package"
    if "agenda" in l:                            return "agenda"
    if "minute" in l:                            return "minutes"
    return "other"

def date_slug(date_text):
    clean = re.sub(r"^special meeting\s+", "", date_text, flags=re.IGNORECASE).strip()
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", clean).strip()
    return re.sub(r"\s+", "-", clean).lower()

def doc_button(label, filename, prefix="files/"):
    return (f'<a class="doc-link" href="{prefix}{filename}" '
            f'target="_blank" rel="noopener">{PDF_ICON} {label}</a>')

def yt_button(url):
    return (f'<a class="doc-link youtube" href="{url}" '
            f'target="_blank" rel="noopener">{YT_ICON} Watch</a>')

def get_yt_url(date_text, yt_videos):
    clean = re.sub(r"^special meeting\s+", "", date_text, flags=re.IGNORECASE).strip()
    return (YOUTUBE_VIDEOS.get(date_text) or YOUTUBE_VIDEOS.get(clean)
         or yt_videos.get(date_text) or yt_videos.get(clean))


# ─────────────────────────────────────────────
#  MEETING PAGE
# ─────────────────────────────────────────────

def generate_meeting_page(date_text, year, slots, yt_videos, summary):
    yt_url = get_yt_url(date_text, yt_videos)
    is_special = "special" in date_text.lower()
    badge = '<span class="special-badge">Special Meeting</span>' if is_special else ""

    meta_links = ""
    for d in slots.get("agenda", []):
        meta_links += f'<a href="../files/{d["filename"]}" target="_blank">{PDF_ICON} Agenda</a>'
    for d in slots.get("minutes", []):
        meta_links += f'<a href="../files/{d["filename"]}" target="_blank">{PDF_ICON} Minutes</a>'
    for d in slots.get("package", []):
        meta_links += f'<a href="../files/{d["filename"]}" target="_blank">{PDF_ICON} Agenda Package</a>'
    if yt_url:
        meta_links += f'<a href="{yt_url}" target="_blank" class="yt-btn">{YT_ICON} Watch Meeting</a>'
    else:
        meta_links += f'<a href="{YOUTUBE_CHANNEL}" target="_blank" class="yt-btn">{YT_ICON} YouTube Channel</a>'

    def doc_card(title, docs_list, border_color="var(--pine)"):
        if not docs_list:
            return ""
        links = "".join(
            f'<a class="doc-link" href="../files/{d["filename"]}" target="_blank" rel="noopener">{PDF_ICON} {d["label"]}</a>'
            for d in docs_list
        )
        return f"""<div class="meeting-card" style="border-top-color:{border_color}">
      <h3>{title}</h3>
      <div class="doc-links">{links}</div>
    </div>"""

    cards_html = (
        doc_card("Agenda", slots["agenda"], "var(--forest)") +
        doc_card("Minutes", slots["minutes"], "var(--pine)") +
        doc_card("Agenda Package", slots["package"], "var(--water)") +
        doc_card("Additional Files", slots["other"], "var(--warm)")
    )

    summary_html = ""
    if summary:
        summary_html = f"""<div class="summary-card">
      <h2>Meeting Summary <span class="ai-badge">AI Generated</span></h2>
      {render_summary_html(summary)}
      <p style="font-size:0.75rem;color:#aaa;margin-top:1rem;">Generated automatically from meeting documents. Always refer to official documents for authoritative information.</p>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{date_text} Council Meeting – Nipissing Township</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <style>{SHARED_CSS}</style>
</head>
<body>
{nav_html()}
<div class="page-hero">
  <div class="inner">
    <p class="eyebrow">Nipissing Township · Council Meeting</p>
    <h1>{date_text} {badge}</h1>
    <p>Official documents and summary for this council meeting.</p>
    <div class="meeting-hero-meta">{meta_links}</div>
    <div class="breadcrumb" style="margin-top:1rem;">
      <a href="../../">All Years</a>
      <span class="sep">/</span>
      <a href="../">{year}</a>
      <span class="sep">/</span>
      <span>{date_text}</span>
    </div>
  </div>
</div>
<main>
  {summary_html}
  <div class="meeting-grid">{cards_html}</div>
</main>
{footer_html()}
</body>
</html>"""


# ─────────────────────────────────────────────
#  YEAR PAGE
# ─────────────────────────────────────────────

def generate_year_page(year, docs, all_years, yt_videos={}, summaries={}):
    grouped = defaultdict(list)
    for doc in docs:
        grouped[doc["date"]].append(doc)

    sorted_dates = sorted(grouped.keys(), key=sort_date_key)

    active = 'class="active"'
    year_nav = "".join(
        f'<a href="../{y}/" {active if y == year else ""}>{y}</a>'
        for y in sorted(all_years, reverse=True)
    )

    rows_html = ""
    for date in sorted_dates:
        date_docs = grouped[date]
        is_special = "special" in date.lower() or any(
            "special" in d["filename"].lower() for d in date_docs
        )
        row_cls = ' class="special-row"' if is_special else ""
        badge   = '<span class="special-badge">Special</span>' if is_special else ""

        slots = {"agenda": [], "minutes": [], "package": [], "other": []}
        for d in date_docs:
            slots[classify_doc(d["label"])].append(d)

        def cell(docs_list):
            if not docs_list:
                return '<td class="doc-cell"><span class="no-doc">—</span></td>'
            buttons = " ".join(doc_button(d["label"], d["filename"]) for d in docs_list)
            return f'<td class="doc-cell">{buttons}</td>'

        other_parts = [doc_button(d["label"], d["filename"]) for d in slots["other"]]
        if other_parts:
            other_cell = f'<td class="doc-cell"><div class="extra-docs">{"".join(other_parts)}</div></td>'
        else:
            other_cell = '<td class="doc-cell"><span class="no-doc">—</span></td>'

        yt_url = get_yt_url(date, yt_videos)
        if yt_url:
            yt_cell = f'<td class="doc-cell">{yt_button(yt_url)}</td>'
        else:
            yt_cell = f'<td class="doc-cell"><a class="doc-link youtube" href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">{YT_ICON} Channel</a></td>'

        slug = date_slug(date)
        rows_html += f"""
      <tr{row_cls}>
        <td class="date-cell"><a href="{slug}/" class="date-link">{date}{badge}</a></td>
        {cell(slots["agenda"])}
        {cell(slots["minutes"])}
        {cell(slots["package"])}
        {other_cell}
        {yt_cell}
      </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{year} Council Meetings – Nipissing Township</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <style>{SHARED_CSS}</style>
</head>
<body>
{nav_html()}
<div class="page-hero">
  <div class="inner">
    <p class="eyebrow">Nipissing Township · Council Archive</p>
    <h1>{year} <em>Council Meetings</em></h1>
    <p>Agendas, Minutes &amp; Agenda Packages — preserved for the public record.</p>
    <div class="breadcrumb">
      <a href="../">All Years</a>
      <span class="sep">/</span>
      <span>{year}</span>
    </div>
  </div>
</div>
<main>
  <div class="notice">
    {len(docs)} documents archived for {year}. Sourced from
    <a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a>
    and preserved here before they are deleted. Videos via the
    <a href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">Township of Nipissing YouTube channel</a>.
    Last updated {datetime.now().strftime("%B %d, %Y")}.
  </div>
  <p class="section-label">Browse by year</p>
  <div class="year-nav" style="margin-bottom:1.5rem;">{year_nav}</div>
  <div class="table-wrapper">
    <table class="meetings-table">
      <thead>
        <tr>
          <th>Meeting Date</th>
          <th>Agenda</th>
          <th>Minutes</th>
          <th>Agenda Package</th>
          <th>Additional Files</th>
          <th>Video</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</main>
{footer_html()}
</body>
</html>"""


# ─────────────────────────────────────────────
#  INDEX PAGE
# ─────────────────────────────────────────────

def generate_index_page(meetings_by_year):
    years = sorted(meetings_by_year.keys(), reverse=True)
    total_docs = sum(len(v) for v in meetings_by_year.values())

    cards = "".join(f"""
    <a class="year-card" href="{year}/">
      <span class="yr">{year}</span>
      <span class="count">{len(meetings_by_year[year])} documents</span>
    </a>""" for year in years)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Council Meeting Archive – Nipissing Township</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <style>{SHARED_CSS}</style>
</head>
<body>
{nav_html()}
<div class="page-hero">
  <div class="inner">
    <p class="eyebrow">Nipissing Township</p>
    <h1>Council Meeting <em>Archive</em></h1>
    <p>Agendas, Minutes &amp; Agenda Packages — automatically preserved every two weeks.</p>
  </div>
</div>
<main>
  <div class="notice">
    <strong>{total_docs} documents</strong> preserved across <strong>{len(years)} year{"s" if len(years) != 1 else ""}</strong>.
    Automatically mirrored from
    <a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a>
    every two weeks. Documents are kept permanently here even after the township removes them.
    Videos via the <a href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">Township of Nipissing YouTube channel</a>.
  </div>
  <p class="section-label">Select a year</p>
  <div class="year-grid">{cards}</div>
</main>
{footer_html()}
</body>
</html>"""


# ─────────────────────────────────────────────
#  BUILD
# ─────────────────────────────────────────────

def build_html(meetings, yt_videos={}):
    print("\nGenerating HTML pages ...")
    DOCS_DIR.mkdir(exist_ok=True)
    all_years = list(meetings.keys())

    try:
        summaries = json.loads(SUMMARIES_FILE.read_text()) if SUMMARIES_FILE.exists() else {}
    except (json.JSONDecodeError, ValueError):
        summaries = {}

    for year, docs in meetings.items():
        year_dir = DOCS_DIR / year
        year_dir.mkdir(exist_ok=True)
        year_files_dir = year_dir / "files"

        grouped = defaultdict(list)
        for doc in docs:
            grouped[doc["date"]].append(doc)

        for date_text, date_docs in grouped.items():
            slug = date_slug(date_text)
            meeting_dir = year_dir / slug
            meeting_dir.mkdir(exist_ok=True)

            slots = {"agenda": [], "minutes": [], "package": [], "other": []}
            for d in date_docs:
                slots[classify_doc(d["label"])].append(d)

            summary_key = f"{year}/{slug}"
            summary = summaries.get(summary_key)

            # Only generate summary when BOTH minutes AND package are available
            has_both = bool(slots["minutes"] and slots["package"])
            if summary is None and has_both and ANTHROPIC_API_KEY:
                print(f"  Generating AI summary for {date_text} ...")
                summary = generate_ai_summary(date_text, slots, year_files_dir)
                if summary:
                    summaries[summary_key] = summary
                    SUMMARIES_FILE.write_text(json.dumps(summaries, indent=2))

            (meeting_dir / "index.html").write_text(
                generate_meeting_page(date_text, year, slots, yt_videos, summary),
                encoding="utf-8"
            )

        (year_dir / "index.html").write_text(
            generate_year_page(year, docs, all_years, yt_videos, summaries),
            encoding="utf-8"
        )
        print(f"  ✓ docs/{year}/index.html  ({len(docs)} docs)")

    (DOCS_DIR / "index.html").write_text(generate_index_page(meetings), encoding="utf-8")
    print(f"  ✓ docs/index.html")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Nipissing Council Archive — Scraper")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    state = load_state()
    yt_videos = fetch_youtube_videos(state)
    meetings = fetch_links()

    print("\nDownloading new PDFs ...")
    new_count = download_pdfs(meetings, state)
    save_state(state)

    build_html(meetings, yt_videos)

    print("\n✓ Done.")
    if new_count:
        print(f"  {new_count} new file(s) added.")
    else:
        print("  No new files found.")
