# council-meetings scraper update

## What changed
The scraper now discovers and archives older council meeting records stored
as HTML pages on nipissingtownship.com — going back to 2018 and possibly
earlier.

## New files to add to your council-meetings repo
- scraper.py (replace existing)
- html_page_cache.json (auto-created on first run — add to .gitignore)

## Add to .gitignore in council-meetings repo
html_page_cache.json

## What the HTML page scraper does
1. Queries the WordPress REST API for all child pages under /council-meeting-dates-agendas-minutes/
2. Falls back to a hardcoded list of known pages (2018-2022) if REST API is unavailable
3. Fetches each page, extracts date/type/content
4. Merges with PDF records — PDFs take priority if both exist for same date
5. Caches 404s so dead URLs aren't re-checked every run
6. Renders HTML page content inline on meeting detail pages
7. Links back to original nipissingtownship.com page for full text

## Expected new records
~150+ additional meetings from 2018-2022 will appear in the archive
on the next scraper run.
