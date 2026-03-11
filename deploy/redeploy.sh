#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Quick Redeploy Script - Pull latest code and restart services
# Usage: bash redeploy.sh
# ═══════════════════════════════════════════════════════════════════

set -e

APP_DIR="/home/master/applications/$(ls /home/master/applications/ | head -1)/public_html"
cd "$APP_DIR"

echo "── Pulling latest code..."
git pull origin main

echo "── Activating virtual environment..."
source venv/bin/activate

echo "── Installing/updating dependencies..."
pip install -r requirements.txt

echo "── Restarting services..."
sudo supervisorctl restart idcard_gunicorn
sudo supervisorctl restart idcard_celery

echo "── Waiting for startup..."
sleep 3

echo "── Checking health..."
curl -s http://127.0.0.1:8000/health | python3 -m json.tool

echo ""
echo "✓ Redeployment complete!"
