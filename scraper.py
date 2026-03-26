"""
Nipissing Township Council Meeting Scraper
==========================================
Set BRANCH=public for stripped-down public version.
Set BRANCH=campaign (default) for full campaign version.
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
BRANCH             = os.environ.get("BRANCH", "campaign").lower()
IS_PUBLIC          = BRANCH == "public"

DOCS_DIR       = Path("docs")
STATE_FILE     = Path("state.json")
SUMMARIES_FILE = Path("summaries.json")

YOUTUBE_VIDEOS = {}


# ─── YOUTUBE RSS ───────────────────────────────────────────────

def fetch_youtube_videos(state):
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
    videos = {v["date"]: v["url"] for v in state.get("_youtube_videos", {}).values()}
    print(f"Fetching YouTube RSS ... ({len(videos)} previously saved)")
    DATE_RE = re.compile(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{{1,2}}),?\s+(\d{{4}})",
        re.IGNORECASE
    )
    try:
        resp = requests.get(rss_url, timeout=15)
        resp.raise_for_status()
        NS = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(resp.content)
        for entry in root.findall("atom:entry", NS):
            title_el = entry.find("atom:title", NS)
            link_el  = entry.find("atom:link",  NS)
            if title_el is None or link_el is None:
                continue
            title = title_el.text or ""
            url   = link_el.get("href", "")
            tl = title.lower()
            if "council meeting" not in tl:
                continue
            if any(w in tl for w in ["committee","adjustment","conservation","museum","recreation"]):
                continue
            m = re.search(
                r"(January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
                title, re.IGNORECASE
            )
            if m:
                date_key = f"{m.group(1).capitalize()} {int(m.group(2))}, {m.group(3)}"
                if date_key not in videos:
                    videos[date_key] = url
                    print(f"  YouTube: {date_key} -> {url}")
    except Exception as e:
        print(f"  Could not fetch YouTube RSS: {e}")
    state["_youtube_videos"] = {d: {"date": d, "url": u} for d, u in videos.items()}
    print(f"  Found {len(videos)} dated video(s) total")
    return videos


# ─── SCRAPING ──────────────────────────────────────────────────

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
        r"September|October|November|December)\s+\d{1,2},?\s+\d{4}", re.IGNORECASE
    )
    for tag in soup.find_all(["h1","h2","h3","h4","strong","b","a","br"]):
        if tag.name in ("h1","h2","h3","h4","strong","b"):
            ym = re.search(r"\b(20\d{2})\b", tag.get_text(strip=True))
            if ym:
                current_year = ym.group(1)
        if tag.name == "a" and tag.get("href","").endswith(".pdf"):
            href  = tag["href"]
            label = re.sub(r"^\(+|\)+$", "", tag.get_text(strip=True)).strip()
            line_text = ""
            if tag.parent:
                for sib in tag.previous_siblings:
                    if hasattr(sib,"name") and sib.name == "br":
                        break
                    line_text = (sib.get_text(" ") if hasattr(sib,"get_text") else str(sib)) + " " + line_text
            dm = DATE_RE.search(line_text)
            if dm:
                raw = dm.group(0).strip()
                raw = re.sub(
                    r"(January|February|March|April|May|June|July|August|"
                    r"September|October|November|December)\s+(\d{1,2})\s+(\d{4})",
                    r"\1 \2, \3", raw
                )
                current_date = raw
                if re.search(r"special meeting", line_text, re.IGNORECASE):
                    current_date = "Special Meeting " + raw
            meetings[current_year].append({
                "date": current_date, "label": label,
                "url": href, "filename": os.path.basename(urlparse(href).path),
            })
    for year in meetings:
        seen, unique = set(), []
        for item in meetings[year]:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)
        meetings[year] = unique
    total = sum(len(v) for v in meetings.values())
    print(f"  Found {total} PDF links across {len(meetings)} years")
    return meetings


# ─── DOWNLOADING ───────────────────────────────────────────────

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
        yd = DOCS_DIR / year / "files"
        yd.mkdir(parents=True, exist_ok=True)
        for doc in docs:
            url  = doc["url"]
            dest = yd / doc["filename"]
            if url in state:
                state[url].update({"label": doc["label"], "date": doc["date"], "year": year})
            if url in state and dest.exists():
                continue
            print(f"  Downloading: {doc['filename']} ...")
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                dest.write_bytes(r.content)
                state[url] = {"filename": doc["filename"], "year": year,
                              "label": doc["label"], "date": doc["date"],
                              "downloaded_at": datetime.now().isoformat()}
                new_count += 1
                print(f"    ✓ Saved")
            except Exception as e:
                print(f"    ✗ Failed: {e}")
    print(f"  {new_count} new file(s) downloaded")
    return new_count


# ─── AI SUMMARY ────────────────────────────────────────────────

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
    combined = ""
    for dt, lbl in [("package","Agenda Package"),("minutes","Minutes")]:
        for doc in slots.get(dt, []):
            path = year_files_dir / doc["filename"]
            if path.exists():
                txt = extract_pdf_text(path)
                if txt:
                    combined += f"\n\n=== {lbl} ===\n{txt}"
    if not combined.strip():
        print("    No PDF text extracted, skipping summary")
        return None
    prompt = f"""Summarize this Nipissing Township Council meeting for a public archive.

Meeting date: {date_text}

Documents:
{combined[:35000]}

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
        try: print(f"    Response: {resp.text[:300]}")
        except: pass
        return None

def render_summary_html(md):
    if not md:
        return ""
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


# ─── STYLES ────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700;800&family=Lato:wght@300;400;700&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
:root{
  --forest:#2C4A3E;--pine:#3D6B5E;--water:#5B9FAF;--sky:#A8D5E2;
  --sand:#F2EAD3;--warm:#E8C98A;--rust:#C06830;--charcoal:#1E2B2A;
  --cream:#FAF7F0;--white:#FFFFFF;--shadow:0 2px 16px rgba(30,43,42,.10);
}
body{font-family:'Lato',Georgia,sans-serif;background:var(--cream);color:var(--charcoal);line-height:1.6;overflow-x:hidden}
nav{position:fixed;top:0;left:0;right:0;z-index:100;display:flex;align-items:center;justify-content:space-between;padding:1rem 2.5rem;background:rgba(44,74,62,.97);backdrop-filter:blur(8px);box-shadow:0 2px 20px rgba(0,0,0,.2)}
.nav-logo{font-family:'Playfair Display',serif;font-size:1.1rem;color:var(--warm);text-decoration:none}
.nav-logo span{color:var(--sky)}
.nav-links{display:flex;gap:1.6rem;list-style:none}
.nav-links a{color:rgba(255,255,255,.85);text-decoration:none;font-size:.75rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;transition:color .2s}
.nav-links a:hover{color:var(--warm)}
.page-hero{background:var(--forest);padding:7rem 2rem 3.5rem;border-bottom:4px solid var(--warm);position:relative;overflow:hidden}
.page-hero::before{content:'';position:absolute;inset:0;background:repeating-linear-gradient(-45deg,transparent,transparent 40px,rgba(255,255,255,.015) 40px,rgba(255,255,255,.015) 80px)}
.page-hero .inner{max-width:1100px;margin:0 auto;position:relative}
.page-hero .eyebrow{font-size:.7rem;font-weight:700;letter-spacing:.25em;text-transform:uppercase;color:var(--warm);margin-bottom:.8rem;opacity:0;animation:fadeUp .6s ease .1s forwards}
.page-hero h1{font-family:'Playfair Display',serif;font-size:clamp(2rem,4vw,3.2rem);font-weight:800;color:var(--white);line-height:1.1;margin-bottom:.75rem;opacity:0;animation:fadeUp .6s ease .25s forwards}
.page-hero h1 em{font-style:normal;color:var(--warm)}
.page-hero p{font-size:1rem;font-weight:300;color:rgba(255,255,255,.75);max-width:580px;line-height:1.75;opacity:0;animation:fadeUp .6s ease .4s forwards}
.breadcrumb{display:flex;align-items:center;gap:.5rem;margin-top:1.5rem;font-size:.78rem;color:rgba(255,255,255,.45);opacity:0;animation:fadeUp .6s ease .5s forwards}
.breadcrumb a{color:var(--sky);text-decoration:none;transition:color .2s}
.breadcrumb a:hover{color:var(--warm)}
.breadcrumb .sep{opacity:.4}
main{max-width:1100px;margin:3rem auto;padding:0 2rem 5rem}
.section-label{font-size:.68rem;font-weight:700;letter-spacing:.25em;text-transform:uppercase;color:var(--rust);margin-bottom:.5rem}
.year-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1.2rem;margin-top:1.5rem}
.year-card{background:var(--white);border:1px solid rgba(44,74,62,.15);border-radius:10px;padding:1.75rem 1.5rem;text-decoration:none;color:var(--charcoal);box-shadow:var(--shadow);transition:border-color .2s,transform .15s,box-shadow .2s;display:flex;flex-direction:column;gap:.4rem;border-top:3px solid var(--forest)}
.year-card:hover{border-color:var(--warm);border-top-color:var(--rust);transform:translateY(-3px);box-shadow:0 8px 28px rgba(30,43,42,.14)}
.year-card .yr{font-family:'Playfair Display',serif;font-size:2rem;font-weight:800;color:var(--forest);line-height:1}
.year-card .count{font-size:.8rem;font-weight:700;color:#888}
.notice{background:var(--sand);border-left:4px solid var(--warm);padding:1rem 1.5rem;border-radius:0 8px 8px 0;margin-bottom:2.5rem;font-size:.88rem;color:#666;line-height:1.65}
.notice a{color:var(--forest);font-weight:700;text-decoration:none}
.notice a:hover{color:var(--rust)}
.year-nav{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:2rem}
.year-nav a{font-size:.78rem;font-weight:700;padding:.3rem .8rem;border-radius:4px;background:var(--white);border:1px solid rgba(44,74,62,.2);color:var(--forest);text-decoration:none;transition:background .15s,color .15s}
.year-nav a:hover,.year-nav a.active{background:var(--forest);color:var(--white);border-color:var(--forest)}
.meetings-table{width:100%;border-collapse:collapse;margin-top:.5rem;font-size:.88rem}
.meetings-table thead tr{background:var(--forest);color:var(--white)}
.meetings-table thead th{padding:.75rem 1rem;text-align:left;font-size:.7rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;white-space:nowrap}
.meetings-table thead th:first-child{border-radius:8px 0 0 0}
.meetings-table thead th:last-child{border-radius:0 8px 0 0}
.meetings-table tbody tr{border-bottom:1px solid rgba(44,74,62,.1);transition:background .15s}
.meetings-table tbody tr:last-child{border-bottom:none}
.meetings-table tbody tr:hover{background:rgba(44,74,62,.04)}
.meetings-table tbody tr.special-row{background:rgba(192,104,48,.04)}
.meetings-table td{padding:.85rem 1rem;vertical-align:middle}
.meetings-table td.date-cell{font-family:'Playfair Display',serif;font-weight:700;color:var(--forest);white-space:nowrap;min-width:180px}
.meetings-table td.doc-cell{min-width:100px}
.date-link{color:var(--forest);text-decoration:none;border-bottom:2px solid #e8d5a3;transition:border-color .15s,color .15s}
.date-link:hover{color:var(--rust);border-color:var(--rust)}
.doc-link{display:inline-flex;align-items:center;gap:.3rem;background:var(--cream);border:1px solid rgba(44,74,62,.18);border-radius:4px;padding:.3rem .65rem;font-size:.8rem;font-weight:700;color:var(--forest);text-decoration:none;white-space:nowrap;transition:background .15s,color .15s,border-color .15s}
.doc-link:hover{background:var(--forest);color:var(--white);border-color:var(--forest)}
.doc-link.youtube{background:rgba(255,0,0,.07);border-color:rgba(255,0,0,.2);color:#c00}
.doc-link.youtube:hover{background:#c00;color:var(--white);border-color:#c00}
.doc-link svg{flex-shrink:0}
.extra-docs{display:flex;flex-wrap:wrap;gap:.4rem}
.no-doc{color:rgba(44,74,62,.25);font-size:.8rem}
.table-wrapper{background:var(--white);border:1px solid rgba(44,74,62,.12);border-radius:10px;overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
.special-badge{display:inline-block;background:var(--rust);color:var(--white);font-size:.62rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;padding:.18rem .55rem;border-radius:3px}
.meeting-hero-meta{display:flex;gap:1rem;flex-wrap:wrap;margin-top:1rem;opacity:0;animation:fadeUp .6s ease .5s forwards}
.meeting-hero-meta a{display:inline-flex;align-items:center;gap:.35rem;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);border-radius:5px;padding:.4rem .9rem;font-size:.82rem;font-weight:700;color:var(--white);text-decoration:none;transition:background .15s}
.meeting-hero-meta a:hover{background:rgba(255,255,255,.22)}
.meeting-hero-meta a.yt-btn{background:rgba(192,0,0,.25);border-color:rgba(255,80,80,.3)}
.meeting-hero-meta a.yt-btn:hover{background:rgba(192,0,0,.45)}
.meeting-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin-bottom:2rem}
.meeting-card{background:var(--white);border:1px solid rgba(44,74,62,.12);border-top:3px solid var(--pine);border-radius:0 0 10px 10px;padding:1.25rem 1.5rem;box-shadow:var(--shadow)}
.meeting-card h3{font-family:'Playfair Display',serif;font-size:.9rem;font-weight:700;color:var(--forest);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.75rem}
.meeting-card .doc-links{display:flex;flex-direction:column;gap:.4rem}
.meeting-card .doc-link{justify-content:flex-start}
.summary-card{background:var(--white);border:1px solid rgba(44,74,62,.12);border-top:3px solid var(--rust);border-radius:0 0 10px 10px;padding:1.75rem 2rem;box-shadow:var(--shadow);margin-bottom:2rem}
.summary-card h2{font-family:'Playfair Display',serif;font-size:1.3rem;color:var(--forest);margin-bottom:1.25rem;display:flex;align-items:center;gap:.5rem}
.summary-card p{font-size:.93rem;line-height:1.8;color:#444;margin-bottom:.75rem}
.summary-card strong{color:var(--forest)}
.summary-card ul{margin:.4rem 0 .75rem 1.25rem}
.summary-card li{font-size:.91rem;line-height:1.75;color:#444;margin-bottom:.25rem}
.ai-badge{font-size:.65rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;background:rgba(192,104,48,.1);color:var(--rust);padding:.2rem .5rem;border-radius:3px}
footer{background:var(--charcoal);padding:2.5rem 2rem;text-align:center}
footer .footer-inner{max-width:1100px;margin:0 auto;display:flex;flex-direction:column;align-items:center;gap:.75rem}
footer .footer-logo{font-family:'Playfair Display',serif;font-size:1.1rem;color:var(--warm)}
footer .footer-logo span{color:var(--sky)}
footer p{font-size:.8rem;color:rgba(255,255,255,.35)}
footer a{color:var(--warm);text-decoration:none}
@keyframes fadeUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:700px){
  nav{padding:1rem 1.25rem}.nav-links{gap:.8rem}.nav-links a{font-size:.65rem}
  .year-grid{grid-template-columns:repeat(2,1fr)}main{padding:0 1.25rem 4rem}
  .meeting-grid{grid-template-columns:1fr}
  .meetings-table thead{display:none}
  .meetings-table tbody tr{display:block;padding:1rem;border-bottom:2px solid rgba(44,74,62,.1)}
  .meetings-table td{display:block;padding:.3rem 0;border:none}
  .meetings-table td.date-cell{font-size:1rem;margin-bottom:.5rem}
}
"""

PDF_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
YT_ICON   = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M23 7s-.3-2-1.2-2.8c-1.1-1.2-2.4-1.2-3-1.3C16.4 2.8 12 2.8 12 2.8s-4.4 0-6.8.1c-.6.1-1.9.1-3 1.3C1.3 5 1 7 1 7S.7 9.1.7 11.3v2c0 2.1.3 4.3.3 4.3s.3 2 1.2 2.8c1.1 1.2 2.6 1.1 3.3 1.2C7.5 21.7 12 21.7 12 21.7s4.4 0 6.8-.2c.6-.1 1.9-.1 3-1.3.9-.8 1.2-2.8 1.2-2.8s.3-2.1.3-4.3v-2C23.3 9.1 23 7 23 7zM9.7 15.5V8.3l8.1 3.6-8.1 3.6z"/></svg>'


def nav_html():
    if IS_PUBLIC:
        return '<nav><a class="nav-logo" href="/">Nipissing <span>Council Archive</span></a><ul class="nav-links"><li><a href="/">Home</a></li></ul></nav>'
    return '<nav><a class="nav-logo" href="https://chriswjohnston.ca">Chris <span>Johnston</span></a><ul class="nav-links"><li><a href="https://chriswjohnston.ca#priorities">Priorities</a></li><li><a href="https://chriswjohnston.ca#issues">Issues</a></li><li><a href="https://chriswjohnston.ca#contact">Contact</a></li><li><a href="https://council.chriswjohnston.ca" style="color:var(--warm);">Council Archive</a></li></ul></nav>'

def footer_html():
    if IS_PUBLIC:
        return f'<footer><div class="footer-inner"><div class="footer-logo">Nipissing <span>Council Archive</span></div><p>A community resource for Nipissing Township council meeting documents.</p><p>Sourced from <a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a> &mdash; Last updated {datetime.now().strftime("%B %d, %Y")}</p></div></footer>'
    return f'<footer><div class="footer-inner"><div class="footer-logo">Chris <span>Johnston</span></div><p>Candidate for Nipissing Township Council &middot; Municipal Election October 2026</p><p>Sourced from <a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a> &mdash; Last updated {datetime.now().strftime("%B %d, %Y")}</p></div></footer>'

def notice_text(count, year=None):
    yt_link = f'<a href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">Township of Nipissing YouTube channel</a>'
    src_link = f'<a href="{SOURCE_URL}" target="_blank" rel="noopener">nipissingtownship.com</a>'
    if year:
        return f"{count} documents available for {year}. Sourced from {src_link}. Videos via the {yt_link}. Last updated {datetime.now().strftime('%B %d, %Y')}."
    total_str = f"<strong>{count} documents</strong>"
    return f"{total_str} organized and indexed from {src_link}. Videos via the {yt_link}."


# ─── HTML HELPERS ──────────────────────────────────────────────

def sort_date_key(s):
    for fmt in ("%B %d, %Y", "%B %d %Y", "Special Meeting %B %d, %Y"):
        try: return datetime.strptime(s.strip(), fmt)
        except: pass
    return datetime.min

def classify_doc(label):
    l = label.lower()
    if "agenda package" in l or "package" in l: return "package"
    if "agenda" in l: return "agenda"
    if "minute" in l: return "minutes"
    return "other"

def date_slug(dt):
    c = re.sub(r"^special meeting\s+","",dt,flags=re.IGNORECASE).strip()
    c = re.sub(r"[^a-zA-Z0-9\s]","",c).strip()
    return re.sub(r"\s+","-",c).lower()

def doc_button(label, filename):
    return f'<a class="doc-link" href="files/{filename}" target="_blank" rel="noopener">{PDF_ICON} {label}</a>'

def yt_button(url):
    return f'<a class="doc-link youtube" href="{url}" target="_blank" rel="noopener">{YT_ICON} Watch</a>'

def get_yt_url(date_text, yt_videos):
    clean = re.sub(r"^special meeting\s+","",date_text,flags=re.IGNORECASE).strip()
    return (YOUTUBE_VIDEOS.get(date_text) or YOUTUBE_VIDEOS.get(clean)
         or yt_videos.get(date_text) or yt_videos.get(clean))


# ─── MEETING PAGE ──────────────────────────────────────────────

def generate_meeting_page(date_text, year, slots, yt_videos, summary):
    yt_url = get_yt_url(date_text, yt_videos)
    is_special = "special" in date_text.lower()
    badge = '<span class="special-badge">Special Meeting</span>' if is_special else ""

    meta = ""
    for d in slots.get("agenda",[]): meta += f'<a href="../files/{d["filename"]}" target="_blank">{PDF_ICON} Agenda</a>'
    for d in slots.get("minutes",[]): meta += f'<a href="../files/{d["filename"]}" target="_blank">{PDF_ICON} Minutes</a>'
    for d in slots.get("package",[]): meta += f'<a href="../files/{d["filename"]}" target="_blank">{PDF_ICON} Agenda Package</a>'
    if yt_url: meta += f'<a href="{yt_url}" target="_blank" class="yt-btn">{YT_ICON} Watch Meeting</a>'
    else: meta += f'<a href="{YOUTUBE_CHANNEL}" target="_blank" class="yt-btn">{YT_ICON} YouTube Channel</a>'

    def card(title, docs, color="var(--pine)"):
        if not docs: return ""
        links = "".join(f'<a class="doc-link" href="../files/{d["filename"]}" target="_blank" rel="noopener">{PDF_ICON} {d["label"]}</a>' for d in docs)
        return f'<div class="meeting-card" style="border-top-color:{color}"><h3>{title}</h3><div class="doc-links">{links}</div></div>'

    cards = (card("Agenda",slots["agenda"],"var(--forest)") +
             card("Minutes",slots["minutes"],"var(--pine)") +
             card("Agenda Package",slots["package"],"var(--water)") +
             card("Additional Files",slots["other"],"var(--warm)"))

    summary_html = ""
    if summary and not IS_PUBLIC:
        summary_html = f'<div class="summary-card"><h2>Meeting Summary <span class="ai-badge">AI Generated</span></h2>{render_summary_html(summary)}<p style="font-size:.75rem;color:#aaa;margin-top:1rem;">Generated automatically from meeting documents. Refer to source documents for authoritative information.</p></div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{date_text} Council Meeting – Nipissing Township</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <style>{CSS}</style>
</head>
<body>
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
<main>{summary_html}<div class="meeting-grid">{cards}</div></main>
{footer_html()}
</body></html>"""


# ─── YEAR PAGE ─────────────────────────────────────────────────

def generate_year_page(year, docs, all_years, yt_videos={}, summaries={}):
    grouped = defaultdict(list)
    for doc in docs:
        grouped[doc["date"]].append(doc)
    sorted_dates = sorted(grouped.keys(), key=sort_date_key)
    active = 'class="active"'
    year_nav = "".join(f'<a href="../{y}/" {active if y==year else ""}>{y}</a>' for y in sorted(all_years, reverse=True))

    rows = ""
    for date in sorted_dates:
        date_docs = grouped[date]
        is_special = "special" in date.lower() or any("special" in d["filename"].lower() for d in date_docs)
        row_cls = ' class="special-row"' if is_special else ""
        badge   = '<span class="special-badge">Special</span>' if is_special else ""
        slots   = {"agenda":[],"minutes":[],"package":[],"other":[]}
        for d in date_docs: slots[classify_doc(d["label"])].append(d)

        def cell(dl):
            if not dl: return '<td class="doc-cell"><span class="no-doc">&mdash;</span></td>'
            return f'<td class="doc-cell">{"".join(doc_button(d["label"],d["filename"]) for d in dl)}</td>'

        other_parts = [doc_button(d["label"],d["filename"]) for d in slots["other"]]
        other_cell = f'<td class="doc-cell"><div class="extra-docs">{"".join(other_parts)}</div></td>' if other_parts else '<td class="doc-cell"><span class="no-doc">&mdash;</span></td>'

        yt_url = get_yt_url(date, yt_videos)
        yt_cell = f'<td class="doc-cell">{yt_button(yt_url)}</td>' if yt_url else f'<td class="doc-cell"><a class="doc-link youtube" href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">{YT_ICON} Channel</a></td>'

        slug = date_slug(date)
        rows += f"""
      <tr{row_cls}>
        <td class="date-cell"><a href="{slug}/" class="date-link">{date}{badge}</a></td>
        {cell(slots["agenda"])}{cell(slots["minutes"])}{cell(slots["package"])}
        {other_cell}{yt_cell}
      </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{year} Council Meetings – Nipissing Township</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <style>{CSS}</style>
</head>
<body>
{nav_html()}
<div class="page-hero"><div class="inner">
  <p class="eyebrow">Nipissing Township &middot; Council Archive</p>
  <h1>{year} <em>Council Meetings</em></h1>
  <p>Agendas, minutes and agenda packages for {year} council meetings.</p>
  <div class="breadcrumb"><a href="../">All Years</a><span class="sep">/</span><span>{year}</span></div>
</div></div>
<main>
  <div class="notice">{notice_text(len(docs), year)}</div>
  <p class="section-label">Browse by year</p>
  <div class="year-nav" style="margin-bottom:1.5rem;">{year_nav}</div>
  <div class="table-wrapper">
    <table class="meetings-table">
      <thead><tr><th>Meeting Date</th><th>Agenda</th><th>Minutes</th><th>Agenda Package</th><th>Additional Files</th><th>Video</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</main>
{footer_html()}
</body></html>"""


# ─── INDEX PAGE ────────────────────────────────────────────────

def generate_index_page(meetings_by_year):
    years = sorted(meetings_by_year.keys(), reverse=True)
    total = sum(len(v) for v in meetings_by_year.values())
    cards = "".join(f'<a class="year-card" href="{y}/"><span class="yr">{y}</span><span class="count">{len(meetings_by_year[y])} documents</span></a>' for y in years)
    title = "Nipissing Township Council Archive" if IS_PUBLIC else "Council Meeting Archive"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <style>{CSS}</style>
</head>
<body>
{nav_html()}
<div class="page-hero"><div class="inner">
  <p class="eyebrow">Nipissing Township</p>
  <h1>Council Meeting <em>Archive</em></h1>
  <p>Agendas, minutes and agenda packages for Nipissing Township council meetings.</p>
</div></div>
<main>
  <div class="notice">{notice_text(total)}</div>
  <p class="section-label">Select a year</p>
  <div class="year-grid">{cards}</div>
</main>
{footer_html()}
</body></html>"""


# ─── BUILD ─────────────────────────────────────────────────────

def build_html(meetings, yt_videos={}):
    print(f"\nGenerating HTML pages (branch: {BRANCH}) ...")
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
        for doc in docs: grouped[doc["date"]].append(doc)

        for date_text, date_docs in grouped.items():
            slug = date_slug(date_text)
            (year_dir / slug).mkdir(exist_ok=True)
            slots = {"agenda":[],"minutes":[],"package":[],"other":[]}
            for d in date_docs: slots[classify_doc(d["label"])].append(d)

            summary_key = f"{year}/{slug}"
            summary = summaries.get(summary_key)
            has_both = bool(slots["minutes"] and slots["package"])
            if summary is None and has_both and ANTHROPIC_API_KEY and not IS_PUBLIC:
                print(f"  Generating AI summary for {date_text} ...")
                summary = generate_ai_summary(date_text, slots, year_files_dir)
                if summary:
                    summaries[summary_key] = summary
                    SUMMARIES_FILE.write_text(json.dumps(summaries, indent=2))

            (year_dir / slug / "index.html").write_text(
                generate_meeting_page(date_text, year, slots, yt_videos, summary), encoding="utf-8"
            )

        (year_dir / "index.html").write_text(
            generate_year_page(year, docs, all_years, yt_videos, summaries), encoding="utf-8"
        )
        print(f"  ✓ docs/{year}/index.html  ({len(docs)} docs)")

    (DOCS_DIR / "index.html").write_text(generate_index_page(meetings), encoding="utf-8")
    print(f"  ✓ docs/index.html")


# ─── MAIN ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print(f"Nipissing Council Archive — [{BRANCH.upper()} branch]")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    state = load_state()
    yt_videos = fetch_youtube_videos(state)
    meetings = fetch_links()
    print("\nDownloading new PDFs ...")
    new_count = download_pdfs(meetings, state)
    save_state(state)
    build_html(meetings, yt_videos)
    print(f"\n✓ Done. {new_count} new file(s) added." if new_count else "\n✓ Done. No new files.")
