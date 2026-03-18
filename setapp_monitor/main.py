#!/usr/bin/env python3
"""
Setapp App Rating Monitor — Main Orchestration Script

Usage:
    python -m setapp_monitor.main              # Full run: scrape + analyze + report
    python -m setapp_monitor.main --scrape     # Only scrape and store data
    python -m setapp_monitor.main --analyze    # Only run analysis + report (no scraping)
    python -m setapp_monitor.main --report     # Only generate report from existing data
    python -m setapp_monitor.main --serve      # Start web server (for Cloud Run service)
"""
import json
import os
import shutil
import sys
import argparse
import logging
from datetime import datetime
from . import config, gcs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("setapp_monitor")

# GitHub Pages output directory (root-level docs/)
DOCS_DIR = os.path.join(os.path.dirname(config.BASE_DIR), "docs")


def run_scrape():
    """Discover apps, scrape ratings, and store snapshots."""
    from . import database, scraper
    logger.info("=== Starting data collection ===")

    # Pull latest DB from GCS if running in Cloud Run
    gcs.sync_db_from_gcs()

    # Step 1: Discover apps
    logger.info("Discovering apps from Setapp listing...")
    apps = scraper.discover_apps()

    if not apps:
        logger.error("No apps discovered. Check network connectivity or page structure.")
        return False

    logger.info(f"Found {len(apps)} apps on Setapp")

    # Step 2: Scrape individual app pages for detailed ratings
    def progress(current, total, name):
        if current % 25 == 0 or current == total:
            logger.info(f"Progress: {current}/{total} apps scraped ({name})")

    logger.info("Scraping individual app pages for ratings...")
    enriched_apps = scraper.collect_all_ratings(apps, progress_callback=progress)

    # Step 3: Store in database
    today = datetime.now().strftime("%Y-%m-%d")
    stored = 0
    for app_data in enriched_apps:
        app_id = database.upsert_app(
            app_name=app_data["app_name"],
            app_slug=app_data["app_slug"],
            app_url=app_data["app_url"],
            developer=app_data.get("developer"),
        )
        database.insert_snapshot(
            app_id=app_id,
            rating_score=app_data.get("rating_score"),
            rating_count=app_data.get("rating_count"),
            snapshot_date=today,
        )
        stored += 1

    logger.info(f"Stored {stored} rating snapshots for {today}")

    # Push updated DB to GCS
    gcs.sync_db_to_gcs()

    return True


def run_analysis_and_report():
    """Run trend analysis and generate reports + dashboard."""
    from . import database, analysis, report, dashboard
    logger.info("=== Running trend analysis ===")

    results = analysis.run_full_analysis()

    at_risk = [r for r in results if r["risk"]["is_at_risk"]]
    logger.info(f"Analysis complete: {len(at_risk)} app(s) flagged as at risk")

    for r in at_risk:
        logger.warning(
            f"AT RISK: {r['app']['app_name']} — "
            f"{r['risk']['risk_level']} — "
            f"{', '.join(r['risk']['risk_reasons'])}"
        )

    # Generate markdown report
    logger.info("Generating markdown report...")
    stats = {
        "total_apps": len(results),
        "total_snapshots": database.get_snapshot_count(),
        "run_date": datetime.now().strftime("%Y-%m-%d"),
    }
    report_content = report.generate_report(results, stats)
    filepath = report.save_report(report_content)
    logger.info(f"Report saved to: {filepath}")

    # Generate dashboard data JSON
    logger.info("Generating dashboard data...")
    dash_data = dashboard.get_dashboard_data()
    dashboard.save_dashboard_data(dash_data)

    # Build GitHub Pages output in docs/
    logger.info("Building GitHub Pages output in docs/...")
    os.makedirs(DOCS_DIR, exist_ok=True)

    # Copy dashboard_data.json to docs/
    json_src = os.path.join(config.REPORTS_DIR, "dashboard_data.json")
    json_dst = os.path.join(DOCS_DIR, "dashboard_data.json")
    shutil.copy2(json_src, json_dst)

    # Copy template as index.html (loads data via fetch)
    template_path = os.path.join(config.REPORTS_DIR, "dashboard_template.html")
    index_dst = os.path.join(DOCS_DIR, "index.html")
    if os.path.exists(template_path):
        shutil.copy2(template_path, index_dst)
        logger.info(f"GitHub Pages dashboard → {index_dst}")

    # Also generate the self-contained dashboard.html with embedded data
    output_path = os.path.join(config.REPORTS_DIR, "dashboard.html")
    if os.path.exists(template_path):
        with open(template_path, "r") as f:
            html = f.read()
        embedded_script = f"<script>const EMBEDDED_DATA = {json.dumps(dash_data)};</script>\n"
        html = html.replace("<script>", embedded_script + "<script>", 1)
        with open(output_path, "w") as f:
            f.write(html)
        logger.info(f"Self-contained dashboard → {output_path}")

    # Upload dashboard files to GCS for the web service
    gcs.upload_dashboard_files()

    return filepath


def run_serve():
    """Start a lightweight HTTP server to serve the dashboard (Cloud Run service mode)."""
    import http.server
    import threading
    import time

    port = int(os.environ.get("PORT", 8080))
    serve_dir = "/tmp/dashboard"
    os.makedirs(serve_dir, exist_ok=True)

    # Try to download latest dashboard files from GCS (don't crash if it fails)
    try:
        gcs.download_dashboard_files(serve_dir)
    except Exception as e:
        logger.warning(f"GCS download failed on startup: {e}")

    # If no GCS files, fall back to local docs/
    index_path = os.path.join(serve_dir, "index.html")
    if not os.path.exists(index_path):
        app_docs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
        if os.path.exists(os.path.join(app_docs, "index.html")):
            shutil.copytree(app_docs, serve_dir, dirs_exist_ok=True)
            logger.info(f"Using local docs/ as fallback")

    # If still no index.html, create a placeholder so the server starts
    if not os.path.exists(index_path):
        with open(index_path, "w") as f:
            f.write("<html><body><h1>Setapp Dashboard</h1><p>Waiting for first scrape to populate data. Run the scraper job to generate dashboard.</p></body></html>")
        logger.info("Created placeholder index.html — run scraper to populate")

    # Periodically refresh from GCS (every 5 minutes)
    def refresh_loop():
        while True:
            time.sleep(300)
            try:
                gcs.download_dashboard_files(serve_dir)
                logger.info("Refreshed dashboard files from GCS")
            except Exception as e:
                logger.warning(f"GCS refresh failed: {e}")

    if config.GCS_BUCKET:
        t = threading.Thread(target=refresh_loop, daemon=True)
        t.start()

    # Serve — start immediately so Cloud Run health check passes
    os.chdir(serve_dir)
    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.HTTPServer(("0.0.0.0", port), handler)
    logger.info(f"Dashboard server running on port {port}")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Setapp App Rating Monitor")
    parser.add_argument("--scrape", action="store_true", help="Only scrape data")
    parser.add_argument("--analyze", action="store_true", help="Only analyze + report")
    parser.add_argument("--report", action="store_true", help="Only generate report")
    parser.add_argument("--serve", action="store_true", help="Start web server")
    args = parser.parse_args()

    if args.serve:
        run_serve()
        return

    # Initialize database (lazy import — not needed in serve mode)
    from . import database
    database.init_db()

    if args.scrape:
        run_scrape()
    elif args.analyze or args.report:
        run_analysis_and_report()
    else:
        # Full run
        success = run_scrape()
        if success:
            run_analysis_and_report()
        else:
            logger.error("Scraping failed, skipping analysis.")
            sys.exit(1)

    logger.info("=== Done ===")


if __name__ == "__main__":
    main()
