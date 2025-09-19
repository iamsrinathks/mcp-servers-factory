#!/bin/bash
set -e

GCP_PROJECT_ID=""
REGION=""
SERVICE_NAME="confluence-mcp-server"

# Build & deploy to Cloud Run directly from source
gcloud run deploy $SERVICE_NAME \
  --project $GCP_PROJECT_ID \
  --region $REGION \
  --source . \
  --set-env-vars CONFLUENCE_BASE_URL="" \
  --set-env-vars CONFLUENCE_PAT=""
