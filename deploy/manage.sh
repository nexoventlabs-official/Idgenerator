#!/bin/bash
# Start/Stop/Restart Gunicorn + Celery for Cloudways
APP_DIR="/home/master/applications/ykvncyeead/public_html"
VENV="$APP_DIR/venv/bin"
PIDFILE_GUNICORN="$APP_DIR/logs/gunicorn.pid"
PIDFILE_CELERY="$APP_DIR/logs/celery.pid"

cd "$APP_DIR"
source "$VENV/activate"

case "$1" in
  start)
    echo "Starting Gunicorn..."
    nohup $VENV/gunicorn app:app --bind 127.0.0.1:8000 --timeout 30 --workers 3 --threads 2 --pid $PIDFILE_GUNICORN --access-logfile $APP_DIR/logs/gunicorn_access.log --error-logfile $APP_DIR/logs/gunicorn_error.log > $APP_DIR/logs/gunicorn_out.log 2>&1 &
    echo "Gunicorn PID: $!"
    
    echo "Starting Celery..."
    nohup $VENV/celery -A tasks worker --loglevel=info --concurrency=2 --pidfile=$PIDFILE_CELERY > $APP_DIR/logs/celery_out.log 2>&1 &
    echo "Celery PID: $!"
    echo "All services started!"
    ;;
  stop)
    echo "Stopping Gunicorn..."
    if [ -f "$PIDFILE_GUNICORN" ]; then kill $(cat $PIDFILE_GUNICORN) 2>/dev/null; rm -f $PIDFILE_GUNICORN; fi
    pkill -f "gunicorn app:app" 2>/dev/null
    
    echo "Stopping Celery..."
    if [ -f "$PIDFILE_CELERY" ]; then kill $(cat $PIDFILE_CELERY) 2>/dev/null; rm -f $PIDFILE_CELERY; fi
    pkill -f "celery -A tasks" 2>/dev/null
    echo "All services stopped!"
    ;;
  restart)
    $0 stop
    sleep 2
    $0 start
    ;;
  status)
    echo "=== Gunicorn ==="
    if pgrep -f "gunicorn app:app" > /dev/null; then echo "RUNNING (PIDs: $(pgrep -f 'gunicorn app:app' | tr '\n' ' '))"; else echo "STOPPED"; fi
    echo "=== Celery ==="  
    if pgrep -f "celery -A tasks" > /dev/null; then echo "RUNNING (PIDs: $(pgrep -f 'celery -A tasks' | tr '\n' ' '))"; else echo "STOPPED"; fi
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
