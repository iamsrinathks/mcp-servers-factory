#!/bin/bash
set -e

PROJECT_ID=""
REGION=""
SERVICE_NAME="gitlab-mcp-server"

# Build & deploy to Cloud Run directly from source
gcloud run deploy $SERVICE_NAME \
  --project $PROJECT_ID \
  --region $REGION \
  --source . \
  --set-env-vars GITLAB_TOKEN="" \
  --set-env-vars GITLAB_BASE_URL=""


