# Nipissing Township Council Meeting Archive

Automatically scrapes and archives council meeting agendas, minutes, and agenda packages from [nipissingtownship.com](https://nipissingtownship.com/council-meeting-dates-agendas-minutes/) before they are deleted.

Runs every other Tuesday via GitHub Actions and publishes to GitHub Pages.

---

## Setup (one time, ~10 minutes)

### 1. Create a GitHub account
Go to [github.com](https://github.com) and sign up if you don't have one.

### 2. Create a new repository
- Click **+** → **New repository**
- Name it something like `council-archive`
- Set it to **Public** (required for free GitHub Pages)
- Click **Create repository**

### 3. Upload these files
In your new repo, click **Add file** → **Upload files** and upload everything in this folder.

### 4. Enable GitHub Pages
- Go to your repo's **Settings** tab
- Click **Pages** in the left sidebar
- Under **Source**, select **Deploy from a branch**
- Set branch to `main`, folder to `/docs`
- Click **Save**

Your site will be live in ~2 minutes at:
`https://yourusername.github.io/council-archive`

### 5. Run it for the first time
- Go to the **Actions** tab in your repo
- Click **Scrape Nipissing Council Meetings**
- Click **Run workflow** → **Run workflow**

This will download all existing PDFs and build the site. It may take a few minutes.

### 6. That's it!
GitHub Actions will now run every other Tuesday automatically at 9 AM Eastern. Any new files posted to the Nipissing Township website will be downloaded, and the archive site will be updated.

---

## How it works

```
Every other Tuesday @ 9 AM Eastern
         ↓
GitHub Actions runs scraper.py
         ↓
Scrapes nipissingtownship.com for new PDF links
         ↓
Downloads any PDFs not already in the archive
         ↓
Regenerates HTML pages in docs/ (one page per year)
         ↓
Commits changes back to the repo
         ↓
GitHub Pages serves the updated site
```

## File structure

```
council-archive/
├── .github/
│   └── workflows/
│       └── scrape.yml      ← GitHub Actions schedule
├── docs/                   ← Published website (served by GitHub Pages)
│   ├── index.html          ← Main page (links to each year)
│   ├── 2024/
│   │   ├── index.html      ← 2024 meetings page
│   │   └── files/          ← Downloaded PDFs
│   ├── 2025/
│   │   ├── index.html
│   │   └── files/
│   └── 2026/
│       ├── index.html
│       └── files/
├── scraper.py              ← Main scraper script
├── state.json              ← Tracks which files have been downloaded
├── requirements.txt
└── README.md
```

## Custom domain (optional)

If you want the archive at your own domain (e.g. `archive.yoursite.com`):
1. Add a file called `CNAME` inside the `docs/` folder containing just your domain, e.g: `archive.yoursite.com`
2. In your domain registrar, add a CNAME DNS record pointing to `yourusername.github.io`
3. In GitHub Pages settings, enter your custom domain

## Adjusting the schedule

The workflow runs every other Tuesday. To change this, edit `.github/workflows/scrape.yml` and modify the cron line. [crontab.guru](https://crontab.guru) is a handy tool for building cron expressions.
