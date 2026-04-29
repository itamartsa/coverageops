#!/bin/bash
set -e

echo "🚀 CoverageOps Backend Starting..."

# Create required directories
mkdir -p /tmp/srtm /tmp/results

# Run DB seed (creates admin user etc.)
echo "🌱 Seeding database..."
python -m app.scripts.seed || echo "⚠️  Seed skipped (already seeded)"

# Start the API server
echo "✅ Starting FastAPI..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
