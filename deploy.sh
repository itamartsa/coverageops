#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
#  deploy.sh – CoverageOps Production Deploy Script
#  Usage: ./deploy.sh
#  Requires: .env.prod file to exist in the same directory
# ═══════════════════════════════════════════════════════════════════════
set -e

# Load .env.prod
if [ ! -f .env.prod ]; then
  echo "❌ Missing .env.prod file! Copy .env.prod.example and fill in values."
  exit 1
fi
source .env.prod

echo "🚀 CoverageOps Deploy – $(date)"
echo "──────────────────────────────────"

# 1. Pull latest code (if using git)
if [ -d .git ]; then
  echo "📥 Pulling latest code..."
  git pull origin main
fi

# 2. Build images
echo "🔨 Building Docker images..."
docker compose -f docker-compose.prod.yml --env-file .env.prod build --no-cache

# 3. Stop old containers gracefully
echo "⏹  Stopping old containers..."
docker compose -f docker-compose.prod.yml --env-file .env.prod down --remove-orphans

# 4. Start services
echo "▶  Starting services..."
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d

# 5. Wait for backend health
echo "⏳ Waiting for backend to be ready..."
for i in $(seq 1 30); do
  if docker exec coverageops_api curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "✅ Backend is healthy!"
    break
  fi
  sleep 2
  echo "   [${i}/30] waiting..."
done

# 6. Clean up unused images
echo "🧹 Cleaning up old images..."
docker image prune -f

echo ""
echo "══════════════════════════════════════"
echo " ✅ Deploy complete!"
echo " 🌐 App: https://${DOMAIN}"
echo " 📊 Logs: docker compose -f docker-compose.prod.yml logs -f"
echo "══════════════════════════════════════"
