web: sh -c 'gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --timeout 30 --workers 5 --threads 2'
worker: celery -A tasks worker --loglevel=info --concurrency=4
