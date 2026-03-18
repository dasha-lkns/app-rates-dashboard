"""
Web scraper for Setapp app data.
Uses Playwright (headless browser) to handle sites with bot protection.
Collects app listings and individual app ratings from setapp.com.
"""
import re
import time
import logging
from typing import Optional
from playwright.sync_api import sync_playwright, Page, Browser
from . import config

logger = logging.getLogger(__name__)

# Batch size for detail scraping (close/reopen browser periodically)
BATCH_SIZE = 50


def _create_browser(playwright) -> Browser:
    """Launch a headless browser with stealth-like settings."""
    return playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]
    )


def _new_page(browser: Browser) -> Page:
    """Create a new page with realistic settings."""
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        locale="en-US",
    )
    page = context.new_page()
    return page


def discover_apps() -> list[dict]:
    """
    Scrape the Setapp apps listing page to discover all apps.

    Returns a list of dicts with:
        - app_name: str
        - app_slug: str
        - app_url: str (full URL)
        - listing_rating: float or None
    """
    apps = []

    with sync_playwright() as pw:
        browser = _create_browser(pw)
        page = _new_page(browser)

        try:
            logger.info(f"Navigating to {config.SETAPP_APPS_URL}")
            page.goto(config.SETAPP_APPS_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)  # Let JS render

            # Scroll to load any lazy content
            for _ in range(5):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(500)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1000)

            # Get all links matching /apps/<slug>
            links = page.query_selector_all('a[href^="/apps/"]')
            logger.info(f"Found {len(links)} links matching /apps/")

            seen_slugs = set()
            for link in links:
                href = link.get_attribute("href") or ""
                match = re.match(r"^/apps/([a-z0-9][a-z0-9\-\.]+)$", href)
                if not match:
                    continue

                slug = match.group(1)
                if slug in seen_slugs:
                    continue

                # Get app name and rating from the card text
                app_name = ""
                listing_rating = None
                try:
                    text = link.inner_text().strip()
                    if not text:
                        continue
                    # The card text looks like:
                    # "Hot\nCleanMyMac\nTidy up your Mac\n97%"
                    # or "CleanMyMac\nTidy up your Mac\n97%"
                    lines = [l.strip() for l in text.split("\n") if l.strip()]

                    # Extract rating from lines
                    for line in lines:
                        rm = re.match(r"^(\d{1,3})%$", line)
                        if rm:
                            listing_rating = float(rm.group(1))

                    # Filter out known non-name lines
                    skip_words = {"Hot", "New", "AI+", "AI", "Mac", "iOS", "Web"}
                    name_candidates = [
                        l for l in lines
                        if l not in skip_words
                        and not re.match(r"^\d{1,3}%$", l)
                        and len(l) < 60
                        and len(l) > 1
                    ]

                    # First remaining line is typically the app name
                    if name_candidates:
                        app_name = name_candidates[0]
                except Exception:
                    pass

                if not app_name:
                    continue

                seen_slugs.add(slug)
                apps.append({
                    "app_name": app_name,
                    "app_slug": slug,
                    "app_url": f"{config.SETAPP_BASE_URL}{href}",
                    "listing_rating": listing_rating,
                })

        except Exception as e:
            logger.error(f"Error discovering apps: {e}")
        finally:
            browser.close()

    logger.info(f"Discovered {len(apps)} unique apps")
    return apps


def scrape_app_details(page: Page, app_url: str) -> dict:
    """
    Scrape an individual app page for detailed rating data.

    Returns:
        - rating_score: float or None
        - rating_count: int or None
        - developer: str or None
    """
    result = {"rating_score": None, "rating_count": None, "developer": None}

    try:
        page.goto(app_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        page_text = page.inner_text("body")

        # Extract rating score (percentage like "97%")
        rating_match = re.search(r"\b(\d{1,3})%", page_text)
        if rating_match:
            val = float(rating_match.group(1))
            if 0 <= val <= 100:
                result["rating_score"] = val

        # Extract rating count (e.g., "17,408 ratings")
        count_match = re.search(r"([\d,]+)\s*ratings?", page_text, re.IGNORECASE)
        if count_match:
            count_str = count_match.group(1).replace(",", "")
            try:
                result["rating_count"] = int(count_str)
            except ValueError:
                pass

        # Developer info
        dev_match = re.search(
            r"(?:by|developer[:\s]+)\s*([A-Z][A-Za-z0-9\s\.\,&]+?)(?:\s*[|•\-\n]|\s*$)",
            page_text
        )
        if dev_match:
            result["developer"] = dev_match.group(1).strip()[:100]

    except Exception as e:
        logger.warning(f"Error scraping {app_url}: {e}")

    return result


def collect_all_ratings(apps: list[dict], progress_callback=None) -> list[dict]:
    """
    For each app, scrape its detail page for ratings.
    Uses batched browser sessions to manage memory.
    """
    results = []
    total = len(apps)

    with sync_playwright() as pw:
        browser = _create_browser(pw)
        page = _new_page(browser)

        for i, app in enumerate(apps):
            if progress_callback:
                progress_callback(i + 1, total, app["app_name"])

            # Restart browser every BATCH_SIZE apps to avoid memory issues
            if i > 0 and i % BATCH_SIZE == 0:
                browser.close()
                browser = _create_browser(pw)
                page = _new_page(browser)
                logger.info(f"Browser restarted at app {i}")

            details = scrape_app_details(page, app["app_url"])
            enriched = {**app, **details}

            # Fall back to listing_rating if detail page didn't yield one
            if enriched["rating_score"] is None and app.get("listing_rating") is not None:
                enriched["rating_score"] = app["listing_rating"]

            results.append(enriched)

            logger.info(
                f"[{i+1}/{total}] {app['app_name']}: "
                f"rating={enriched['rating_score']}, count={enriched['rating_count']}"
            )

            # Brief delay between requests
            time.sleep(config.REQUEST_DELAY)

        browser.close()

    return results
