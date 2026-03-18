"""
Google Cloud Storage helper for persisting data between Cloud Run Job executions
and serving fresh dashboard data from the Cloud Run service.
"""
import os
import logging
from . import config

logger = logging.getLogger(__name__)


def _get_client():
    """Lazy-load the GCS client (only when GCS_BUCKET is set)."""
    from google.cloud import storage
    return storage.Client()


def upload_file(local_path: str, blob_name: str):
    """Upload a local file to the configured GCS bucket."""
    if not config.GCS_BUCKET:
        logger.debug("GCS_BUCKET not set, skipping upload")
        return
    client = _get_client()
    bucket = client.bucket(config.GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    logger.info(f"Uploaded {local_path} → gs://{config.GCS_BUCKET}/{blob_name}")


def download_file(blob_name: str, local_path: str) -> bool:
    """Download a file from GCS to a local path. Returns True if successful."""
    if not config.GCS_BUCKET:
        logger.debug("GCS_BUCKET not set, skipping download")
        return False
    try:
        client = _get_client()
        bucket = client.bucket(config.GCS_BUCKET)
        blob = bucket.blob(blob_name)
        if not blob.exists():
            logger.info(f"gs://{config.GCS_BUCKET}/{blob_name} does not exist")
            return False
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        blob.download_to_filename(local_path)
        logger.info(f"Downloaded gs://{config.GCS_BUCKET}/{blob_name} → {local_path}")
        return True
    except Exception as e:
        logger.warning(f"GCS download failed for {blob_name}: {e}")
        return False


def sync_db_from_gcs():
    """Pull the latest SQLite database from GCS before scraping."""
    download_file("data/setapp_ratings.db", config.DB_PATH)


def sync_db_to_gcs():
    """Push the updated SQLite database to GCS after scraping."""
    upload_file(config.DB_PATH, "data/setapp_ratings.db")


def upload_dashboard_files():
    """Upload dashboard HTML and JSON to GCS for the web service to serve."""
    json_path = os.path.join(config.REPORTS_DIR, "dashboard_data.json")
    template_path = os.path.join(config.REPORTS_DIR, "dashboard_template.html")

    if os.path.exists(json_path):
        upload_file(json_path, "dashboard/dashboard_data.json")
    if os.path.exists(template_path):
        upload_file(template_path, "dashboard/index.html")


def download_dashboard_files(target_dir: str):
    """Download dashboard files from GCS for serving."""
    os.makedirs(target_dir, exist_ok=True)
    download_file("dashboard/dashboard_data.json",
                   os.path.join(target_dir, "dashboard_data.json"))
    download_file("dashboard/index.html",
                   os.path.join(target_dir, "index.html"))
