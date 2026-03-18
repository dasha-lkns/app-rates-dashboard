"""
Configuration for Setapp App Rating Monitor
"""
import os

# Base URLs
SETAPP_BASE_URL = "https://setapp.com"
SETAPP_APPS_URL = f"{SETAPP_BASE_URL}/apps"

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DB_PATH = os.path.join(DATA_DIR, "setapp_ratings.db")

# GCS bucket for Cloud Run persistence (set via environment variable)
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

# Scraping
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 1.0  # seconds between requests to be polite
MAX_RETRIES = 3
USER_AGENT = "SetappMonitor/1.0 (Quality Monitoring Agent)"

# Analysis
TREND_WINDOW_DAYS = 7
RISK_RATING_DROP_PERCENT = 20  # flag if rating drops by 20%+
RISK_CONSECUTIVE_DROP_DAYS = 3  # flag if rating drops 3+ consecutive days

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
