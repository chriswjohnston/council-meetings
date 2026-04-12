"""
scraper_patches.py
==================
Three targeted patches to apply to scraper.py.

PATCH 1 — render_summary_html(): filter error summaries, keep existing logic
PATCH 2 — generate_year_page():  add upcoming meeting rows (dashed blue)
PATCH 3 — write_council_data_json(): include future meetings with is_future flag

Apply by finding each OLD block in scraper.py and replacing with the NEW block.
Search strings are unique enough to find with Ctrl+F / IDE search.
"""


# ══════════════════════════════════════════════════════════════════════════════
# PATCH 1 — render_summary_html()
# Finds and replaces the existing function. Adds error-message filtering.
# ══════════════════════════════════════════════════════════════════════════════

# ── FIND THIS (exact text, starting from def render_summary_html) ──────────
PATCH1_OLD = '''def render_summary_html(md):
    if not md:
        return ""
    lines, out, in_ul = md.split("\\n"), [], False
    for line in lines:
        line = line.strip()
        if not line:
            if in_ul: out.append("</ul>"); in_ul = False
            continue
        line = re.sub(r"\\*\\*(.+?)\\*\\*", r"<strong>\\1</strong>", line)
        if line.startswith(("- ","• ")):
            if not in_ul: out.append("<ul>"); in_ul = True
            out.append(f"<li>{line[2:].strip()}</li>")
        else:
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<p>{line}</p>")
    if in_ul: out.append("</ul>")
    return "\\n".join(out)'''

# ── REPLACE WITH THIS ────────────────────────────────────────────────────────
PATCH1_NEW = '''_SUMMARY_ERROR_PHRASES = [
    "I'm unable to read", "I apologize, but I'm unable",
    "I appreciate your request, but I'm unable",
    "I appreciate you sharing this", "unable to extract",
    "corrupted or", "compressed format", "unreadable",
    "improperly encoded", "I would need", "cannot decode",
    "cannot decompress", "doesn't convert", "not able to read",
]

def _is_error_summary(text):
    """Return True if text is an AI failure/error message rather than real content."""
    return text and any(p in text for p in _SUMMARY_ERROR_PHRASES)

def render_summary_html(md):
    """Convert markdown summary to HTML. Returns empty string for errors or None."""
    if not md or _is_error_summary(md):
        return ""
    lines, out, in_ul = md.split("\\n"), [], False
    for line in lines:
        s = line.strip()
        if not s:
            if in_ul: out.append("</ul>"); in_ul = False
            continue
        if s in ("---", "***", "___"):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append("<hr style=\\'border:none;border-top:1px solid #e0d8cc;margin:.75rem 0\\'>")
            continue
        # Inline: bold, italic
        s = re.sub(r"\\*\\*(.+?)\\*\\*", r"<strong>\\1</strong>", s)
        s = re.sub(r"\\*(.+?)\\*",       r"<em>\\1</em>",         s)
        # Headers — render as bold paragraph (keeps it compact in the card)
        if s.startswith("### "): s = f"<strong>{s[4:]}</strong>"
        elif s.startswith("## "): s = f"<strong>{s[3:]}</strong>"
        elif s.startswith("# "):  s = f"<strong>{s[2:]}</strong>"; s = s  # same treatment
        # List items
        if s.startswith(("- ", "• ", "* ")) or re.match(r"^\\d+\\.\\s", s):
            content = re.sub(r"^[-•*]\\s|^\\d+\\.\\s", "", s)
            if not in_ul: out.append("<ul>"); in_ul = True
            out.append(f"<li>{content}</li>")
        else:
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<p>{s}</p>")
    if in_ul: out.append("</ul>")
    return "\\n".join(out)'''


# ══════════════════════════════════════════════════════════════════════════════
# PATCH 2 — generate_year_page()
# Adds upcoming meeting rows at the top of the table with dashed blue styling.
# Also fixes meeting order: upcoming first (soonest), then past (newest→oldest).
# ══════════════════════════════════════════════════════════════════════════════

# ── FIND THIS (the rows-building block inside generate_year_page) ────────────
PATCH2_OLD = '''    rows = ""
    for date in sorted_dates:
        date_docs = grouped[date]
        is_special = "special" in date.lower() or any("special" in d["filename"].lower() for d in date_docs)
        row_cls = \' class="special-row"\' if is_special else ""
        badge   = \'<span class="special-badge">Special</span>\' if is_special else ""
        slots   = {"agenda":[],"minutes":[],"package":[],"other":[]}
        for d in date_docs: slots[classify_doc(d["label"])].append(d)

        def cell(dl):
            if not dl: return \'<td class="doc-cell"><span class="no-doc">&mdash;</span></td>\'
            return f\'<td class="doc-cell">{"".join(doc_button(d["label"],d["filename"],d) for d in dl)}</td>\'

        other_parts = [doc_button(d["label"],d["filename"],d) for d in slots["other"]]
        other_cell = f\'<td class="doc-cell"><div class="extra-docs">{"".join(other_parts)}</div></td>\' if other_parts else \'<td class="doc-cell"><span class="no-doc">&mdash;</span></td>\'

        yt_url = get_yt_url(date, yt_videos)
        yt_cell = f\'<td class="doc-cell">{yt_button(yt_url)}</td>\' if yt_url else f\'<td class="doc-cell"><a class="doc-link youtube" href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">{YT_ICON} Channel</a></td>\'

        slug = date_slug(date)
        rows += f"""
      <tr{row_cls}>
        <td class="date-cell"><a href="{slug}/" class="date-link">{date}{badge}</a></td>
        {cell(slots["agenda"])}{cell(slots["minutes"])}{cell(slots["package"])}
        {yt_cell}{other_cell}
      </tr>\""""'''

# ── REPLACE WITH THIS ────────────────────────────────────────────────────────
PATCH2_NEW = '''    today = datetime.now().date()

    # Separate upcoming (future) from past, then re-sort each group
    def _parsed(d):
        clean = re.sub(r"^Special Meeting\\s+", "", d, flags=re.IGNORECASE).strip()
        try: return datetime.strptime(clean, "%B %d, %Y").date()
        except: return datetime.min.date()

    upcoming_dates = sorted(
        [d for d in sorted_dates if _parsed(d) > today],
        key=_parsed          # soonest first
    )
    past_dates = sorted(
        [d for d in sorted_dates if _parsed(d) <= today],
        key=_parsed, reverse=True   # newest first
    )
    ordered_dates = upcoming_dates + past_dates

    # Upcoming row CSS — injected once per page via inline <style>
    upcoming_css = """<style>
.upcoming-meeting-row { background:#EAF5F8 !important; }
.upcoming-meeting-row td { border-bottom: 1.5px dashed #A8D5E2; }
.upcoming-meeting-row .date-link { color:#1a5a6e; border-bottom-color:#7bbcd4; }
.upcoming-banner-row td {
  background:#EAF5F8; padding:.45rem 1rem;
  font-size:.72rem; font-weight:700; letter-spacing:.06em;
  text-transform:uppercase; color:#1a5a6e;
  border-bottom:1px solid #A8D5E2;
}
</style>"""

    rows = upcoming_css if upcoming_dates else ""
    first_past = True

    for date in ordered_dates:
        date_docs = grouped[date]
        is_upcoming = _parsed(date) > today
        is_special = "special" in date.lower() or any("special" in d.get("filename","").lower() for d in date_docs)

        # Insert section dividers
        if is_upcoming and date == upcoming_dates[0]:
            rows += f\'\'\'<tr class="upcoming-banner-row"><td colspan="6">&#128197; Upcoming meetings — documents will appear once published</td></tr>\'\'\'
        if not is_upcoming and first_past:
            first_past = False
            if upcoming_dates:
                rows += \'<tr><td colspan="6" style="padding:.35rem 1rem;font-size:.62rem;font-weight:700;letter-spacing:.22em;text-transform:uppercase;color:#6e6e6e;background:#F2EAD3;border-bottom:1px solid #d8d0c8;">Past meetings</td></tr>\'

        row_cls = \' class="upcoming-meeting-row"\' if is_upcoming else (\' class="special-row"\' if is_special else "")
        badge   = \'<span class="special-badge">Special</span>\' if is_special else ""
        slots   = {"agenda":[],"minutes":[],"package":[],"other":[]}
        for d in date_docs: slots[classify_doc(d["label"])].append(d)

        def cell(dl):
            if not dl: return \'<td class="doc-cell"><span class="no-doc">&mdash;</span></td>\'
            return f\'<td class="doc-cell">{"".join(doc_button(d["label"],d["filename"],d) for d in dl)}</td>\'

        other_parts = [doc_button(d["label"],d["filename"],d) for d in slots["other"]]
        other_cell = f\'<td class="doc-cell"><div class="extra-docs">{"".join(other_parts)}</div></td>\' if other_parts else \'<td class="doc-cell"><span class="no-doc">&mdash;</span></td>\'

        yt_url = get_yt_url(date, yt_videos)
        yt_cell = (f\'<td class="doc-cell">{yt_button(yt_url)}</td>\' if yt_url
                   else (\'<td class="doc-cell"><span class="no-doc">&mdash;</span></td>\' if is_upcoming
                         else f\'<td class="doc-cell"><a class="doc-link youtube" href="{YOUTUBE_CHANNEL}" target="_blank" rel="noopener">{YT_ICON} Channel</a></td>\'))

        slug = date_slug(date)
        rows += f"""
      <tr{row_cls}>
        <td class="date-cell"><a href="{slug}/" class="date-link">{date}{badge}</a></td>
        {cell(slots["agenda"])}{cell(slots["minutes"])}{cell(slots["package"])}
        {yt_cell}{other_cell}
      </tr>\""""'''


# ══════════════════════════════════════════════════════════════════════════════
# PATCH 3 — write_council_data_json()
# Adds is_future flag so build_index.py can show upcoming pill on year cards.
# ══════════════════════════════════════════════════════════════════════════════

# ── FIND THIS (inside write_council_data_json, the out.append block) ─────────
PATCH3_OLD = '''            out.append({
                "date":         iso_date,
                "display_date": display_date,
                "year":         int(year),
                "meeting_type": meeting_type,
                "title":        "",
                "agenda_url":   agenda_url,
                "minutes_url":  minutes_url,
                "package_url":  package_url,
                "video_url":    video_url,
                "summary":      None,
                "cancelled":    False,
            })'''

# ── REPLACE WITH THIS ────────────────────────────────────────────────────────
PATCH3_NEW = '''            out.append({
                "date":         iso_date,
                "display_date": display_date,
                "year":         int(year),
                "is_future":    (parsed > today if isinstance(parsed, type(today)) else False),
                "meeting_type": meeting_type,
                "title":        "",
                "agenda_url":   agenda_url,
                "minutes_url":  minutes_url,
                "package_url":  package_url,
                "video_url":    video_url,
                "summary":      None,
                "cancelled":    False,
            })'''


# ══════════════════════════════════════════════════════════════════════════════
# HOW TO APPLY
# ══════════════════════════════════════════════════════════════════════════════
#
# Run this script from the repo root to apply all three patches automatically:
#
#   python3 scraper_patches.py
#
# Or apply manually: open scraper.py, Ctrl+F for the OLD text, replace with NEW.
#
# After patching, run: python3 scraper.py
# The year pages will now show upcoming meetings at the top.
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    from pathlib import Path

    target = Path("scraper.py")
    if not target.exists():
        print("ERROR: scraper.py not found. Run from repo root.")
        sys.exit(1)

    content = target.read_text(encoding="utf-8")
    original = content

    patches = [
        ("PATCH 1 — render_summary_html", PATCH1_OLD, PATCH1_NEW),
        ("PATCH 2 — generate_year_page rows", PATCH2_OLD, PATCH2_NEW),
        ("PATCH 3 — write_council_data_json is_future", PATCH3_OLD, PATCH3_NEW),
    ]

    all_ok = True
    for name, old, new in patches:
        # Normalise whitespace for matching (handle minor indentation variations)
        if old in content:
            content = content.replace(old, new, 1)
            print(f"  ✓ Applied: {name}")
        else:
            print(f"  ✗ NOT FOUND: {name}")
            print(f"    → Apply manually: search for the first ~3 lines of PATCH*_OLD in scraper.py")
            all_ok = False

    if content != original:
        # Back up original
        backup = Path("scraper.py.bak")
        backup.write_text(original, encoding="utf-8")
        print(f"\n  Backup saved to scraper.py.bak")
        target.write_text(content, encoding="utf-8")
        print(f"  scraper.py updated")
    else:
        print("\n  No changes made (patches already applied or not found)")

    if all_ok:
        print("\n✓ All patches applied. Run: python3 scraper.py")
    else:
        print("\n⚠ Some patches need manual application (see above)")
