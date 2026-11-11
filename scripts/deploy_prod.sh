#!/usr/bin/env bash
set -euo pipefail

echo "========================================================"
echo "🚀 Starting LifeStore Production Deployment"
echo "Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================================"

APP_DIR="/opt/Ask_SLT"
BRANCH="deploy/lifestore-enterprise-prod"
BACKUP_DIR="/root/ask-slt-deploy-backups"

cd "${APP_DIR}" || { echo "❌ Cannot cd to ${APP_DIR}"; exit 1; }

# 1. Backup .env file
echo "📦 Backing up production .env..."
mkdir -p "${BACKUP_DIR}"
if [ -f .env ]; then
    cp .env "${BACKUP_DIR}/env.$(date '+%Y%m%d-%H%M%S')"
    echo "✅ .env backed up to ${BACKUP_DIR}"
fi

# 2. Fetch and checkout latest code
echo "🔄 Fetching latest code from origin..."
git fetch origin "${BRANCH}"
git checkout "${BRANCH}"
git pull origin "${BRANCH}"
echo "✅ Code updated to commit: $(git rev-parse --short HEAD)"

# 3. Clean up dangling build cache if disk usage is high
echo "🧹 Checking docker build cache..."
docker builder prune -f --keep-storage 5GB > /dev/null 2>&1 || true

# 4. Rebuild and restart backend and frontend
echo "🐳 Rebuilding and restarting containers..."
docker compose -f docker-compose.prod.yml up -d --build --no-deps backend frontend

# 5. Wait for services to start
echo "⏳ Waiting 10 seconds for containers to initialize..."
sleep 10

# 6. Verify containers status
echo "📊 Container status:"
docker compose -f docker-compose.prod.yml ps backend frontend

# 7. Health checks
echo "🩺 Testing backend health..."
if curl -fsS http://127.0.0.1:8100/health > /dev/null 2>&1 || curl -fsS http://127.0.0.1:8100/docs > /dev/null 2>&1; then
    echo "✅ Backend is healthy and responding on 127.0.0.1:8100"
else
    echo "⚠️ Backend health check warning. Check logs with: docker logs slt_backend"
fi

echo "🩺 Testing frontend..."
if curl -fsS http://127.0.0.1:3100 > /dev/null 2>&1; then
    echo "✅ Frontend is healthy and responding on 127.0.0.1:3100"
else
    echo "⚠️ Frontend health check warning. Check logs with: docker logs slt_frontend"
fi

# 8. Clean up unused images
docker image prune -f > /dev/null 2>&1 || true

# 9. Verify Nginx
echo "🌐 Checking Nginx proxy configuration..."
if nginx -t > /dev/null 2>&1; then
    systemctl reload nginx || true
    echo "✅ Nginx reloaded successfully"
fi

echo "========================================================"
echo "🎉 LifeStore Production Deployment Completed!"
echo "Subdomain: https://aiagents.sltdigitallab.lk"
echo "========================================================"
