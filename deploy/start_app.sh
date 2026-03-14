#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Startup Script for Gunicorn + Celery on Cloudways
# Runs without sudo — uses crontab @reboot for persistence
# ═══════════════════════════════════════════════════════════════════

APP_DIR="/home/master/applications/ykvncyeead/public_html"
VENV="$APP_DIR/venv/bin"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"

# Kill existing processes
pkill -f "gunicorn app:app" 2>/dev/null
pkill -f "celery -A tasks worker" 2>/dev/null
sleep 2

# Start Gunicorn
cd "$APP_DIR"
nohup "$VENV/gunicorn" app:app \
    --bind 127.0.0.1:8000 \
    --timeout 60 \
    --workers 3 \
    --threads 2 \
    --access-logfile "$LOG_DIR/gunicorn_access.log" \
    --error-logfile "$LOG_DIR/gunicorn_error.log" \
    > "$LOG_DIR/gunicorn_out.log" 2>&1 &

echo "Gunicorn started (PID: $!)"

# Wait for Gunicorn to initialize
sleep 3

# Start Celery
nohup "$VENV/celery" -A tasks worker \
    --loglevel=info \
    --concurrency=2 \
    > "$LOG_DIR/celery_out.log" 2>&1 &

echo "Celery started (PID: $!)"

sleep 3

# Verify
if curl -s -m 5 http://127.0.0.1:8000/ > /dev/null 2>&1; then
    echo "✓ Gunicorn is responding"
else
    echo "✗ Gunicorn not responding — check $LOG_DIR/gunicorn_error.log"
fi

echo "Done. Logs at: $LOG_DIR/"
