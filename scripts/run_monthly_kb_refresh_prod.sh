#!/bin/bash

set -e

PROJECT_DIR="/opt/Ask_SLT"
LOG_FILE="$PROJECT_DIR/logs/monthly_kb_refresh.log"

mkdir -p "$PROJECT_DIR/logs"

echo "====================================================" >> "$LOG_FILE"
echo "Production monthly KB refresh started at $(date)" >> "$LOG_FILE"
echo "====================================================" >> "$LOG_FILE"

cd "$PROJECT_DIR"

docker compose up -d >> "$LOG_FILE" 2>&1

echo "Waiting for backend container API..." >> "$LOG_FILE"

until docker compose exec -T backend python - <<'PY' >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8000/api/v1/admin/ingestion-status", timeout=5)
PY
do
  sleep 10
done

echo "Backend is reachable inside container." >> "$LOG_FILE"

docker compose exec -T backend python scripts/monthly_kb_refresh.py >> "$LOG_FILE" 2>&1

echo "Production monthly KB refresh finished at $(date)" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
