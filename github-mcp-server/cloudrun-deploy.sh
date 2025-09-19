#!/bin/bash
set -e

GCP_PROJECT_ID=""
REGION=""
SERVICE_NAME="github-mcp-server"

# Build & deploy to Cloud Run directly from source
gcloud run deploy $SERVICE_NAME \
  --project $GCP_PROJECT_ID \
  --region $REGION \
  --source . \
  --set-env-vars GITHUB_TOKEN=""
