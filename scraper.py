"""
Nipissing Township Council Meeting Archive — scraper.py
=======================================================
Generates a static HTML site in docs/ for GitHub Pages.

Sources:
  1. Main page PDFs (2024+)        — scraped directly
  2. 2023 PDFs                     — loaded from state.json (pre-discovered)
  3. Older HTML pages (2018-2022)  — fetched via WordPress REST API + known list
  4. YouTube videos                — via channel RSS feed

Environment variables:
  ANTHROPIC_API_KEY   — enables AI meeting summaries (optional)
  BRANCH              — "public" strips campaign nav; default shows campaign nav

Run time target: < 5 minutes on GitHub Actions (cold)
"""

import os, re, io, json, requests, xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from collections import defaultdict
from urllib.parse import urlparse
from email.utils import parsedate_to_datetime

try:
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    PDF_EXTRACT = True
except ImportError:
    PDF_EXTRACT = False

# ── Config ─────────────────────────────────────────────────────────────────
SOURCE_URL         = "https://nipissingtownship.com/council-meeting-dates-agendas-minutes/"
YOUTUBE_CHANNEL    = "https://www.youtube.com/@townshipofnipissing505/streams"
YOUTUBE_CHANNEL_ID = "UC2XSMZqRNHbwVppelfKcEXw"

# Hardcoded known videos — permanent fallback so they're never lost
# Add new ones here manually if RSS stops picking them up
KNOWN_YOUTUBE_VIDEOS = {
    # 2022
    "January 11, 2022":   "https://www.youtube.com/watch?v=TfBrPMmVnrM",
    "January 25, 2022":   "https://www.youtube.com/watch?v=nX9sYSVgiyU",
    "February 8, 2022":   "https://www.youtube.com/watch?v=fSmpIfJf7hU",
    "February 22, 2022":  "https://www.youtube.com/watch?v=HiNQaF_XMTQ",
    "March 8, 2022":      "https://www.youtube.com/watch?v=CKpUjh2Tz_Y",
    "March 15, 2022":     "https://www.youtube.com/watch?v=rIBBVRMlniE",
    "March 22, 2022":     "https://www.youtube.com/watch?v=LLxV_i6XKWU",
    "April 5, 2022":      "https://www.youtube.com/watch?v=YRX52Nt0j3A",
    "April 19, 2022":     "https://www.youtube.com/watch?v=5jlKlOp-b3g",
    "April 26, 2022":     "https://www.youtube.com/watch?v=fFVWNwOcI9o",
    "May 10, 2022":       "https://www.youtube.com/watch?v=f-LjGOUiTfQ",
    "May 24, 2022":       "https://www.youtube.com/watch?v=7ImmC8TCWKM",
    "June 14, 2022":      "https://www.youtube.com/watch?v=Vgs9r1sJHAc",
    "June 28, 2022":      "https://www.youtube.com/watch?v=Ky7QK1hLyBo",
    "July 12, 2022":      "https://www.youtube.com/watch?v=hKJ9y_RRq84",
    "August 9, 2022":     "https://www.youtube.com/watch?v=sElXRJ_WqM4",
    "August 23, 2022":    "https://www.youtube.com/watch?v=JHWJUcBaXJY",
    "September 13, 2022": "https://www.youtube.com/watch?v=sCFvFkMWqhY",
    "September 27, 2022": "https://www.youtube.com/watch?v=yqFWk-OuZ2Q",
    "October 4, 2022":    "https://www.youtube.com/watch?v=3LWbPO3bBWM",
    "October 18, 2022":   "https://www.youtube.com/watch?v=MiKTfEQ0e0Y",
    "November 1, 2022":   "https://www.youtube.com/watch?v=nFPtknOxoT0",
    "November 22, 2022":  "https://www.youtube.com/watch?v=2RpWRcSlCFI",
    "December 6, 2022":   "https://www.youtube.com/watch?v=DP8hc8MtLRA",
    "December 20, 2022":  "https://www.youtube.com/watch?v=RCOlkB5Gt3c",
    # 2023
    "March 21, 2023":     "https://www.youtube.com/watch?v=kippOBqmwfk",
    "April 4, 2023":      "https://www.youtube.com/watch?v=7EbdoWEbwxQ",
    "April 18, 2023":     "https://www.youtube.com/watch?v=TXOFlTFtUUc",
    "May 2, 2023":        "https://www.youtube.com/watch?v=BZzIz4qSD9g",
    "May 16, 2023":       "https://www.youtube.com/watch?v=XKciD82AmcE",
    "June 20, 2023":      "https://www.youtube.com/watch?v=v8Na_8nEg5Q",
    "October 3, 2023":    "https://www.youtube.com/watch?v=U5KCHDjMARM",
    "November 14, 2023":  "https://www.youtube.com/watch?v=5M_sG23RnJE",
    "December 5, 2023":   "https://www.youtube.com/watch?v=-G4mDYXQUA4",
    "December 19, 2023":  "https://www.youtube.com/watch?v=w-ojRVeF_-4",
    # 2024
    "January 10, 2024":   "https://www.youtube.com/watch?v=v5HtehqR3mU",
    "January 16, 2024":   "https://www.youtube.com/watch?v=dbsFvOEIpCE",
    "February 6, 2024":   "https://www.youtube.com/watch?v=rIcfa30oKzA",
    "March 19, 2024":     "https://www.youtube.com/watch?v=VtYFN3mL8UI",
    "April 2, 2024":      "https://www.youtube.com/watch?v=OWEucjlHuB8",
    "April 16, 2024":     "https://www.youtube.com/watch?v=rjBt6-YKf1E",
    "June 4, 2024":       "https://www.youtube.com/watch?v=UCkolSuqleg",
    "June 24, 2024":      "https://www.youtube.com/watch?v=A9o88wa5tDA",
    "July 16, 2024":      "https://www.youtube.com/watch?v=Gz41pUfaWtc",
    "September 17, 2024": "https://www.youtube.com/watch?v=kgAtYdDfTfY",
    "October 1, 2024":    "https://www.youtube.com/watch?v=s7UOXkc2dN8",
    "December 3, 2024":   "https://www.youtube.com/watch?v=Nt6tbnD0_Ao",
    "December 17, 2024":  "https://www.youtube.com/watch?v=XJUNGhAd-oM",
    "December 19, 2024":  "https://www.youtube.com/watch?v=9NRPQpCA9Rw",
    # 2025
    "January 7, 2025":    "https://www.youtube.com/watch?v=qXkA365W-K4",
    "January 21, 2025":   "https://www.youtube.com/watch?v=wz5TF8DTOI0",
    "February 4, 2025":   "https://www.youtube.com/watch?v=wQsO0bwgaLA",
    "February 18, 2025":  "https://www.youtube.com/watch?v=q-27WqKXd7k",
    "March 4, 2025":      "https://www.youtube.com/watch?v=mvqHbEWpB7Y",
    "March 18, 2025":     "https://www.youtube.com/watch?v=cRiBjaHgm6o",
    "April 8, 2025":      "https://www.youtube.com/watch?v=8CQB010m1xY",
    "May 13, 2025":       "https://www.youtube.com/watch?v=z7wRNoSr7G8",
    "May 27, 2025":       "https://www.youtube.com/watch?v=A5TU4_9KMrI",
    "June 10, 2025":      "https://www.youtube.com/watch?v=hiPxt0g7fvI",
    "June 24, 2025":      "https://www.youtube.com/watch?v=A9o88wa5tDA",
    "July 15, 2025":      "https://www.youtube.com/watch?v=iqEmLVk6XHQ",
    "August 12, 2025":    "https://www.youtube.com/watch?v=WLOhWsICLN0",
    "September 2, 2025":  "https://www.youtube.com/watch?v=HUPTQajeFWk",
    "September 16, 2025": "https://www.youtube.com/watch?v=mtgCDDJigjI",
    "October 7, 2025":    "https://www.youtube.com/watch?v=GmfEZJbNOKY",
    "October 28, 2025":   "https://www.youtube.com/watch?v=wi6xJN35aRo",
    "November 18, 2025":  "https://www.youtube.com/watch?v=tNtaUpFm4DQ",
    "December 2, 2025":   "https://www.youtube.com/watch?v=Zed4sZNUBfo",
    "December 16, 2025":  "https://www.youtube.com/watch?v=_idoxh5wKLk",
    # 2026
    "January 6, 2026":    "https://www.youtube.com/watch?v=B-JeDGKD4GU",
    "January 20, 2026":   "https://www.youtube.com/watch?v=aHJOVza17GM",
    "February 3, 2026":  "https://www.youtube.com/watch?v=zEZK_BNVS4I",
    "February 17, 2026": "https://www.youtube.com/watch?v=PHK0uaveLEk",
    "March 3, 2026":     "https://www.youtube.com/watch?v=OGlKpjmXUwM",
    "March 17, 2026":    "https://www.youtube.com/watch?v=mi40epWqO_s",
}
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
BRANCH             = os.environ.get("BRANCH", "campaign").lower()
IS_PUBLIC          = BRANCH == "public"

DOCS_DIR       = Path("docs")
STATE_FILE     = Path("state.json")
SUMMARIES_FILE = Path("summaries.json")
HTML_CACHE     = Path("html_page_cache.json")

# ── Known 2023 PDFs (pre-discovered to avoid slow brute-force) ─────────────
# These are stored in state.json after first discovery — this list is the
# initial seed so we never need to brute-force again.
KNOWN_2023 = [
    ("January 3, 2023",    "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/01/Minutes-January-3-2023.pdf"),
    ("January 3, 2023",    "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/01/Agenda-January-3-2023.pdf"),
    ("January 17, 2023",   "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/01/Minutes-January-17-2023.pdf"),
    ("January 17, 2023",   "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/01/Agenda-January-17-2023.pdf"),
    ("February 7, 2023",   "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/02/Minutes-February-7-2023.pdf"),
    ("February 7, 2023",   "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/02/Agenda-February-7-2023.pdf"),
    ("February 21, 2023",  "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/02/Minutes-February-21-2023.pdf"),
    ("February 21, 2023",  "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/02/Agenda-February-21-2023.pdf"),
    ("March 7, 2023",      "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/03/Minutes-March-7-2023.pdf"),
    ("March 21, 2023",     "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/03/Minutes-March-21-2023.pdf"),
    ("March 21, 2023",     "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/03/Agenda-March-21-2023.pdf"),
    ("April 4, 2023",      "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/04/Minutes-April-4-2023.pdf"),
    ("April 18, 2023",     "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/04/Minutes-April-18-2023.pdf"),
    ("April 18, 2023",     "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/04/Agenda-April-18-2023.pdf"),
    ("May 2, 2023",        "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/05/Minutes-May-2-2023.pdf"),
    ("May 16, 2023",       "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/05/Minutes-May-16-2023.pdf"),
    ("May 16, 2023",       "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/05/May-16-2023-Agenda.pdf"),
    ("June 6, 2023",       "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/06/Minutes-June-6-2023.pdf"),
    ("June 20, 2023",      "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/06/Minutes-June-20-2023.pdf"),
    ("June 20, 2023",      "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/06/June-20-2023-Agenda.pdf"),
    ("July 11, 2023",      "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/07/Minutes-July-11-2023.pdf"),
    ("August 15, 2023",    "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/08/Minutes-August-15-2023.pdf"),
    ("September 5, 2023",  "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/09/Minutes-September-5-2023.pdf"),
    ("September 19, 2023", "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/09/Minutes-September-19-2023.pdf"),
    ("October 3, 2023",    "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/10/Minutes-October-3-2023.pdf"),
    ("October 17, 2023",   "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/10/Minutes-October-17-2023.pdf"),
    ("November 14, 2023",  "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/11/Minutes-November-14-2023.pdf"),
    ("December 5, 2023",   "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/12/Minutes-December-5-2023.pdf"),
    ("December 19, 2023",  "Minutes",       "https://nipissingtownship.com/wp-content/uploads/2023/12/Minutes-December-19-2023.pdf"),
    ("December 19, 2023",  "Agenda",        "https://nipissingtownship.com/wp-content/uploads/2023/12/Agenda-December-19-2023.pdf"),
]

# ── Known older HTML pages (2018–2022) ─────────────────────────────────────
BASE_HTML = "https://nipissingtownship.com/council-meeting-dates-agendas-minutes/"
KNOWN_HTML_PAGES = [
    # 2022
    f"{BASE_HTML}may-10-2022-minutes/",
    f"{BASE_HTML}special-meeting-minutes/",
    # 2021 minutes
    f"{BASE_HTML}minutes-january-19-2021/", f"{BASE_HTML}minutes-february-2-2021/",
    f"{BASE_HTML}minutes-february-16-2021/", f"{BASE_HTML}minutes-march-2-2021/",
    f"{BASE_HTML}minutes-march-16-2021/", f"{BASE_HTML}minutes-april-6-2021/",
    f"{BASE_HTML}minutes-april-20-2021/", f"{BASE_HTML}minutes-may-4-2021/",
    f"{BASE_HTML}minutes-may-18-2021/", f"{BASE_HTML}minutes-june-1-2021/",
    f"{BASE_HTML}minutes-june-8-2021/", f"{BASE_HTML}minutes-june-22-2021/",
    f"{BASE_HTML}minutes-july-13-2021/", f"{BASE_HTML}minutes-august-3-2021/",
    f"{BASE_HTML}minutes-august-17-2021/", f"{BASE_HTML}minutes-september-7-2021/",
    f"{BASE_HTML}minutes-september-21-2021/", f"{BASE_HTML}minutes-october-5-2021/",
    f"{BASE_HTML}minutes-october-19-2021/", f"{BASE_HTML}minutes-november-2-2021/",
    f"{BASE_HTML}minutes-november-16-2021/", f"{BASE_HTML}minutes-december-7-2021/",
    f"{BASE_HTML}minutes-december-21-2021/",
    f"{BASE_HTML}minutes-special-meeting-march-9-2021/",
    # 2021 agendas
    f"{BASE_HTML}agenda-january-19-2021/", f"{BASE_HTML}agenda-february-2-2021/",
    f"{BASE_HTML}agenda-february-16-2021/", f"{BASE_HTML}agenda-march-2-2021/",
    f"{BASE_HTML}agenda-march-16-2021/", f"{BASE_HTML}agenda-april-6-2021/",
    f"{BASE_HTML}agenda-april-20-2021/", f"{BASE_HTML}agenda-may-18-2021/",
    f"{BASE_HTML}agenda-june-1-2021/", f"{BASE_HTML}agenda-june-22-2021/",
    f"{BASE_HTML}agenda-july-13-2021/", f"{BASE_HTML}agenda-august-17-2021/",
    f"{BASE_HTML}agenda-september-7-2021/", f"{BASE_HTML}agenda-september-21-2021/",
    f"{BASE_HTML}agenda-october-5-2021/", f"{BASE_HTML}agenda-october-19-2021/",
    f"{BASE_HTML}agenda-november-2-2021/", f"{BASE_HTML}agenda-november-16-2021/",
    f"{BASE_HTML}agenda-december-7-2021/", f"{BASE_HTML}agenda-december-21-2021/",
    # 2020 minutes
    f"{BASE_HTML}minutes-january-21-2020/", f"{BASE_HTML}minutes-february-4-2020/",
    f"{BASE_HTML}minutes-february-18-2020/", f"{BASE_HTML}minutes-march-10-2020/",
    f"{BASE_HTML}minutes-march-17-2020/", f"{BASE_HTML}minutes-april-21-2020/",
    f"{BASE_HTML}minutes-may-5-2020/", f"{BASE_HTML}minutes-may-19-2020/",
    f"{BASE_HTML}minutes-june-2-2020/", f"{BASE_HTML}minutes-june-16-2020/",
    f"{BASE_HTML}minutes-july-7-2020/", f"{BASE_HTML}minutes-july-21-2020/",
    f"{BASE_HTML}minutes-august-4-2020/", f"{BASE_HTML}minutes-august-18-2020/",
    f"{BASE_HTML}minutes-september-1-2020/", f"{BASE_HTML}minutes-september-15-2020/",
    f"{BASE_HTML}minutes-october-6-2020/", f"{BASE_HTML}minutes-october-20-2020/",
    f"{BASE_HTML}minutes-november-3-2020/", f"{BASE_HTML}minutes-november-17-2020/",
    f"{BASE_HTML}minutes-december-1-2020/", f"{BASE_HTML}minutes-december-15-2020/",
    # 2020 agendas
    f"{BASE_HTML}agenda-march-10-2020/",
    # 2019 minutes
    f"{BASE_HTML}minutes-january-8-2019/", f"{BASE_HTML}minutes-january-22-2019/",
    f"{BASE_HTML}minutes-february-5-2019/", f"{BASE_HTML}minutes-february-19-2019/",
    f"{BASE_HTML}minutes-march-5-2019/", f"{BASE_HTML}minutes-march-19-2019/",
    f"{BASE_HTML}minutes-april-2-2019/", f"{BASE_HTML}minutes-april-16-2019/",
    f"{BASE_HTML}minutes-april-30-2019/", f"{BASE_HTML}minutes-may-14-2019/",
    f"{BASE_HTML}minutes-may-28-2019/", f"{BASE_HTML}minutes-june-11-2019/",
    f"{BASE_HTML}minutes-june-25-2019/", f"{BASE_HTML}minutes-july-9-2019/",
    f"{BASE_HTML}minutes-july-23-2019/", f"{BASE_HTML}minutes-august-13-2019/",
    f"{BASE_HTML}minutes-august-27-2019/", f"{BASE_HTML}minutes-september-10-2019/",
    f"{BASE_HTML}minutes-september-24-2019/", f"{BASE_HTML}minutes-october-8-2019/",
    f"{BASE_HTML}minutes-october-22-2019/", f"{BASE_HTML}minutes-november-5-2019/",
    f"{BASE_HTML}minutes-november-19-2019/", f"{BASE_HTML}minutes-december-3-2019/",
    f"{BASE_HTML}minutes-december-17-2019/",
    # 2018 minutes
    f"{BASE_HTML}minutes-january-16-2018/", f"{BASE_HTML}minutes-february-6-2018/",
    f"{BASE_HTML}minutes-february-20-2018/", f"{BASE_HTML}minutes-march-6-2018/",
    f"{BASE_HTML}minutes-march-20-2018/", f"{BASE_HTML}minutes-april-3-2018/",
    f"{BASE_HTML}minutes-april-17-2018/", f"{BASE_HTML}minutes-may-1-2018/",
    f"{BASE_HTML}minutes-may-15-2018/", f"{BASE_HTML}minutes-june-5-2018/",
    f"{BASE_HTML}minutes-june-19-2018/", f"{BASE_HTML}minutes-july-10-2018/",
    f"{BASE_HTML}minutes-july-24-2018/", f"{BASE_HTML}minutes-august-7-2018/",
    f"{BASE_HTML}minutes-august-21-2018/", f"{BASE_HTML}minutes-september-4-2018/",
    f"{BASE_HTML}minutes-september-18-2018/", f"{BASE_HTML}minutes-october-2-2018/",
    f"{BASE_HTML}minutes-october-16-2018/", f"{BASE_HTML}minutes-november-6-2018/",
    f"{BASE_HTML}minutes-november-20-2018/", f"{BASE_HTML}minutes-december-4-2018/",
    f"{BASE_HTML}minutes-december-18-2018/",
    # Historic
    f"{BASE_HTML}minutes-town-hall-meeting-strategic-plan/",
]

DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},?\s+\d{4}", re.IGNORECASE
)


# ── State ──────────────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text())
        except: pass
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def load_cache():
    if HTML_CACHE.exists():
        try: return json.loads(HTML_CACHE.read_text())
        except: pass
    return {"not_found": [], "fetched": {}}

def save_cache(cache):
    HTML_CACHE.write_text(json.dumps(cache, indent=2))


# ── YouTube RSS ────────────────────────────────────────────────────────────

def fetch_youtube_videos(state):
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
    # Start with hardcoded known videos (never lost even if state is reset)
    videos = dict(KNOWN_YOUTUBE_VIDEOS)
    # Merge in anything previously cached in state (additive — never overwrite)
    for v in state.get("_youtube_videos", {}).values():
        if v["date"] not in videos:
            videos[v["date"]] = v["url"]
    print(f"YouTube RSS ({len(videos)} known + cached)...")
    try:
        resp = requests.get(rss_url, timeout=15)
        resp.raise_for_status()
        NS = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(resp.content)
        for entry in root.findall("atom:entry", NS):
            title_el = entry.find("atom:title", NS)
            link_el  = entry.find("atom:link",  NS)
            if not title_el or not link_el: continue
            title = title_el.text or ""
            url   = link_el.get("href", "")
            tl    = title.lower()
            # Skip non-council videos
            if any(w in tl for w in ["committee","adjustment","museum","recreation",
                                      "strategic plan","town hall"]): continue
            # Must either say "council" or "nipissing" with a date — avoids random videos
            if "council" not in tl and "nipissing" not in tl: continue
            m = re.search(
                r"(January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
                title, re.IGNORECASE
            )
            if m:
                dk = f"{m.group(1).capitalize()} {int(m.group(2))}, {m.group(3)}"
                if dk not in videos:
                    videos[dk] = url
                    print(f"  + YouTube: {dk}")
    except Exception as e:
        print(f"  YouTube error: {e}")
    state["_youtube_videos"] = {d: {"date": d, "url": u} for d, u in videos.items()}
    return videos


# ── PDF scraping (main page) ───────────────────────────────────────────────

def normalise_date(raw):
    raw = raw.strip()
    return re.sub(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{1,2})\s+(\d{4})",
        r"\1 \2, \3", raw
    )

def fetch_pdf_links():
    """Scrape PDFs directly linked from the main council page."""
    print("Scraping main page PDFs...")
    resp = requests.get(SOURCE_URL, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    meetings = defaultdict(list)
    current_year = str(datetime.now().year)
    current_date = "Unknown"

    for tag in soup.find_all(["h1","h2","h3","h4","strong","b","a","br"]):
        if tag.name in ("h1","h2","h3","h4","strong","b"):
            ym = re.search(r"\b(20\d{2})\b", tag.get_text(strip=True))
            if ym: current_year = ym.group(1)
        if tag.name == "a" and tag.get("href","").endswith(".pdf"):
            href  = tag["href"]
            label = re.sub(r"^\(+|\)+$", "", tag.get_text(strip=True)).strip()
            line  = ""
            if tag.parent:
                for sib in tag.previous_siblings:
                    if hasattr(sib,"name") and sib.name == "br": break
                    line = (sib.get_text(" ") if hasattr(sib,"get_text") else str(sib)) + " " + line
            dm = DATE_RE.search(line)
            if dm:
                raw = normalise_date(dm.group(0))
                current_date = ("Special Meeting " + raw) if re.search(r"special", line, re.IGNORECASE) else raw
            meetings[current_year].append({
                "date": current_date, "label": label,
                "url": href, "filename": os.path.basename(urlparse(href).path),
                "type": "pdf",
            })

    # Deduplicate
    for yr in meetings:
        seen, uniq = set(), []
        for d in meetings[yr]:
            if d["url"] not in seen:
                seen.add(d["url"])
                uniq.append(d)
        meetings[yr] = uniq

    total = sum(len(v) for v in meetings.values())
    print(f"  {total} PDFs across {len(meetings)} years")
    return meetings


# ── 2023 PDFs (from state or known list — no brute-force) ─────────────────

def get_2023_meetings(state):
    """Load 2023 meetings from state (seeded from KNOWN_2023 on first run)."""
    cached = state.get("_2023_meetings", [])
    if cached:
        print(f"2023: {len(cached)} meetings from cache")
        return cached

    print("2023: seeding from known list...")
    docs = []
    for date, label, url in KNOWN_2023:
        docs.append({
            "date": date, "label": label,
            "url": url, "filename": os.path.basename(urlparse(url).path),
            "type": "pdf",
        })
    state["_2023_meetings"] = docs
    print(f"  {len(docs)} 2023 documents seeded")
    return docs


# ── HTML page scraping (2018–2022) ─────────────────────────────────────────

def fetch_html_pages(state):
    """
    Fetch older council meeting HTML pages.
    Uses cache so already-fetched pages aren't re-fetched.
    Only fetches pages not yet in cache.
    """
    cache = load_cache()
    known_404s   = set(cache.get("not_found", []))
    fetched_data = cache.get("fetched", {})  # url -> doc dict

    # Discover pages via WordPress REST API
    candidate_urls = set(KNOWN_HTML_PAGES)
    try:
        r = requests.get(
            "https://nipissingtownship.com/wp-json/wp/v2/pages",
            params={"slug": "council-meeting-dates-agendas-minutes", "_fields": "id"},
            timeout=10
        )
        if r.status_code == 200 and r.json():
            parent_id = r.json()[0]["id"]
            page_num = 1
            while True:
                cr = requests.get(
                    "https://nipissingtownship.com/wp-json/wp/v2/pages",
                    params={"parent": parent_id, "per_page": 100,
                            "page": page_num, "_fields": "link"},
                    timeout=10
                )
                if cr.status_code != 200 or not cr.json(): break
                for pg in cr.json():
                    link = pg.get("link","").rstrip("/") + "/"
                    if link.startswith(BASE_HTML) and link != BASE_HTML:
                        candidate_urls.add(link)
                if len(cr.json()) < 100: break
                page_num += 1
            print(f"HTML pages: {len(candidate_urls)} candidates (REST API)")
    except Exception as e:
        print(f"  REST API error: {e} — using known list only")

    # Only fetch URLs not already cached or 404'd
    to_fetch = [u for u in sorted(candidate_urls)
                if u not in known_404s and u not in fetched_data]
    print(f"  {len(fetched_data)} cached, {len(to_fetch)} to fetch")

    new_404s = set()
    for url in to_fetch:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 404:
                new_404s.add(url)
                continue
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            title_el = soup.find("h1") or soup.find("title")
            title = title_el.get_text(strip=True) if title_el else ""
            dm = DATE_RE.search(title)
            if not dm:
                content = soup.find("div", class_=re.compile(r"entry|content"))
                if content: dm = DATE_RE.search(content.get_text())
            if not dm:
                new_404s.add(url)
                continue
            raw_date = normalise_date(dm.group(0))
            year = re.search(r"\b(20\d{2})\b", raw_date)
            if not year: continue
            year = year.group(1)
            is_special = bool(re.search(r"special", title, re.IGNORECASE))
            display_date = ("Special Meeting " + raw_date) if is_special else raw_date
            slug_part = url.rstrip("/").split("/")[-1].lower()
            tl = title.lower()
            if slug_part.startswith("agenda"):       label = "Agenda"
            elif slug_part.startswith("minutes"):    label = "Minutes"
            elif tl.startswith("agenda"):            label = "Agenda"
            elif tl.startswith("minutes"):           label = "Minutes"
            elif re.search(r"\*+\s*agenda\s*\*+", tl): label = "Agenda"
            else:                                    label = "Minutes"
            content_div = (soup.find("div", class_=re.compile(r"entry-content|post-content"))
                           or soup.find("article") or soup.find("main"))
            content_text = ""
            if content_div:
                for el in content_div.find_all(["nav","header","footer","script","style"]):
                    el.decompose()
                content_text = content_div.get_text(separator="\n").strip()[:6000]
            fetched_data[url] = {
                "date": display_date, "label": label, "year": year,
                "url": url, "filename": url.rstrip("/").split("/")[-1],
                "type": "html_page", "content_text": content_text,
                "page_title": title,
            }
            print(f"  ✓ {display_date} — {label}")
        except Exception as e:
            print(f"  Error {url}: {e}")

    cache["not_found"] = list(known_404s | new_404s)
    cache["fetched"]   = fetched_data
    save_cache(cache)
    print(f"  HTML pages total: {len(fetched_data)}")
    return list(fetched_data.values())


# ── Download PDFs ──────────────────────────────────────────────────────────

def download_pdfs(meetings, state):
    new_count = 0
    for year, docs in meetings.items():
        yd = DOCS_DIR / year / "files"
        yd.mkdir(parents=True, exist_ok=True)
        for doc in docs:
            if doc.get("type") != "pdf": continue
            url  = doc["url"]
            dest = yd / doc["filename"]
            if url in state and dest.exists(): continue
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                dest.write_bytes(r.content)
                state[url] = {"filename": doc["filename"], "year": year,
                              "downloaded_at": datetime.now().isoformat()}
                new_count += 1
                print(f"  ↓ {doc['filename']}")
            except Exception as e:
                print(f"  ✗ {url}: {e}")
    print(f"  {new_count} new PDF(s) downloaded")
    return new_count


# ── AI Summaries ───────────────────────────────────────────────────────────

def extract_pdf_text(path, max_chars=40000):
    if not PDF_EXTRACT: return ""
    try:
        buf = io.StringIO()
        with open(path,"rb") as f:
            extract_text_to_fp(f, buf, laparams=LAParams(), output_type="text", codec="utf-8")
        return buf.getvalue().strip()[:max_chars]
    except: return ""

def generate_ai_summary(date_text, slots, year_files_dir):
    if not ANTHROPIC_API_KEY: return None
    combined = ""
    for dt, lbl in [("package","Agenda Package"),("minutes","Minutes")]:
        for doc in slots.get(dt,[]):
            if doc.get("type") != "pdf": continue
            path = year_files_dir / doc["filename"]
            if path.exists():
                txt = extract_pdf_text(path)
                if txt: combined += f"\n\n=== {lbl} ===\n{txt}"
    if not combined.strip(): return None
    prompt = f"""Summarize this Nipissing Township Council meeting for a public archive.
Meeting date: {date_text}
Documents: {combined[:35000]}
Provide:
1. **Key Decisions** — motions passed or defeated (bullets)
2. **Main Topics** — what was discussed (bullets)
3. **Notable Items** — significant spending or items of public interest
Factual, neutral, under 400 words, plain language."""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 1024,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"    AI summary error: {e}")
        return None

def render_summary_html(md):
    if not md: return ""
    lines, out, in_ul = md.split("\n"), [], False
    for line in lines:
        line = line.strip()
        if not line:
            if in_ul: out.append("</ul>"); in_ul = False
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if line.startswith(("- ","• ")):
            if not in_ul: out.append("<ul>"); in_ul = True
            out.append(f"<li>{line[2:].strip()}</li>")
        else:
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<p>{line}</p>")
    if in_ul: out.append("</ul>")
    return "\n".join(out)


# ── Next update date ───────────────────────────────────────────────────────

def next_scrape_date():
    today = datetime.now()
    days_ahead = (1 - today.weekday()) % 7 or 7  # next Monday
    nxt = today + timedelta(days=days_ahead)
    if nxt.isocalendar()[1] % 2 == 0:
        nxt += timedelta(days=7)
    return nxt.strftime("%B %d, %Y")


# ── HTML helpers ───────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700;800&family=Lato:wght@300;400;700&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}html{scroll-behavior:smooth}
:root{--forest:#2C4A3E;--pine:#3D6B5E;--water:#5B9FAF;--sky:#A8D5E2;--sand:#F2EAD3;--warm:#E8C98A;--rust:#C06830;--charcoal:#1E2B2A;--cream:#FAF7F0;--white:#FFFFFF;--shadow:0 2px 16px rgba(30,43,42,.10)}
body{font-family:'Lato',Georgia,sans-serif;background:var(--cream);color:var(--charcoal);line-height:1.6;overflow-x:hidden}
nav{position:fixed;top:0;left:0;right:0;z-index:100;display:flex;align-items:center;justify-content:space-between;padding:1rem 2.5rem;background:rgba(44,74,62,.97);backdrop-filter:blur(8px);box-shadow:0 2px 20px rgba(0,0,0,.2)}
.nav-logo{font-family:'Playfair Display',serif;font-size:1.1rem;color:var(--warm);text-decoration:none}.nav-logo span{color:var(--sky)}
.nav-links{display:flex;gap:1.6rem;list-style:none}.nav-links a{color:rgba(255,255,255,.85);text-decoration:none;font-size:.75rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;transition:color .2s}.nav-links a:hover{color:var(--warm)}
.page-hero{background:var(--forest);padding:7rem 2rem 3.5rem;border-bottom:4px solid var(--warm);position:relative;overflow:hidden}
.page-hero::before{content:'';position:absolute;inset:0;background:repeating-linear-gradient(-45deg,transparent,transparent 40px,rgba(255,255,255,.015) 40px,rgba(255,255,255,.015) 80px)}
.page-hero .inner{max-width:1100px;margin:0 auto;position:relative}
.page-hero .eyebrow{font-size:.7rem;font-weight:700;letter-spacing:.25em;text-transform:uppercase;color:var(--warm);margin-bottom:.8rem;opacity:0;animation:fadeUp .6s ease .1s forwards}
.page-hero h1{font-family:'Playfair Display',serif;font-size:clamp(2rem,4vw,3.2rem);font-weight:800;color:var(--white);line-height:1.1;margin-bottom:.75rem;opacity:0;animation:fadeUp .6s ease .25s forwards}
.page-hero h1 em{font-style:normal;color:var(--warm)}.page-hero p{font-size:1rem;font-weight:300;color:rgba(255,255,255,.75);max-width:580px;line-height:1.75;opacity:0;animation:fadeUp .6s ease .4s forwards}
.breadcrumb{display:flex;align-items:center;gap:.5rem;margin-top:1.5rem;font-size:.78rem;color:rgba(255,255,255,.45);opacity:0;animation:fadeUp .6s ease .5s forwards}
.breadcrumb a{color:var(--sky);text-decoration:none;transition:color .2s}.breadcrumb a:hover{color:var(--warm)}.breadcrumb .sep{opacity:.4}
main{max-width:1100px;margin:3rem auto;padding:0 2rem 5rem}
.section-label{font-size:.68rem;font-weight:700;letter-spacing:.25em;text-transform:uppercase;color:var(--rust);margin-bottom:.5rem}
.year-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1.2rem;margin-top:1.5rem}
.year-card{background:var(--white);border:1px solid rgba(44,74,62,.15);border-radius:10px;padding:1.75rem 1.5rem;text-decoration:none;color:var(--charcoal);box-shadow:var(--shadow);transition:border-color .2s,transform .15s,box-shadow .2s;display:flex;flex-direction:column;gap:.4rem;border-top:3px solid var(--forest)}
.year-card:hover{border-color:var(--warm);border-top-color:var(--rust);transform:translateY(-3px);box-shadow:0 8px 28px rgba(30,43,42,.14)}
.year-card .yr{font-family:'Playfair Display',serif;font-size:2rem;font-weight:800;color:var(--forest);line-height:1}.year-card .count{font-size:.8rem;font-weight:700;color:#888}
.notice{background:var(--sand);border-left:4px solid var(--warm);padding:1rem 1.5rem;border-radius:0 8px 8px 0;margin-bottom:2.5rem;font-size:.88rem;color:#666;line-height:1.65}
.notice a{color:var(--forest);font-weight:700;text-decoration:none}.notice a:hover{color:var(--rust)}
.year-nav{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:2rem}
.year-nav a{font-size:.78rem;font-weight:700;padding:.3rem .8rem;border-radius:4px;background:var(--white);border:1px solid rgba(44,74,62,.2);color:var(--forest);text-decoration:none;transition:background .15s,color .15s}
.year-nav a:hover,.year-nav a.active{background:var(--forest);color:var(--white);border-color:var(--forest)}
.meetings-table{width:100%;border-collapse:collapse;margin-top:.5rem;font-size:.88rem}
.meetings-table thead tr{background:var(--forest);color:var(--white)}
.meetings-table thead th{padding:.75rem 1rem;text-align:left;font-size:.7rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;white-space:nowrap}
.meetings-table thead th:first-child{border-radius:8px 0 0 0}.meetings-table thead th:last-child{border-radius:0 8px 0 0}
.meetings-table tbody tr{border-bottom:1px solid rgba(44,74,62,.1);transition:background .15s}
.meetings-table tbody tr:last-child{border-bottom:none}.meetings-table tbody tr:hover{background:rgba(44,74,62,.04)}
.meetings-table tbody tr.special-row{background:rgba(192,104,48,.04)}
.meetings-table td{padding:.85rem 1rem;vertical-align:middle}
.meetings-table td.date-cell{font-family:'Playfair Display',serif;font-weight:700;color:var(--forest);white-space:nowrap;min-width:180px}
.meetings-table td.doc-cell{min-width:100px}
.date-link{color:var(--forest);text-decoration:none;border-bottom:2px solid #e8d5a3;transition:border-color .15s,color .15s}.date-link:hover{color:var(--rust);border-color:var(--rust)}
.doc-link{display:inline-flex;align-items:center;gap:.3rem;background:var(--cream);border:1px solid rgba(44,74,62,.18);border-radius:4px;padding:.3rem .65rem;font-size:.8rem;font-weight:700;color:var(--forest);text-decoration:none;white-space:nowrap;transition:background .15s,color .15s,border-color .15s}
.doc-link:hover{background:var(--forest);color:var(--white);border-color:var(--forest)}
.doc-link.youtube{background:rgba(255,0,0,.07);border-color:rgba(255,0,0,.2);color:#c00}.doc-link.youtube:hover{background:#c00;color:var(--white);border-color:#c00}
.doc-link svg{flex-shrink:0}.extra-docs{display:flex;flex-wrap:wrap;gap:.4rem}
.no-doc{color:rgba(44,74,62,.25);font-size:.8rem}
.table-wrapper{background:var(--white);border:1px solid rgba(44,74,62,.12);border-radius:10px;overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
.special-badge{display:inline-block;background:var(--rust);color:var(--white);font-size:.62rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;padding:.18rem .55rem;border-radius:3px}
.meeting-hero-meta{display:flex;gap:1rem;flex-wrap:wrap;margin-top:1rem;opacity:0;animation:fadeUp .6s ease .5s forwards}
.meeting-hero-meta a{display:inline-flex;align-items:center;gap:.35rem;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);border-radius:5px;padding:.4rem .9rem;font-size:.82rem;font-weight:700;color:var(--white);text-decoration:none;transition:background .15s}.meeting-hero-meta a:hover{background:rgba(255,255,255,.22)}
.meeting-hero-meta a.yt-btn{background:rgba(192,0,0,.25);border-color:rgba(255,80,80,.3)}.meeting-hero-meta a.yt-btn:hover{background:rgba(192,0,0,.45)}
.meeting-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin-bottom:2rem}
.meeting-card{background:var(--white);border:1px solid rgba(44,74,62,.12);border-top:3px solid var(--pine);border-radius:0 0 10px 10px;padding:1.25rem 1.5rem;box-shadow:var(--shadow)}
.meeting-card h3{font-family:'Playfair Display',serif;font-size:.9rem;font-weight:700;color:var(--forest);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.75rem}
.meeting-card .doc-links{display:flex;flex-direction:column;gap:.4rem}.meeting-card .doc-link{justify-content:flex-start}
.summary-card{background:var(--white);border:1px solid rgba(44,74,62,.12);border-top:3px solid var(--rust);border-radius:0 0 10px 10px;padding:1.75rem 2rem;box-shadow:var(--shadow);margin-bottom:2rem}
.summary-card h2{font-family:'Playfair Display',serif;font-size:1.3rem;color:var(--forest);margin-bottom:1.25rem;display:flex;align-items:center;gap:.5rem}
.summary-card p{font-size:.93rem;line-height:1.8;color:#444;margin-bottom:.75rem}.summary-card strong{color:var(--forest)}
.summary-card ul{margin:.4rem 0 .75rem 1.25rem}.summary-card li{font-size:.91rem;line-height:1.75;color:#444;margin-bottom:.25rem}
.ai-badge{font-size:.65rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;background:rgba(192,104,48,.1);color:var(--rust);padding:.2rem .5rem;border-radius:3px}
footer{background:var(--charcoal);padding:2.5rem 2rem;text-align:center}
footer .footer-inner{max-width:1100px;margin:0 auto;display:flex;flex-direction:column;align-items:center;gap:.75rem}
footer .footer-logo{font-family:'Playfair Display',serif;font-size:1.1rem;color:var(--warm)}.footer-logo span{color:var(--sky)}
footer p{font-size:.8rem;color:rgba(255,255,255,.35)}footer a{color:var(--warm);text-decoration:none}
@keyframes fadeUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:700px){nav{padding:1rem 1.25rem}.nav-links{gap:.8rem}.nav-links a{font-size:.65rem}.year-grid{grid-template-columns:repeat(2,1fr)}main{padding:0 1.25rem 4rem}.meeting-grid{grid-template-columns:1fr}.meetings-table thead{display:none}.meetings-table tbody tr{display:block;padding:1rem;border-bottom:2px solid rgba(44,74,62,.1)}.meetings-table td{display:block;padding:.3rem 0;border:none}.meetings-table td.date-cell{font-size:1rem;margin-bottom:.5rem}}
"""

PDF_ICON  = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
HTML_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>'
YT_ICON   = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M23 7s-.3-2-1.2-2.8c-1.1-1.2-2.4-1.2-3-1.3C16.4 2.8 12 2.8 12 2.8s-4.4 0-6.8.1c-.6.1-1.9.1-3 1.3C1.3 5 1 7 1 7S.7 9.1.7 11.3v2c0 2.1.3 4.3.3 4.3s.3 2 1.2 2.8c1.1 1.2 2.6 1.1 3.3 1.2C7.5 21.7 12 21.7 12 21.7s4.4 0 6.8-.2c.6-.1 1.9-.1 3-1.3.9-.8 1.2-2.8 1.2-2.8s.3-2.1.3-4.3v-2C23.3 9.1 23 7 23 7zM9.7 15.5V8.3l8.1 3.6-8.1 3.6z"/></svg>'

def nav_html():
    if IS_PUBLIC:
        return '<nav><a class="nav-logo" href="/">Nipissing <span>Council Archive</span></a><ul class="nav-links"><li><a href="/">Home</a></li><li><a href="https://bylaw.chriswjohnston.ca">By-Law Archive</a></li></ul></nav>'
    return '<nav><a class="nav-logo" href="https://chriswjohnston.ca">Chris <span>Johnston</span></a><ul class="nav-links"><li><a href="/">Council Archive</a></li><li><a href="https://bylaw.chriswjohnston.ca">By-Law Archive</a></li></ul></nav>'

def footer_html():
    return f'<footer><div class="footer-inner"><div class="footer-logo">Nipissing <span>Council Archive</span></div><p>Sourced from <a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a> &mdash; Last updated {datetime.now().strftime("%B %d, %Y")}</p></div></footer>'

def notice_text(count, year=None):
    src = f'<a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a>'
    yt  = f'<a href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">Township of Nipissing YouTube channel</a>'
    upd = datetime.now().strftime("%B %d, %Y")
    nxt = next_scrape_date()
    if year:
        return f"{count} documents for {year}. Sourced from {src}. Videos via the {yt}. Last updated {upd} &mdash; Next update {nxt}."
    return f"<strong>{count} documents</strong> organized and indexed from {src}. Videos via the {yt}. Last updated {upd} &mdash; Next update {nxt}."

def sort_date_key(s):
    for fmt in ("%B %d, %Y", "%B %d %Y", "Special Meeting %B %d, %Y"):
        try: return datetime.strptime(s.strip(), fmt)
        except: pass
    return datetime.min

def classify(label):
    l = label.lower()
    if "package" in l: return "package"
    if "agenda" in l:  return "agenda"
    if "minute" in l:  return "minutes"
    return "other"

def date_slug(dt):
    c = re.sub(r"^special meeting\s+","",dt,flags=re.IGNORECASE).strip()
    c = re.sub(r"[^a-zA-Z0-9\s]","",c).strip()
    return re.sub(r"\s+","-",c).lower()

def get_yt_url(date_text, yt_videos):
    clean = re.sub(r"^special meeting\s+","",date_text,flags=re.IGNORECASE).strip()
    return yt_videos.get(date_text) or yt_videos.get(clean)

def doc_btn(doc, prefix="files/"):
    if doc.get("type") == "html_page":
        return (f'<a class="doc-link" href="{doc["url"]}" target="_blank" rel="noopener">'
                f'{HTML_ICON} {doc["label"]} <span style="font-size:.65rem;opacity:.6">(web)</span></a>')
    return (f'<a class="doc-link" href="{prefix}{doc["filename"]}" target="_blank" rel="noopener">'
            f'{PDF_ICON} {doc["label"]}</a>')

def yt_btn(url):
    return f'<a class="doc-link youtube" href="{url}" target="_blank" rel="noopener">{YT_ICON} Watch</a>'

def head(title):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <style>{CSS}</style>
</head>
<body>"""


# ── Page generators ────────────────────────────────────────────────────────

def generate_meeting_page(date_text, year, slots, yt_videos, summary):
    yt_url     = get_yt_url(date_text, yt_videos)
    is_special = "special" in date_text.lower()
    badge      = '<span class="special-badge">Special Meeting</span>' if is_special else ""
    meta = ""
    for slot, lbl in [("agenda","Agenda"),("minutes","Minutes"),("package","Agenda Package")]:
        for d in slots.get(slot,[]):
            p = "../files/" if d.get("type") == "pdf" else ""
            icon = HTML_ICON if d.get("type") == "html_page" else PDF_ICON
            href = d["url"] if d.get("type") == "html_page" else f"../files/{d['filename']}"
            meta += f'<a href="{href}" target="_blank">{icon} {lbl}</a>'
    if yt_url: meta += f'<a href="{yt_url}" target="_blank" class="yt-btn">{YT_ICON} Watch Meeting</a>'
    else:       meta += f'<a href="{YOUTUBE_CHANNEL}" target="_blank" class="yt-btn">{YT_ICON} YouTube Channel</a>'

    def card(title, docs, color="var(--pine)"):
        if not docs: return ""
        links = ""
        for d in docs:
            href = d["url"] if d.get("type") == "html_page" else f"../files/{d['filename']}"
            icon = HTML_ICON if d.get("type") == "html_page" else PDF_ICON
            suffix = ' <span style="font-size:.65rem;opacity:.6">(web)</span>' if d.get("type") == "html_page" else ""
            links += f'<a class="doc-link" href="{href}" target="_blank" rel="noopener">{icon} {d["label"]}{suffix}</a>'
        return f'<div class="meeting-card" style="border-top-color:{color}"><h3>{title}</h3><div class="doc-links">{links}</div></div>'

    html_text_card = ""
    for slot_docs in slots.values():
        for d in slot_docs:
            if d.get("type") == "html_page" and d.get("content_text"):
                txt = "<br>".join(
                    ln for ln in d["content_text"].split("\n") if ln.strip() and len(ln.strip()) > 3
                )
                html_text_card = f"""<div class="summary-card" style="border-top-color:var(--pine)">
  <h2>{d["label"]} — {date_text}</h2>
  <div style="font-size:.88rem;line-height:1.9;color:#444;max-height:600px;overflow-y:auto;">{txt}</div>
  <p style="font-size:.75rem;color:#aaa;margin-top:1rem;">Source: <a href="{d['url']}" target="_blank" rel="noopener">nipissingtownship.com</a></p>
</div>"""
                break
        if html_text_card: break

    summary_html = ""
    if summary:
        summary_html = f'<div class="summary-card"><h2>Meeting Summary <span class="ai-badge">AI Generated</span></h2>{render_summary_html(summary)}<p style="font-size:.75rem;color:#aaa;margin-top:1rem;">Generated from meeting documents. Refer to official minutes for authoritative information.</p></div>'

    cards = (card("Agenda",slots["agenda"],"var(--forest)") +
             card("Minutes",slots["minutes"],"var(--pine)") +
             card("Agenda Package",slots["package"],"var(--water)") +
             card("Additional Files",slots["other"],"var(--warm)"))

    return f"""{head(f"{date_text} Council Meeting – Nipissing Township")}
{nav_html()}
<div class="page-hero"><div class="inner">
  <p class="eyebrow">Nipissing Township &middot; Council Meeting</p>
  <h1>{date_text} {badge}</h1>
  <p>Agenda, minutes and supporting documents for this council meeting.</p>
  <div class="meeting-hero-meta">{meta}</div>
  <div class="breadcrumb" style="margin-top:1rem;">
    <a href="../../">All Years</a><span class="sep">/</span>
    <a href="../">{year}</a><span class="sep">/</span>
    <span>{date_text}</span>
  </div>
</div></div>
<main>{summary_html}{html_text_card}<div class="meeting-grid">{cards}</div></main>
{footer_html()}
</body></html>"""


def generate_year_page(year, docs, all_years, yt_videos, summaries):
    grouped = defaultdict(list)
    for doc in docs: grouped[doc["date"]].append(doc)
    sorted_dates = sorted(grouped.keys(), key=sort_date_key)
    year_nav_parts = []
    for y in sorted(all_years, reverse=True):
        active = 'class="active"' if y == year else ""
        year_nav_parts.append(f'<a href="../{y}/" {active}>{y}</a>')
    year_nav = "".join(year_nav_parts)
    rows = ""
    for date in sorted_dates:
        date_docs = grouped[date]
        is_special = "special" in date.lower() or any("special" in d.get("filename","").lower() for d in date_docs)
        row_cls = ' class="special-row"' if is_special else ""
        badge   = '<span class="special-badge">Special</span>' if is_special else ""
        slots   = {"agenda":[],"minutes":[],"package":[],"other":[]}
        for d in date_docs: slots[classify(d["label"])].append(d)

        def cell(dl):
            if not dl: return '<td class="doc-cell"><span class="no-doc">&mdash;</span></td>'
            return f'<td class="doc-cell">{"".join(doc_btn(d) for d in dl)}</td>'

        other_parts = [doc_btn(d) for d in slots["other"]]
        other_cell = (f'<td class="doc-cell"><div class="extra-docs">{"".join(other_parts)}</div></td>'
                      if other_parts else '<td class="doc-cell"><span class="no-doc">&mdash;</span></td>')

        yt_url = get_yt_url(date, yt_videos)
        yt_cell = (f'<td class="doc-cell">{yt_btn(yt_url)}</td>' if yt_url else
                   f'<td class="doc-cell"><a class="doc-link youtube" href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">{YT_ICON} Channel</a></td>')

        slug = date_slug(date)
        rows += f"""
      <tr{row_cls}>
        <td class="date-cell"><a href="{slug}/" class="date-link">{date}{badge}</a></td>
        {cell(slots["agenda"])}{cell(slots["minutes"])}{cell(slots["package"])}
        {yt_cell}{other_cell}
      </tr>"""

    return f"""{head(f"{year} Council Meetings – Nipissing Township")}
{nav_html()}
<div class="page-hero"><div class="inner">
  <p class="eyebrow">Nipissing Township &middot; Council Archive</p>
  <h1>{year} <em>Council Meetings</em></h1>
  <p>Agendas, minutes, agenda packages and videos for {year} council meetings.</p>
  <div class="breadcrumb"><a href="../">All Years</a><span class="sep">/</span><span>{year}</span></div>
</div></div>
<main>
  <div class="notice">{notice_text(len(docs), year)}</div>
  <p class="section-label">Browse by year</p>
  <div class="year-nav" style="margin-bottom:1.5rem;">{year_nav}</div>
  <div class="table-wrapper">
    <table class="meetings-table">
      <thead><tr><th>Meeting Date</th><th>Agenda</th><th>Minutes</th><th>Agenda Package</th><th>Video</th><th>Additional Files</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</main>
{footer_html()}
</body></html>"""


def generate_index_page(meetings_by_year):
    years = sorted(meetings_by_year.keys(), reverse=True)
    total = sum(len(v) for v in meetings_by_year.values())
    cards = "".join(
        f'<a class="year-card" href="{y}/"><span class="yr">{y}</span>'
        f'<span class="count">{len(meetings_by_year[y])} documents</span></a>'
        for y in years
    )
    return f"""{head("Council Meeting Archive")}
{nav_html()}
<div class="page-hero"><div class="inner">
  <p class="eyebrow">Nipissing Township</p>
  <h1>Council Meeting <em>Archive</em></h1>
  <p>Agendas, minutes, agenda packages and videos for Nipissing Township council meetings.</p>
</div></div>
<main>
  <div class="notice">{notice_text(total)}</div>
  <p class="section-label">Select a year</p>
  <div class="year-grid">{cards}</div>
</main>
{footer_html()}
</body></html>"""


# ── Build HTML site ────────────────────────────────────────────────────────

def build_html(meetings, yt_videos):
    print("\nBuilding HTML site...")
    DOCS_DIR.mkdir(exist_ok=True)
    all_years = list(meetings.keys())
    try:
        summaries = json.loads(SUMMARIES_FILE.read_text()) if SUMMARIES_FILE.exists() else {}
    except: summaries = {}

    for year, docs in meetings.items():
        year_dir = DOCS_DIR / year
        year_dir.mkdir(exist_ok=True)
        year_files_dir = year_dir / "files"
        grouped = defaultdict(list)
        for doc in docs: grouped[doc["date"]].append(doc)

        for date_text, date_docs in grouped.items():
            slug = date_slug(date_text)
            mtg_dir = year_dir / slug
            mtg_dir.mkdir(exist_ok=True)
            slots = {"agenda":[],"minutes":[],"package":[],"other":[]}
            for d in date_docs: slots[classify(d["label"])].append(d)

            summary_key = f"{year}/{slug}"
            summary     = summaries.get(summary_key)
            has_both    = bool(slots["minutes"] and slots["package"] and
                               any(d.get("type")=="pdf" for d in slots["minutes"]) and
                               any(d.get("type")=="pdf" for d in slots["package"]))
            if summary is None and has_both and ANTHROPIC_API_KEY:
                print(f"  AI summary: {date_text}...")
                summary = generate_ai_summary(date_text, slots, year_files_dir)
                if summary:
                    summaries[summary_key] = summary
                    SUMMARIES_FILE.write_text(json.dumps(summaries, indent=2))

            (mtg_dir / "index.html").write_text(
                generate_meeting_page(date_text, year, slots, yt_videos, summary),
                encoding="utf-8"
            )

        (year_dir / "index.html").write_text(
            generate_year_page(year, docs, all_years, yt_videos, summaries),
            encoding="utf-8"
        )
        print(f"  ✓ {year}: {len(docs)} docs")

    (DOCS_DIR / "index.html").write_text(generate_index_page(meetings), encoding="utf-8")
    print(f"  ✓ index.html ({sum(len(v) for v in meetings.values())} total docs)")


# ── Main ───────────────────────────────────────────────────────────────────

def merge_all(pdf_meetings, docs_2023, html_docs):
    """Merge all sources into one dict keyed by year."""
    merged = defaultdict(list)

    # Add PDF meetings (main page)
    for year, docs in pdf_meetings.items():
        merged[year].extend(docs)

    # Add 2023 (no brute-force needed)
    for doc in docs_2023:
        url = doc["url"]
        if not any(d["url"] == url for d in merged["2023"]):
            merged["2023"].append(doc)

    # Add HTML pages — skip if PDF already covers same date+label
    for doc in html_docs:
        year = re.search(r"\b(20\d{2})\b", doc["date"])
        if not year: continue
        year = year.group(1)
        already = any(
            d["date"] == doc["date"] and
            d["label"].lower() == doc["label"].lower() and
            d.get("type") == "pdf"
            for d in merged[year]
        )
        if not already:
            merged[year].append(doc)

    return dict(merged)


if __name__ == "__main__":
    print("=" * 55)
    print(f"Nipissing Council Archive [{BRANCH.upper()}]")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    state = load_state()

    print("\n1. YouTube RSS")
    yt_videos = fetch_youtube_videos(state)

    print("\n2. Main page PDFs")
    pdf_meetings = fetch_pdf_links()

    print("\n3. 2023 PDFs")
    docs_2023 = get_2023_meetings(state)

    print("\n4. HTML pages (2018–2022)")
    html_docs = fetch_html_pages(state)

    print("\n5. Merging all sources")
    meetings = merge_all(pdf_meetings, docs_2023, html_docs)
    for yr in sorted(meetings):
        print(f"   {yr}: {len(meetings[yr])} docs")

    print("\n6. Downloading new PDFs")
    download_pdfs(meetings, state)
    save_state(state)

    print("\n7. Building HTML site")
    build_html(meetings, yt_videos)

    total = sum(len(v) for v in meetings.values())
    print(f"\n✓ Done — {total} documents across {len(meetings)} years")
