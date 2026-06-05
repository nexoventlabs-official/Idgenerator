"""
Health Check Endpoints — MongoDB edition
"""
from flask import jsonify, Blueprint
from datetime import datetime, timezone
import sys

health_bp = Blueprint('health', __name__)


@health_bp.route('/health')
def health_check():
    return jsonify({'status': 'healthy',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'service': 'wetheleaders-idcard', 'version': '5.0'})


@health_bp.route('/health/ready')
def readiness_check():
    checks = {}
    all_ok = True

    # MongoDB check
    try:
        from app import _get_db
        db = _get_db()
        db.command('ping')
        checks['mongodb'] = {'status': 'healthy'}
    except Exception as e:
        checks['mongodb'] = {'status': 'unhealthy', 'error': str(e)}
        all_ok = False

    # Cloudinary check
    try:
        import cloudinary.api
        cloudinary.api.ping()
        checks['cloudinary'] = {'status': 'healthy'}
    except Exception as e:
        checks['cloudinary'] = {'status': 'unhealthy', 'error': str(e)}
        all_ok = False

    code = 200 if all_ok else 503
    return jsonify({'status': 'ready' if all_ok else 'not_ready',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'checks': checks}), code


@health_bp.route('/health/live')
def liveness_check():
    return jsonify({'status': 'alive',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'python_version': sys.version})


@health_bp.route('/health/metrics')
def metrics():
    try:
        from app import _get_db
        db = _get_db()
        return jsonify({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'database': {
                'total_voters':    db.voters.estimated_document_count(),
                'total_generated': db.generated_voters.estimated_document_count(),
                'total_stats':     db.generation_stats.estimated_document_count(),
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
