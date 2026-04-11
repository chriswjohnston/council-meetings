#!/usr/bin/env python3
"""
build_index.py — council.chriswjohnston.ca
==========================================
Assembles the unified SPA docs/index.html from:
  - docs/council-data.json   (written by scraper.py)
  - docs/boards-data.json    (written by scrape_boards.py)

Run after both scrapers complete, or trigger independently.
Writes a single self-contained docs/index.html with all data embedded.

SPA features (matching bylaw.chriswjohnston.ca pattern):
  - Four tabs: Council · Recreation Committee · Museum Board · Cemetery Committee
  - Keyword search (filters across date, summary, doc types)
  - Year selector
  - URL hash deep linking: #council/2026, #museum/2025, etc.
  - Future meetings styled distinctly with dashed border
  - Document links: Agenda · Minutes · Package · Video
"""

import json
from datetime import datetime, date
from pathlib import Path

DOCS = Path("docs")
TODAY = date.today().isoformat()


def load_council() -> list[dict]:
    path = DOCS / "council-data.json"
    if not path.exists():
        print("  ⚠ docs/council-data.json not found — council tab will be empty")
        return []
    data = json.loads(path.read_text())
    meetings = data.get("meetings", data) if isinstance(data, dict) else data
    # Normalise fields
    out = []
    for m in meetings:
        out.append({
            "date":         m.get("date", ""),
            "display_date": m.get("display_date", m.get("date", "")),
            "year":         m.get("year", int(m.get("date", "0")[:4])),
            "is_future":    m.get("date", "") > TODAY,
            "title":        m.get("title", ""),
            "meeting_type": m.get("meeting_type", "Regular"),
            "agenda_url":   m.get("agenda_url"),
            "minutes_url":  m.get("minutes_url"),
            "package_url":  m.get("package_url"),
            "video_url":    m.get("video_url"),
            "summary":      m.get("summary"),
            "cancelled":    m.get("cancelled", False),
            "tab":          "council",
        })
    return sorted(out, key=lambda x: x["date"], reverse=True)


def load_boards() -> dict[str, list[dict]]:
    path = DOCS / "boards-data.json"
    if not path.exists():
        # Fall back to per-board data.json files
        boards = {}
        for board_id in ["recreation", "museum", "cemetery"]:
            p = DOCS / "boards" / board_id / "data.json"
            if p.exists():
                d = json.loads(p.read_text())
                boards[board_id] = d.get("meetings", [])
        if not boards:
            print("  ⚠ docs/boards-data.json not found — board tabs will be empty")
        return boards

    data = json.loads(path.read_text())
    out = {}
    for board in data.get("boards", []):
        bid = board["id"]
        meetings = []
        for m in board.get("meetings", []):
            meetings.append({
                "date":         m.get("date", ""),
                "display_date": m.get("display_date", m.get("date", "")),
                "year":         m.get("year", int(m.get("date", "0")[:4])),
                "is_future":    m.get("is_future", m.get("date", "") > TODAY),
                "agenda_url":   m.get("agenda_url"),
                "minutes_url":  m.get("minutes_url"),
                "package_url":  m.get("package_url"),
                "cancelled":    m.get("cancelled", False),
                "postponed":    m.get("postponed", False),
                "rescheduled":  m.get("rescheduled", False),
                "summary":      m.get("summary"),
                "tab":          bid,
            })
        out[bid] = sorted(meetings, key=lambda x: x["date"], reverse=True)
    return out


def build_spa(council: list[dict], boards: dict[str, list[dict]]) -> str:
    all_data = {
        "council":    council,
        "recreation": boards.get("recreation", []),
        "museum":     boards.get("museum", []),
        "cemetery":   boards.get("cemetery", []),
    }

    council_count = len(council)
    rec_count = len(boards.get("recreation", []))
    museum_count = len(boards.get("museum", []))
    cem_count = len(boards.get("cemetery", []))
    total = council_count + rec_count + museum_count + cem_count

    last_updated = datetime.now().strftime("%B %d, %Y")

    data_json = json.dumps(all_data, separators=(",", ":"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nipissing Township Council Archive · council.chriswjohnston.ca</title>
<meta name="description" content="Agendas, minutes, and meeting records for Nipissing Township Council and all boards and committees — searchable and permanently preserved.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,600;0,700;1,600&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --forest: #2C4A3E;
  --pine:   #3D6B5E;
  --pine-lt:#e8f0eb;
  --warm:   #E8C98A;
  --rust:   #C06830;
  --sky:    #A8D5E2;
  --sky-lt: #EAF5F8;
  --ink:    #1c1c1c;
  --body:   #3a3a3a;
  --muted:  #6e6e6e;
  --rule:   #d8d0c8;
  --bg:     #FAF7F0;
  --white:  #ffffff;
  --sand:   #F2EAD3;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{font-family:'DM Sans',system-ui,sans-serif;background:var(--bg);color:var(--body);line-height:1.6;min-height:100vh}}

/* ── NAV ── */
nav{{
  background:var(--forest);
  padding:0 2rem;
  display:flex;align-items:center;justify-content:space-between;
  height:56px;position:sticky;top:0;z-index:100;
  box-shadow:0 2px 12px rgba(0,0,0,0.18);
}}
.nav-logo{{
  font-family:'Playfair Display',serif;font-size:1rem;
  color:var(--warm);text-decoration:none;letter-spacing:0.02em;
}}
.nav-links{{display:flex;gap:1.5rem;list-style:none}}
.nav-links a{{
  color:rgba(255,255,255,0.72);text-decoration:none;
  font-size:0.78rem;font-weight:500;letter-spacing:0.06em;
  text-transform:uppercase;transition:color 0.2s;
}}
.nav-links a:hover{{color:var(--warm)}}

/* ── HERO ── */
.hero{{
  background:var(--forest);
  padding:3rem 2rem 2.5rem;
  position:relative;overflow:hidden;
}}
.hero::after{{
  content:'';position:absolute;bottom:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--warm) 0%,transparent 55%);
}}
.hero-inner{{max-width:1040px;margin:0 auto}}
.hero-eyebrow{{
  font-size:0.68rem;font-weight:700;letter-spacing:0.28em;
  text-transform:uppercase;color:var(--warm);margin-bottom:0.75rem;
  display:flex;align-items:center;gap:0.6rem;
}}
.hero-eyebrow::before{{
  content:'';display:block;width:24px;height:2px;background:var(--warm);
}}
.hero h1{{
  font-family:'Playfair Display',serif;
  font-size:clamp(1.7rem,3.5vw,2.6rem);
  color:#fff;line-height:1.15;margin-bottom:0.6rem;font-weight:700;
}}
.hero h1 em{{font-style:italic;color:rgba(255,255,255,0.6)}}
.hero-sub{{
  font-size:0.9rem;color:rgba(255,255,255,0.65);
  font-weight:300;max-width:580px;line-height:1.7;
}}

/* ── STATS BAR ── */
.stats-bar{{
  background:var(--white);border-bottom:1px solid var(--rule);
  padding:0.85rem 2rem;
  display:flex;gap:2.5rem;align-items:center;flex-wrap:wrap;
}}
.stats-bar-inner{{max-width:1040px;margin:0 auto;width:100%;display:flex;gap:2.5rem;flex-wrap:wrap;align-items:center}}
.stat-item strong{{
  font-family:'Playfair Display',serif;font-size:1.25rem;
  color:var(--forest);font-weight:700;margin-right:0.3rem;
}}
.stat-item span{{font-size:0.75rem;color:var(--muted);font-weight:500}}
.stat-divider{{width:1px;height:24px;background:var(--rule)}}
.updated{{font-size:0.72rem;color:var(--muted);margin-left:auto}}

/* ── MAIN LAYOUT ── */
.main{{max-width:1040px;margin:0 auto;padding:1.5rem 2rem 5rem}}

/* ── TABS ── */
.tabs{{
  display:flex;gap:0;border-bottom:2px solid var(--rule);
  margin-bottom:1.5rem;overflow-x:auto;
}}
.tab-btn{{
  padding:0.75rem 1.4rem;
  font-size:0.82rem;font-weight:600;letter-spacing:0.04em;
  border:none;background:none;cursor:pointer;
  color:var(--muted);border-bottom:3px solid transparent;
  margin-bottom:-2px;white-space:nowrap;transition:all 0.18s;
  font-family:'DM Sans',sans-serif;
}}
.tab-btn:hover{{color:var(--forest)}}
.tab-btn.active{{color:var(--forest);border-bottom-color:var(--forest)}}
.tab-btn .tab-count{{
  display:inline-block;margin-left:0.35rem;
  font-size:0.65rem;font-weight:700;
  background:var(--pine-lt);color:var(--pine);
  padding:1px 5px;border-radius:10px;
}}
.tab-btn.active .tab-count{{background:var(--forest);color:#fff}}

/* ── CONTROLS ── */
.controls{{
  display:flex;gap:0.75rem;margin-bottom:1.25rem;flex-wrap:wrap;
  align-items:center;
}}
.search-wrap{{
  position:relative;flex:1;min-width:200px;max-width:380px;
}}
.search-wrap::before{{
  content:'⌕';position:absolute;left:10px;top:50%;transform:translateY(-50%);
  color:var(--muted);font-size:1rem;pointer-events:none;
}}
#search{{
  width:100%;padding:0.55rem 0.75rem 0.55rem 2rem;
  border:1px solid var(--rule);border-radius:6px;
  background:var(--white);font-size:0.85rem;font-family:'DM Sans',sans-serif;
  color:var(--ink);outline:none;transition:border-color 0.2s;
}}
#search:focus{{border-color:var(--pine)}}
#search::placeholder{{color:var(--muted)}}

.year-select{{
  padding:0.55rem 0.75rem;border:1px solid var(--rule);border-radius:6px;
  background:var(--white);font-size:0.85rem;font-family:'DM Sans',sans-serif;
  color:var(--ink);cursor:pointer;outline:none;transition:border-color 0.2s;
}}
.year-select:focus{{border-color:var(--pine)}}

.results-count{{
  font-size:0.75rem;color:var(--muted);margin-left:auto;white-space:nowrap;
}}

/* ── SOURCE LINK ── */
.source-note{{
  font-size:0.72rem;color:var(--muted);margin-bottom:1.25rem;
  padding:0.6rem 0.9rem;background:var(--pine-lt);border-radius:5px;
  border-left:3px solid var(--pine);
}}
.source-note a{{color:var(--pine)}}

/* ── MEETING LIST ── */
.meeting-list{{display:flex;flex-direction:column;gap:0}}

.meeting-row{{
  display:grid;grid-template-columns:140px 1fr auto;
  gap:0.75rem 1rem;align-items:start;
  padding:0.9rem 0;border-bottom:1px solid var(--rule);
  transition:background 0.1s;
}}
.meeting-row:last-child{{border-bottom:none}}
.meeting-row:hover{{background:rgba(44,74,62,0.025);margin:0 -0.5rem;padding-left:0.5rem;padding-right:0.5rem;border-radius:4px}}

/* FUTURE meetings — sky blue dashed */
.meeting-row.upcoming{{
  background:var(--sky-lt);
  border:1.5px dashed var(--sky);
  border-radius:6px;
  padding:0.9rem 0.9rem;
  margin-bottom:3px;
}}
.meeting-row.upcoming:hover{{background:var(--sky-lt)}}

.meeting-row.cancelled .m-date,
.meeting-row.postponed .m-date{{
  text-decoration:line-through;color:var(--muted);
}}

/* Date column */
.m-date{{
  font-size:0.82rem;font-weight:600;color:var(--ink);
  padding-top:3px;font-family:'DM Mono',monospace;letter-spacing:0.01em;
}}
.meeting-row.upcoming .m-date{{color:#1a6a80;font-weight:700}}

/* Info column */
.m-info{{display:flex;flex-direction:column;gap:0.25rem}}
.m-badges{{display:flex;gap:0.35rem;flex-wrap:wrap;align-items:center}}

.badge{{
  display:inline-block;font-size:0.58rem;font-weight:700;
  letter-spacing:0.1em;text-transform:uppercase;
  padding:2px 7px;border-radius:3px;
}}
.badge-upcoming{{background:var(--sky);color:#fff}}
.badge-full{{background:var(--pine-lt);color:var(--pine)}}
.badge-agenda{{background:var(--sand);color:#8a6820}}
.badge-cancelled{{background:#f0f0f0;color:var(--muted)}}
.badge-postponed{{background:#f5f0ff;color:#6b52a0}}
.badge-rescheduled{{background:#fff3e0;color:#b86000}}
.badge-type{{background:rgba(44,74,62,0.08);color:var(--forest);font-weight:500}}

.m-title{{font-size:0.85rem;color:var(--body);font-weight:500}}
.m-summary{{
  font-size:0.8rem;line-height:1.6;color:var(--muted);
  margin-top:0.2rem;
}}

/* Links column */
.m-links{{
  display:flex;flex-direction:column;gap:4px;min-width:80px;align-items:flex-end;
}}
.doc-link{{
  font-size:0.68rem;font-weight:700;letter-spacing:0.06em;
  text-decoration:none;padding:3px 9px;border-radius:3px;
  text-align:center;white-space:nowrap;display:block;
  transition:opacity 0.15s;
}}
.doc-link:hover{{opacity:0.82}}
.doc-agenda{{background:var(--forest);color:#fff}}
.doc-minutes{{background:var(--rust);color:#fff}}
.doc-package{{background:#666;color:#fff}}
.doc-video{{background:#1a1a1a;color:#fff}}

/* ── YEAR GROUPS ── */
.year-heading{{
  font-size:0.62rem;font-weight:700;letter-spacing:0.24em;
  text-transform:uppercase;color:var(--muted);
  padding:0.5rem 0 0.4rem;border-bottom:2px solid var(--rule);
  margin:1.5rem 0 0;
}}
.year-heading:first-child{{margin-top:0}}

/* ── EMPTY STATE ── */
.empty-state{{
  padding:3rem 0;text-align:center;color:var(--muted);
  font-size:0.9rem;
}}

/* ── FOOTER ── */
footer{{
  background:#1E2B2A;color:rgba(255,255,255,0.35);
  text-align:center;padding:1.5rem 2rem;font-size:0.75rem;
}}
footer a{{color:var(--warm);text-decoration:none}}

/* ── RESPONSIVE ── */
@media(max-width:680px){{
  .meeting-row{{grid-template-columns:1fr;gap:0.4rem}}
  .m-links{{flex-direction:row;flex-wrap:wrap;align-items:flex-start;min-width:0}}
  .stats-bar-inner{{gap:1rem}}
  nav{{padding:0 1rem}}
  .main{{padding:1.25rem 1rem 4rem}}
  .stats-bar{{padding:0.75rem 1rem}}
  .hero{{padding:2.5rem 1rem 2rem}}
  .controls{{gap:0.5rem}}
  .search-wrap{{max-width:100%}}
  .tab-btn{{padding:0.65rem 0.9rem;font-size:0.75rem}}
}}
</style>
</head>
<body>

<nav>
  <a class="nav-logo" href="/">Nipissing Council Archive</a>
  <ul class="nav-links">
    <li><a href="https://bylaw.chriswjohnston.ca">By-Law Archive</a></li>
    <li><a href="https://chriswjohnston.ca">Chris Johnston</a></li>
  </ul>
</nav>

<div class="hero">
  <div class="hero-inner">
    <div class="hero-eyebrow">Nipissing Township · Public Records</div>
    <h1>Council &amp; Boards <em>Archive</em></h1>
    <p class="hero-sub">
      Agendas, minutes, and meeting records for Nipissing Township Council,
      Recreation Committee, Museum Board, and Cemetery Committee —
      searchable, permanently preserved, and automatically updated.
    </p>
  </div>
</div>

<div class="stats-bar">
  <div class="stats-bar-inner">
    <div class="stat-item">
      <strong id="stat-total">{total}</strong>
      <span>meetings</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <strong>{council_count}</strong>
      <span>council</span>
    </div>
    <div class="stat-item">
      <strong>{rec_count + museum_count + cem_count}</strong>
      <span>boards &amp; committees</span>
    </div>
    <div class="updated">Updated {last_updated}</div>
  </div>
</div>

<div class="main">

  <!-- TABS -->
  <div class="tabs" role="tablist">
    <button class="tab-btn active" data-tab="council" role="tab" aria-selected="true">
      Council <span class="tab-count">{council_count}</span>
    </button>
    <button class="tab-btn" data-tab="recreation" role="tab">
      Recreation Committee <span class="tab-count">{rec_count}</span>
    </button>
    <button class="tab-btn" data-tab="museum" role="tab">
      Museum Board <span class="tab-count">{museum_count}</span>
    </button>
    <button class="tab-btn" data-tab="cemetery" role="tab">
      Cemetery Committee <span class="tab-count">{cem_count}</span>
    </button>
  </div>

  <!-- CONTROLS -->
  <div class="controls">
    <div class="search-wrap">
      <input id="search" type="search" placeholder="Search meetings, summaries…" autocomplete="off">
    </div>
    <select id="year-select" class="year-select">
      <option value="">All years</option>
    </select>
    <div class="results-count" id="results-count"></div>
  </div>

  <!-- SOURCE NOTE (switches per tab) -->
  <div class="source-note" id="source-note"></div>

  <!-- MEETING LIST -->
  <div class="meeting-list" id="meeting-list" role="list"></div>

</div>

<footer>
  <a href="/">council.chriswjohnston.ca</a> ·
  Sourced from <a href="https://nipissingtownship.com" target="_blank">nipissingtownship.com</a> ·
  Built by <a href="https://chriswjohnston.ca">Chris Johnston</a>, Nipissing Township Council Candidate 2026
</footer>

<script>
const DATA = {data_json};

const SOURCES = {{
  council:    'Sourced from <a href="https://nipissingtownship.com/council-meeting-dates-agendas-minutes/" target="_blank">nipissingtownship.com · Council Meeting Dates, Agendas &amp; Minutes</a>. Updated automatically every two weeks.',
  recreation: 'Sourced from <a href="https://nipissingtownship.com/services/recreation/" target="_blank">nipissingtownship.com · Recreation</a>. Governed by By-Law 2023-09. Updated daily.',
  museum:     'Sourced from <a href="https://nipissingtownship.com/services/museum-services-and-information/" target="_blank">nipissingtownship.com · Museum</a>. Governed by By-Law 2023-10. Updated daily.',
  cemetery:   'Sourced from <a href="https://nipissingtownship.com/services/cemetery/" target="_blank">nipissingtownship.com · Cemetery</a>. Governed by By-Law 2023-11. Updated daily.',
}};

let currentTab = 'council';
let currentYear = '';
let currentSearch = '';

// ── Hash routing ──────────────────────────────────────────────────────
function parseHash() {{
  const h = location.hash.slice(1); // e.g. "council/2026" or "museum"
  if (!h) return;
  const [tab, year] = h.split('/');
  if (DATA[tab]) {{
    currentTab = tab;
    currentYear = year || '';
  }}
}}

function updateHash() {{
  const h = currentYear ? `${{currentTab}}/${{currentYear}}` : currentTab;
  history.replaceState(null, '', '#' + h);
}}

// ── Year options ──────────────────────────────────────────────────────
function populateYears(tab) {{
  const years = [...new Set(DATA[tab].map(m => m.year))].sort((a,b) => b - a);
  const sel = document.getElementById('year-select');
  sel.innerHTML = '<option value="">All years</option>';
  years.forEach(y => {{
    const opt = document.createElement('option');
    opt.value = y;
    opt.textContent = y;
    if (String(y) === String(currentYear)) opt.selected = true;
    sel.appendChild(opt);
  }});
}}

// ── Render ────────────────────────────────────────────────────────────
function render() {{
  const list = document.getElementById('meeting-list');
  const countEl = document.getElementById('results-count');
  document.getElementById('source-note').innerHTML = SOURCES[currentTab];

  let meetings = DATA[currentTab];

  // Year filter
  if (currentYear) {{
    meetings = meetings.filter(m => String(m.year) === String(currentYear));
  }}

  // Search filter
  const q = currentSearch.toLowerCase().trim();
  if (q) {{
    meetings = meetings.filter(m => {{
      const haystack = [
        m.display_date, m.title || '', m.summary || '',
        m.meeting_type || '',
      ].join(' ').toLowerCase();
      return q.split(' ').every(word => haystack.includes(word));
    }});
  }}

  countEl.textContent = meetings.length === 1
    ? '1 meeting'
    : `${{meetings.length}} meetings`;

  if (meetings.length === 0) {{
    list.innerHTML = '<div class="empty-state">No meetings found. Try adjusting your search or year filter.</div>';
    return;
  }}

  // Group by year (only when no specific year selected and no search)
  const showYearHeadings = !currentYear && !q;

  let html = '';
  let lastYear = null;

  meetings.forEach(m => {{
    if (showYearHeadings && m.year !== lastYear) {{
      html += `<div class="year-heading">${{m.year}}</div>`;
      lastYear = m.year;
    }}

    // Row classes
    let rowClass = 'meeting-row';
    if (m.is_future && !m.cancelled) rowClass += ' upcoming';
    if (m.cancelled) rowClass += ' cancelled';
    if (m.postponed) rowClass += ' postponed';

    // Badges
    let badges = '';
    if (m.is_future && !m.cancelled && !m.postponed) {{
      badges += '<span class="badge badge-upcoming">Upcoming</span>';
    }} else if (m.cancelled) {{
      badges += '<span class="badge badge-cancelled">Cancelled</span>';
    }} else if (m.postponed) {{
      badges += '<span class="badge badge-postponed">Postponed</span>';
    }} else if (m.rescheduled) {{
      badges += '<span class="badge badge-rescheduled">Rescheduled</span>';
    }} else if (m.minutes_url) {{
      badges += '<span class="badge badge-full">Minutes Available</span>';
    }} else if (m.agenda_url) {{
      badges += '<span class="badge badge-agenda">Agenda Only</span>';
    }}
    if (m.meeting_type && m.meeting_type !== 'Regular') {{
      badges += `<span class="badge badge-type">${{m.meeting_type}}</span>`;
    }}

    // Title (council has titles, boards don't)
    const titleHtml = m.title
      ? `<div class="m-title">${{m.title}}</div>`
      : '';

    // Summary
    const summaryHtml = m.summary
      ? `<div class="m-summary">${{m.summary}}</div>`
      : '';

    // Doc links
    let links = '';
    if (m.agenda_url)  links += `<a href="${{m.agenda_url}}"  class="doc-link doc-agenda"  target="_blank" rel="noopener">Agenda</a>`;
    if (m.minutes_url) links += `<a href="${{m.minutes_url}}" class="doc-link doc-minutes" target="_blank" rel="noopener">Minutes</a>`;
    if (m.package_url) links += `<a href="${{m.package_url}}" class="doc-link doc-package" target="_blank" rel="noopener">Package</a>`;
    if (m.video_url)   links += `<a href="${{m.video_url}}"   class="doc-link doc-video"   target="_blank" rel="noopener">▶ Video</a>`;

    html += `
<div class="${{rowClass}}" role="listitem">
  <div class="m-date">${{m.display_date}}</div>
  <div class="m-info">
    <div class="m-badges">${{badges}}</div>
    ${{titleHtml}}
    ${{summaryHtml}}
  </div>
  <div class="m-links">${{links}}</div>
</div>`;
  }});

  list.innerHTML = html;
}}

// ── Tab switching ─────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    currentTab = btn.dataset.tab;
    currentYear = '';
    currentSearch = '';
    document.getElementById('search').value = '';
    document.querySelectorAll('.tab-btn').forEach(b => {{
      b.classList.toggle('active', b === btn);
      b.setAttribute('aria-selected', b === btn ? 'true' : 'false');
    }});
    populateYears(currentTab);
    updateHash();
    render();
  }});
}});

// ── Year select ───────────────────────────────────────────────────────
document.getElementById('year-select').addEventListener('change', e => {{
  currentYear = e.target.value;
  updateHash();
  render();
}});

// ── Search ────────────────────────────────────────────────────────────
let searchTimer;
document.getElementById('search').addEventListener('input', e => {{
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {{
    currentSearch = e.target.value;
    render();
  }}, 180);
}});

// ── Init ──────────────────────────────────────────────────────────────
parseHash();

// Activate the correct tab button
document.querySelectorAll('.tab-btn').forEach(btn => {{
  const isActive = btn.dataset.tab === currentTab;
  btn.classList.toggle('active', isActive);
  btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
}});

populateYears(currentTab);
render();

// Handle browser back/forward
window.addEventListener('hashchange', () => {{
  parseHash();
  document.querySelectorAll('.tab-btn').forEach(btn => {{
    const isActive = btn.dataset.tab === currentTab;
    btn.classList.toggle('active', isActive);
  }});
  populateYears(currentTab);
  render();
}});
</script>
</body>
</html>"""


def main():
    print("=" * 50)
    print("Building council.chriswjohnston.ca SPA index")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    council = load_council()
    boards = load_boards()

    print(f"  Council meetings:    {len(council)}")
    for bid, meetings in boards.items():
        print(f"  {bid.capitalize()}: {len(meetings)}")

    DOCS.mkdir(exist_ok=True)
    html = build_spa(council, boards)
    (DOCS / "index.html").write_text(html)
    print(f"\n✓ Written: docs/index.html ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
