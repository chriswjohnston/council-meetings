import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import os
from collections import OrderedDict

BASE_URL = "https://nipissingtownship.com"
PAGE_URL = f"{BASE_URL}/council-meeting-dates-agendas-minutes/"

def fetch_links():
    """Scrape linked PDFs from the main page"""
    res = requests.get(PAGE_URL)
    soup = BeautifulSoup(res.text, "html.parser")

    meetings = {}

    for link in soup.find_all("a", href=True):
        href = link["href"]

        if ".pdf" not in href:
            continue

        if not href.startswith("http"):
            href = BASE_URL + href

        filename = href.split("/")[-1]

        # extract year
        year = None
        for y in range(2018, 2030):
            if str(y) in filename:
                year = str(y)
                break

        if not year:
            continue

        if year not in meetings:
            meetings[year] = []

        # classify label
        label = "Agenda"
        if "Minutes" in filename:
            label = "Minutes"
        elif "Package" in filename:
            label = "Agenda Package"

        meetings[year].append({
            "date": "Unknown",
            "label": label,
            "url": href,
            "filename": filename,
            "type": "pdf"
        })

    return meetings


def fetch_html_page_links():
    """Stub for your existing HTML page scraper"""
    return {}


def merge_meetings(pdf_meetings, html_meetings):
    """Merge both sources"""
    merged = pdf_meetings.copy()

    for year, items in html_meetings.items():
        if year not in merged:
            merged[year] = []
        merged[year].extend(items)

    return merged


# 🔥 NEW: 2023 brute-force discovery
def discover_2023_pdfs(meetings):
    print("\nBruteforcing 2023 PDFs...")

    BASE = "https://nipissingtownship.com/wp-content/uploads/2023"

    months = [
        ("January", "01"), ("February", "02"), ("March", "03"),
        ("April", "04"), ("May", "05"), ("June", "06"),
        ("July", "07"), ("August", "08"), ("September", "09"),
        ("October", "10"), ("November", "11"), ("December", "12")
    ]

    patterns = [
        "Minutes-{month}-{day}-2023.pdf",
        "Agenda-{month}-{day}-2023.pdf",
        "Agenda-Package-{month}-{day}-2023.pdf",
        "{month}-{day}-2023-Agenda.pdf",
        "{month}-{day}-2023-Agenda-Amended.pdf",
    ]

    if "2023" not in meetings:
        meetings["2023"] = []

    session = requests.Session()
    found = 0

    for month_name, month_num in months:
        for day in range(1, 32):
            for pattern in patterns:
                filename = pattern.format(month=month_name, day=day)
                url = f"{BASE}/{month_num}/{filename}"

                try:
                    r = session.head(url, timeout=5)
                    if r.status_code == 200:

                        label = "Agenda"
                        if "Minutes" in filename:
                            label = "Minutes"
                        elif "Package" in filename:
                            label = "Agenda Package"

                        date_str = f"{month_name} {day}, 2023"

                        meetings["2023"].append({
                            "date": date_str,
                            "label": label,
                            "url": url,
                            "filename": filename,
                            "type": "pdf"
                        })

                        print(f"  ✓ Found: {filename}")
                        found += 1

                except:
                    continue

    # dedupe by URL
    seen = set()
    unique = []
    for doc in meetings["2023"]:
        if doc["url"] not in seen:
            seen.add(doc["url"])
            unique.append(doc)

    meetings["2023"] = unique

    print(f"  Found {found} candidate 2023 files ({len(unique)} unique)")
    return meetings


def download_pdfs(meetings):
    """Optional downloader"""
    os.makedirs("downloads", exist_ok=True)

    for year, docs in meetings.items():
        for doc in docs:
            url = doc["url"]
            filename = doc["filename"]
            path = os.path.join("downloads", filename)

            if os.path.exists(path):
                continue

            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    with open(path, "wb") as f:
                        f.write(r.content)
            except:
                continue


def build_output(meetings):
    """Convert to JSON with unique keys to prevent overwrites"""
    output = {}

    for year, docs in meetings.items():
        for doc in docs:
            key = f"{year}-{doc['date']}-{doc['label']}"
            key = key.replace(" ", "_").replace(",", "")

            output[key] = {
                "filename": doc["filename"],
                "year": year,
                "label": doc["label"],
                "date": doc["date"],
                "url": doc["url"],
                "downloaded_at": datetime.utcnow().isoformat()
            }

    # optional: sort by date
    output = OrderedDict(sorted(output.items(), key=lambda x: x[1]["date"]))

    return output


def main():
    print("Fetching linked PDFs...")
    pdf_meetings = fetch_links()

    print("Fetching HTML pages...")
    html_meetings = fetch_html_page_links()

    meetings = merge_meetings(pdf_meetings, html_meetings)

    # 🔥 Discover 2023 PDFs
    meetings = discover_2023_pdfs(meetings)

    print("Downloading PDFs...")
    download_pdfs(meetings)

    print("Building JSON...")
    output = build_output(meetings)

    with open("meetings.json", "w") as f:
        json.dump(output, f, indent=2)

    print("Done.")


if __name__ == "__main__":
    main()
