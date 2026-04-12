"""
meeting_summary_fix.py
======================
Drop this file into the repo root and import it wherever summaries are rendered.

Provides two things:
  1. is_error_summary(text)  — detects AI failure messages stored in the data
  2. render_summary(text)    — converts markdown summary to clean HTML,
                               returns None for errors or empty summaries

Usage in scrape_boards.py build_board_html():
    from meeting_summary_fix import render_summary
    ...
    summ_html = render_summary(m.get("summary"))
    summ = f'<div class="summary">{summ_html}</div>' if summ_html else ""

Usage in your council meeting detail page template:
    from meeting_summary_fix import render_summary
    ...
    summary_html = render_summary(meeting.get("summary"))
    if summary_html:
        page += f'<div class="summary-card">...<div class="summary-body">{summary_html}</div></div>'
"""

import re

# ── Error detection ───────────────────────────────────────────────────────────

_ERROR_PHRASES = [
    "I'm unable to read",
    "I apologize, but I'm unable",
    "I appreciate your request, but I'm unable",
    "I appreciate you sharing this",
    "unable to extract",
    "corrupted or",
    "compressed format",
    "unreadable",
    "improperly encoded",
    "I would need",
    "cannot decode",
    "cannot decompress",
    "doesn't convert",
    "not able to read",
    "unable to read the content",
]


def is_error_summary(text: str) -> bool:
    """Return True if the summary is an AI error / failure message."""
    if not text:
        return False
    return any(phrase in text for phrase in _ERROR_PHRASES)


# ── Markdown → HTML ───────────────────────────────────────────────────────────

def _inline(text: str) -> str:
    """Convert inline markdown: **bold**, *italic*, `code`."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         text)
    text = re.sub(r'`(.+?)`',       r'<code>\1</code>',     text)
    return text


def _markdown_to_html(text: str) -> str:
    """
    Minimal but complete markdown-to-HTML converter for meeting summaries.
    Handles: h1/h2/h3 headers, bold/italic, bullet lists, numbered lists,
    horizontal rules, and paragraphs.
    """
    lines   = text.split('\n')
    out     = []
    in_ul   = False

    def close_list():
        nonlocal in_ul
        if in_ul:
            out.append('</ul>')
            in_ul = False

    for line in lines:
        s = line.strip()

        if not s:
            close_list()
            continue

        if s in ('---', '***', '___'):
            close_list()
            out.append('<hr>')
            continue

        if s.startswith('### '):
            close_list()
            out.append(f'<h5>{_inline(s[4:])}</h5>')
            continue

        if s.startswith('## '):
            close_list()
            out.append(f'<h4>{_inline(s[3:])}</h4>')
            continue

        if s.startswith('# '):
            close_list()
            out.append(f'<h3>{_inline(s[2:])}</h3>')
            continue

        m = re.match(r'^[-*]\s+(.*)', s)
        if m:
            if not in_ul:
                out.append('<ul>')
                in_ul = True
            out.append(f'<li>{_inline(m.group(1))}</li>')
            continue

        m = re.match(r'^\d+\.\s+(.*)', s)
        if m:
            if not in_ul:
                out.append('<ul>')
                in_ul = True
            out.append(f'<li>{_inline(m.group(1))}</li>')
            continue

        close_list()
        out.append(f'<p>{_inline(s)}</p>')

    close_list()
    return '\n'.join(out)


def render_summary(raw: str | None) -> str | None:
    """
    Main entry point.

    Returns rendered HTML string, or None if:
      - raw is None / empty
      - raw is an AI error/failure message

    The caller decides whether to show a summary card at all.
    """
    if not raw or not raw.strip():
        return None
    if is_error_summary(raw):
        return None
    return _markdown_to_html(raw.strip())


# ── CSS to add to your summary-card block ────────────────────────────────────
#
# Add these rules alongside your existing .summary-card styles:
#
#   .summary-card h3 { font-family:'Playfair Display',serif; font-size:1rem;
#     color:var(--forest); margin:1.1rem 0 .45rem; font-weight:700; }
#   .summary-card h4 { font-size:.93rem; font-weight:700; color:var(--forest);
#     margin:.9rem 0 .35rem; text-transform:uppercase; letter-spacing:.05em; }
#   .summary-card h5 { font-size:.87rem; font-weight:700; color:var(--pine);
#     margin:.75rem 0 .3rem; }
#   .summary-card hr { border:none; border-top:1px solid var(--rule);
#     margin:.9rem 0; }
#   .summary-card code { background:var(--sand); padding:.1em .35em;
#     border-radius:3px; font-size:.84em; }
#   .summary-card ul { margin:.35rem 0 .7rem 1.2rem; }
#   .summary-card li { font-size:.91rem; line-height:1.72; margin-bottom:.2rem; }
#
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == '__main__':
    # Quick smoke tests
    cases = [
        # Real summary with markdown
        ("# Meeting Summary\n\n**March 17, 2026**\n\n## Key Decisions\n\n- **Adopted 2026 Budget** with 3.4% levy increase\n- Approved By-Law 2026-10\n\n## Main Topics\n\n*Healthcare*: Council supported lab access in the North.\n\n---\n\n*Refer to source documents for authoritative information.*",
         "real summary"),
        # Error message — should return None
        ("I apologize, but I'm unable to read the content of this PDF file. The document appears to be corrupted.",
         "error summary"),
        # None input
        (None, "None input"),
        # Empty string
        ("", "empty string"),
    ]

    for text, label in cases:
        result = render_summary(text)
        if result is None:
            print(f"[{label}] → None (suppressed)")
        else:
            preview = result.replace('\n', ' ')[:120]
            print(f"[{label}] → {preview}…")
