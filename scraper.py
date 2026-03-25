"""
Nipissing Township Council Meeting Scraper
==========================================
Scrapes https://nipissingtownship.com/council-meeting-dates-agendas-minutes/
Downloads new PDFs, organizes by year, and generates a static HTML archive site.

Output goes into docs/ which is served by GitHub Pages.
"""

import os
import re
import json
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime
from collections import defaultdict
from urllib.parse import urlparse

SOURCE_URL  = "https://nipissingtownship.com/council-meeting-dates-agendas-minutes/"
DOCS_DIR    = Path("docs")
STATE_FILE  = Path("state.json")

# ─────────────────────────────────────────────
#  SCRAPING
# ─────────────────────────────────────────────

def fetch_links():
    """Scrape all PDF links from the Nipissing council page, grouped by year."""
    print(f"Fetching {SOURCE_URL} ...")
    resp = requests.get(SOURCE_URL, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    meetings = defaultdict(list)
    current_year = str(datetime.now().year)

    content = soup.find("div", class_="entry-content") or soup.find("main") or soup.body

    for element in content.descendants:
        if element.name in ("h1", "h2", "h3", "h4"):
            text = element.get_text(strip=True)
            year_match = re.search(r"\b(20\d{2})\b", text)
            if year_match:
                current_year = year_match.group(1)

        if element.name == "a" and element.get("href", "").endswith(".pdf"):
            href = element["href"]
            label = element.get_text(strip=True)

            parent_text = element.parent.get_text(" ", strip=True) if element.parent else ""
            date_match = re.search(
                r"(Special Meeting\s+)?(January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+\d{1,2},?\s+\d{4}",
                parent_text
            )
            date_text = date_match.group(0).strip() if date_match else "Unknown date"

            url_year_match = re.search(r"/(\d{4})/", href)
            year = url_year_match.group(1) if url_year_match else current_year

            meetings[year].append({
                "date": date_text,
                "label": label,
                "url": href,
                "filename": os.path.basename(urlparse(href).path),
            })

    # Deduplicate by URL within each year
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
    """Download any PDFs not yet saved locally."""
    new_count = 0

    for year, docs in meetings.items():
        year_files_dir = DOCS_DIR / year / "files"
        year_files_dir.mkdir(parents=True, exist_ok=True)

        for doc in docs:
            url = doc["url"]
            dest = year_files_dir / doc["filename"]

            if url in state and dest.exists():
                continue  # already downloaded

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
                print(f"    ✗ Failed to download {doc['filename']}: {e}")

    print(f"  {new_count} new file(s) downloaded")
    return new_count


# ─────────────────────────────────────────────
#  HTML GENERATION
# ─────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=Source+Sans+3:wght@400;500;600&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --navy: #1a2744;
  --gold: #c9a84c;
  --gold-light: #e8d5a3;
  --cream: #faf8f3;
  --text: #2c3148;
  --muted: #6b7280;
  --border: #ddd8cc;
  --white: #ffffff;
  --shadow: 0 2px 12px rgba(26,39,68,0.08);
}

body {
  font-family: 'Source Sans 3', Georgia, serif;
  background: var(--cream);
  color: var(--text);
  line-height: 1.6;
}

header {
  background: var(--navy);
  color: var(--white);
  padding: 2.5rem 2rem;
  border-bottom: 4px solid var(--gold);
}

header .inner {
  max-width: 900px;
  margin: 0 auto;
}

header h1 {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 2rem;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.2;
}

header p {
  margin-top: 0.5rem;
  color: var(--gold-light);
  font-size: 0.95rem;
}

header a.back {
  color: var(--gold-light);
  text-decoration: none;
  font-size: 0.9rem;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 1rem;
  opacity: 0.8;
  transition: opacity 0.2s;
}
header a.back:hover { opacity: 1; }

main {
  max-width: 900px;
  margin: 2.5rem auto;
  padding: 0 1.5rem 4rem;
}

.notice {
  background: var(--white);
  border-left: 4px solid var(--gold);
  padding: 1rem 1.25rem;
  border-radius: 0 8px 8px 0;
  margin-bottom: 2rem;
  font-size: 0.9rem;
  color: var(--muted);
}
.notice a { color: var(--navy); }

/* Index page */
.year-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 1rem;
  margin-top: 0.5rem;
}

.year-card {
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.5rem 1.25rem;
  text-decoration: none;
  color: var(--text);
  box-shadow: var(--shadow);
  transition: border-color 0.2s, transform 0.15s, box-shadow 0.2s;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.year-card:hover {
  border-color: var(--gold);
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(26,39,68,0.12);
}
.year-card .yr {
  font-family: 'Playfair Display', serif;
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--navy);
  line-height: 1;
}
.year-card .count {
  font-size: 0.82rem;
  color: var(--muted);
}

/* Year page */
.meeting-block {
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.25rem 1.5rem;
  margin-bottom: 1rem;
  box-shadow: var(--shadow);
}

.meeting-date {
  font-family: 'Playfair Display', serif;
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--navy);
  margin-bottom: 0.75rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.special-badge {
  display: inline-block;
  background: var(--gold);
  color: var(--navy);
  font-family: 'Source Sans 3', sans-serif;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0.15rem 0.5rem;
  border-radius: 3px;
}

.doc-links {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.doc-link {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  background: var(--cream);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.4rem 0.8rem;
  font-size: 0.875rem;
  color: var(--navy);
  text-decoration: none;
  font-weight: 500;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.doc-link:hover {
  background: var(--navy);
  color: var(--white);
  border-color: var(--navy);
}
.doc-link svg { flex-shrink: 0; }

footer {
  text-align: center;
  padding: 2rem;
  color: var(--muted);
  font-size: 0.82rem;
  border-top: 1px solid var(--border);
}
footer a { color: var(--muted); }

@media (max-width: 600px) {
  header h1 { font-size: 1.5rem; }
  .year-grid { grid-template-columns: repeat(2, 1fr); }
}
"""

PDF_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'

def sort_date_key(date_str):
    for fmt in ("%B %d, %Y", "%B %d %Y", "Special Meeting %B %d, %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except:
            pass
    return datetime.min

def generate_year_page(year, docs, all_years):
    grouped = defaultdict(list)
    for doc in docs:
        grouped[doc["date"]].append(doc)

    sorted_dates = sorted(grouped.keys(), key=sort_date_key, reverse=True)

    meetings_html = ""
    for date in sorted_dates:
        date_docs = grouped[date]
        is_special = "special" in date.lower() or any(
            "special" in d["filename"].lower() for d in date_docs
        )
        badge = '<span class="special-badge">Special Meeting</span>' if is_special else ""

        links_html = "".join(
            f'<a class="doc-link" href="files/{d["filename"]}" target="_blank">{PDF_ICON} {d["label"]}</a>\n'
            for d in date_docs
        )

        meetings_html += f"""
    <div class="meeting-block">
      <div class="meeting-date">{date} {badge}</div>
      <div class="doc-links">{links_html}</div>
    </div>"""

    other_years = " &nbsp;·&nbsp; ".join(
        f'<a href="../{y}/">{y}</a>'
        for y in sorted(all_years, reverse=True) if y != year
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{year} Council Meetings – Nipissing Township Archive</title>
  <style>{CSS}</style>
</head>
<body>
  <header>
    <div class="inner">
      <h1>{year} Council Meetings</h1>
      <p>Agendas, Minutes &amp; Agenda Packages &mdash; Nipissing Township</p>
      <a class="back" href="../">&#8592; All Years</a>
    </div>
  </header>
  <main>
    <div class="notice">
      {len(docs)} documents archived for {year}. Sourced from
      <a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a>.
      &nbsp;Other years: {other_years}
    </div>
    {meetings_html}
  </main>
  <footer>
    <p>Archived from <a href="{SOURCE_URL}" target="_blank">nipissingtownship.com</a>
    &mdash; Last updated {datetime.now().strftime("%B %d, %Y")}</p>
  </footer>
</body>
</html>"""

def generate_index_page(meetings_by_year):
    years = sorted(meetings_by_year.keys(), reverse=True)
    total_docs = sum(len(v) for v in meetings_by_year.values())

    cards = "".join(f"""
    <a class="year-card" href="{year}/">
      <span class="yr">{year}</span>
      <span class="count">{len(meetings_by_year[year])} document{"s" if len(meetings_by_year[year]) != 1 else ""}</span>
    </a>""" for year in years)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Council Meeting Archive – Nipissing Township</title>
  <style>{CSS}</style>
</head>
<body>
  <header>
    <div class="inner">
      <h1>Council Meeting Archive</h1>
      <p>Nipissing Township &mdash; Agendas, Minutes &amp; Agenda Packages</p>
    </div>
  </header>
  <main>
    <div class="notice">
      {total_docs} documents preserved across {len(years)} year{"s" if len(years) != 1 else ""}.
      Automatically mirrored from
      <a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a>
      every two weeks to preserve records before they are deleted.
    </div>
    <div class="year-grid">{cards}</div>
  </main>
  <footer>
    <p>Archived from <a href="{SOURCE_URL}" target="_blank">nipissingtownship.com</a>
    &mdash; Last updated {datetime.now().strftime("%B %d, %Y")}</p>
  </footer>
</body>
</html>"""

def build_html(meetings):
    print("\nGenerating HTML pages ...")
    DOCS_DIR.mkdir(exist_ok=True)
    all_years = list(meetings.keys())

    for year, docs in meetings.items():
        year_dir = DOCS_DIR / year
        year_dir.mkdir(exist_ok=True)
        (year_dir / "index.html").write_text(
            generate_year_page(year, docs, all_years), encoding="utf-8"
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
    meetings = fetch_links()

    print("\nDownloading new PDFs ...")
    new_count = download_pdfs(meetings, state)
    save_state(state)

    build_html(meetings)

    print("\n✓ Done.")
    if new_count:
        print(f"  {new_count} new file(s) added — GitHub Actions will commit and deploy.")
    else:
        print("  No new files found.")
