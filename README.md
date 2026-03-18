# Setapp App Rating Monitor

Automated monitoring agent that tracks ratings for all apps on [Setapp](https://setapp.com/apps), detects quality declines, and presents findings in an interactive web dashboard.

## Live Dashboard

**[View Dashboard →](https://dasha-lkns.github.io/setapp-app-rates/)**

The dashboard updates daily via GitHub Actions.

## Features

- **App Discovery** — Automatically discovers all apps listed on Setapp
- **Rating Collection** — Scrapes rating scores and review counts using Playwright
- **Daily Snapshots** — Stores historical data in SQLite for trend analysis
- **7-Day Trend Analysis** — Detects rating changes over the past week
- **Risk Detection** — Flags apps with rating below 70% or drops of 5+ points
- **Interactive Dashboard** — Corporate-styled HTML dashboard with:
  - KPI summary cards
  - Bottom 10 worst-rated apps chart
  - Interactive rating distribution (click to drill down)
  - 24-hour change tracking
  - Sortable all-apps table with search
  - At Risk and Stable app sections

## How It Works

1. **GitHub Action** runs daily at 08:00 UTC
2. Playwright scrapes `setapp.com/apps` for the full app catalog
3. Individual app pages are scraped for detailed ratings
4. Data is stored in SQLite and trends are analyzed
5. Dashboard JSON + HTML are generated and committed
6. GitHub Pages serves the dashboard at the public URL

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Full run (scrape + analyze + report)
python -m setapp_monitor.main

# Only scrape data
python -m setapp_monitor.main --scrape

# Only generate report/dashboard from existing data
python -m setapp_monitor.main --report
```

## Project Structure

```
├── .github/workflows/
│   └── daily-scrape.yml       # GitHub Action for daily updates
├── docs/                       # GitHub Pages output
│   ├── index.html              # Dashboard (loads data via fetch)
│   └── dashboard_data.json     # Dashboard data
├── setapp_monitor/
│   ├── __init__.py
│   ├── config.py               # Configuration
│   ├── database.py             # SQLite storage layer
│   ├── scraper.py              # Playwright web scraper
│   ├── analysis.py             # Trend analysis + risk detection
│   ├── report.py               # Markdown report generator
│   ├── dashboard.py            # Dashboard data generator
│   ├── main.py                 # CLI orchestration
│   ├── data/                   # SQLite database
│   └── reports/                # Generated reports + templates
├── requirements.txt
└── README.md
```

## GitHub Pages Setup

1. Go to **Settings → Pages** in your repository
2. Set **Source** to "Deploy from a branch"
3. Select **Branch**: `main`, **Folder**: `/docs`
4. Save — your dashboard will be live within minutes
