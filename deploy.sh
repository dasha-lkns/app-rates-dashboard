#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Setapp Rating Monitor — Google Cloud Run Deployment Script
# Project: macpaw-aisprint
# ═══════════════════════════════════════════════════════════════════
#
# Run this script from the root of the setapp-app-rates repository.
# It will walk you through each step, pausing for confirmation.
#
# Prerequisites:
#   - gcloud CLI installed (brew install google-cloud-sdk)
#   - Access to the macpaw-aisprint GCP project
# ═══════════════════════════════════════════════════════════════════

set -e

PROJECT_ID="macpaw-aisprint"
REGION="europe-west1"
BUCKET_NAME="${PROJECT_ID}-setapp-dashboard"
REPO_NAME="setapp-repo"
SERVICE_NAME="setapp-dashboard"
JOB_NAME="setapp-scraper"
SCHEDULER_NAME="setapp-daily-scrape"

echo "═══════════════════════════════════════════════════════════"
echo "  Setapp Rating Monitor — Cloud Run Deployment"
echo "  Project: ${PROJECT_ID}"
echo "  Region:  ${REGION}"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ─── Step 1: Authenticate ─────────────────────────────────────
echo "▸ STEP 1: Authenticate with Google Cloud"
echo "  This will open a browser window for you to sign in."
echo ""
read -p "  Press Enter to authenticate (or Ctrl+C to cancel)..."
gcloud auth login
gcloud config set project ${PROJECT_ID}
echo "✓ Authenticated and project set to ${PROJECT_ID}"
echo ""

# ─── Step 2: Enable APIs ──────────────────────────────────────
echo "▸ STEP 2: Enabling required APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com
echo "✓ All APIs enabled"
echo ""

# ─── Step 3: Create GCS Bucket ────────────────────────────────
echo "▸ STEP 3: Creating Cloud Storage bucket for data persistence..."
if gcloud storage buckets describe gs://${BUCKET_NAME} > /dev/null 2>&1; then
    echo "  Bucket gs://${BUCKET_NAME} already exists, skipping."
else
    gcloud storage buckets create gs://${BUCKET_NAME} \
        --location=${REGION} \
        --uniform-bucket-level-access
    echo "✓ Bucket created: gs://${BUCKET_NAME}"
fi
echo ""

# Upload existing data if available
if [ -f "setapp_monitor/data/setapp_ratings.db" ]; then
    echo "  Uploading existing database to GCS..."
    gcloud storage cp setapp_monitor/data/setapp_ratings.db gs://${BUCKET_NAME}/data/setapp_ratings.db
fi
if [ -f "docs/dashboard_data.json" ]; then
    echo "  Uploading existing dashboard data to GCS..."
    gcloud storage cp docs/dashboard_data.json gs://${BUCKET_NAME}/dashboard/dashboard_data.json
    gcloud storage cp docs/index.html gs://${BUCKET_NAME}/dashboard/index.html
fi
echo ""

# ─── Step 4: Create Artifact Registry ─────────────────────────
echo "▸ STEP 4: Creating Artifact Registry repository..."
if gcloud artifacts repositories describe ${REPO_NAME} --location=${REGION} > /dev/null 2>&1; then
    echo "  Repository ${REPO_NAME} already exists, skipping."
else
    gcloud artifacts repositories create ${REPO_NAME} \
        --repository-format=docker \
        --location=${REGION} \
        --description="Setapp monitor container images"
    echo "✓ Repository created: ${REPO_NAME}"
fi
echo ""

# ─── Step 5: Authenticate Docker with Artifact Registry ──────
echo "▸ STEP 5: Configuring Docker to push to Artifact Registry..."
gcloud auth configure-docker ${REGION}-docker.pkg.dev --quiet
echo "✓ Docker authenticated"
echo ""

DASHBOARD_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/dashboard:latest"
SCRAPER_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/scraper:latest"

# ─── Step 6: Build & push dashboard service image ─────────────
echo "▸ STEP 6: Building dashboard web service image locally..."
echo "  (This may take 2-3 minutes)"
docker buildx build --platform linux/amd64 -t ${DASHBOARD_IMAGE} -f Dockerfile --push .
echo "✓ Dashboard image built and pushed"
echo ""

# ─── Step 7: Build & push scraper job image ───────────────────
echo "▸ STEP 7: Building scraper job image locally..."
echo "  (This may take 5-10 minutes — Playwright + Chromium)"
docker buildx build --platform linux/amd64 -t ${SCRAPER_IMAGE} -f Dockerfile.scraper --push .
echo "✓ Scraper image built and pushed"
echo ""

# ─── Step 8: Deploy dashboard Cloud Run service ───────────────
echo "▸ STEP 8: Deploying dashboard as Cloud Run service..."
gcloud run deploy ${SERVICE_NAME} \
    --image ${DASHBOARD_IMAGE} \
    --region ${REGION} \
    --platform managed \
    --allow-unauthenticated \
    --memory 256Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 2 \
    --set-env-vars "GCS_BUCKET=${BUCKET_NAME}" \
    --timeout=60s

DASHBOARD_URL=$(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format='value(status.url)')
echo ""
echo "✓ Dashboard deployed!"
echo "  URL: ${DASHBOARD_URL}"
echo ""

# ─── Step 9: Create scraper Cloud Run Job ─────────────────────
echo "▸ STEP 9: Creating scraper Cloud Run Job..."
gcloud run jobs create ${JOB_NAME} \
    --image ${SCRAPER_IMAGE} \
    --region ${REGION} \
    --memory 2Gi \
    --cpu 1 \
    --task-timeout 30m \
    --max-retries 1 \
    --set-env-vars "GCS_BUCKET=${BUCKET_NAME}" \
    2>/dev/null || \
gcloud run jobs update ${JOB_NAME} \
    --image ${SCRAPER_IMAGE} \
    --region ${REGION} \
    --memory 2Gi \
    --cpu 1 \
    --task-timeout 30m \
    --max-retries 1 \
    --set-env-vars "GCS_BUCKET=${BUCKET_NAME}"
echo "✓ Scraper job created/updated"
echo ""

# ─── Step 10: Schedule daily execution ────────────────────────
echo "▸ STEP 10: Setting up daily schedule (9:00 AM GMT)..."

# Get the project number for the service account
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Grant the service account permission to invoke Cloud Run Jobs
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/run.invoker" \
    --condition=None \
    --quiet 2>/dev/null || true

gcloud scheduler jobs create http ${SCHEDULER_NAME} \
    --schedule="0 9 * * *" \
    --time-zone="Etc/GMT" \
    --location=${REGION} \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
    --http-method=POST \
    --oauth-service-account-email=${SA_EMAIL} \
    2>/dev/null || \
gcloud scheduler jobs update http ${SCHEDULER_NAME} \
    --schedule="0 9 * * *" \
    --time-zone="Etc/GMT" \
    --location=${REGION} \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
    --http-method=POST \
    --oauth-service-account-email=${SA_EMAIL}
echo "✓ Daily schedule set for 9:00 AM GMT"
echo ""

# ─── Step 11: Run initial scrape ──────────────────────────────
echo "▸ STEP 11: Running initial scrape..."
read -p "  Run the scraper now to populate the dashboard? (y/N) " RUN_NOW
if [[ "$RUN_NOW" =~ ^[Yy]$ ]]; then
    echo "  Starting scraper job (this takes ~18 minutes)..."
    gcloud run jobs execute ${JOB_NAME} --region ${REGION} --wait
    echo "✓ Initial scrape completed"
fi
echo ""

# ─── Done ─────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════════════"
echo "  DEPLOYMENT COMPLETE"
echo ""
echo "  Dashboard URL:  ${DASHBOARD_URL}"
echo "  Daily schedule: 9:00 AM GMT"
echo "  GCS bucket:     gs://${BUCKET_NAME}"
echo ""
echo "  Useful commands:"
echo "    Manual scrape:    gcloud run jobs execute ${JOB_NAME} --region ${REGION}"
echo "    View job logs:    gcloud logging read 'resource.type=cloud_run_job' --limit=50"
echo "    Update dashboard: gcloud run deploy ${SERVICE_NAME} --image ...dashboard:latest --region ${REGION}"
echo "    Pause schedule:   gcloud scheduler jobs pause ${SCHEDULER_NAME} --location ${REGION}"
echo "═══════════════════════════════════════════════════════════"
