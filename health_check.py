"""
Health Check and Monitoring Endpoints
======================================
Provides system health status for monitoring and alerting.
"""
from flask import jsonify, Blueprint
from datetime import datetime, timezone
import sys

health_bp = Blueprint('health', __name__)


def check_mongodb_connection(client, db_name: str) -> dict:
    """Check MongoDB connection health."""
    try:
        client.admin.command('ping')
        db = client[db_name]
        stats = db.command('dbstats')
        return {
            'status': 'healthy',
            'database': db_name,
            'collections': stats.get('collections', 0),
            'size_mb': round(stats.get('dataSize', 0) / (1024 * 1024), 2)
        }
    except Exception as e:
        return {
            'status': 'unhealthy',
            'database': db_name,
            'error': str(e)
        }


def check_redis_connection(redis_client) -> dict:
    """Check Redis connection health."""
    if not redis_client:
        return {'status': 'disabled', 'message': 'Redis not configured'}
    
    try:
        redis_client.ping()
        info = redis_client.info('memory')
        return {
            'status': 'healthy',
            'used_memory_mb': round(info.get('used_memory', 0) / (1024 * 1024), 2),
            'connected_clients': redis_client.info('clients').get('connected_clients', 0)
        }
    except Exception as e:
        return {
            'status': 'unhealthy',
            'error': str(e)
        }


def check_cloudinary_connection() -> dict:
    """Check Cloudinary API health."""
    try:
        import cloudinary.api
        usage = cloudinary.api.usage()
        return {
            'status': 'healthy',
            'credits_used': usage.get('credits', {}).get('usage', 0),
            'plan': usage.get('plan', 'unknown')
        }
    except Exception as e:
        return {
            'status': 'unhealthy',
            'error': str(e)
        }


@health_bp.route('/health')
def health_check():
    """Basic health check - returns 200 if app is running."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'service': 'voter-id-generator',
        'version': '4.0'
    })


@health_bp.route('/health/ready')
def readiness_check():
    """
    Readiness check - returns 200 if app is ready to serve traffic.
    Checks all critical dependencies.
    """
    from app import mongo_client, gen_mongo_client, _redis_client
    import config
    
    checks = {
        'mongodb_voters': check_mongodb_connection(mongo_client, config.MONGO_DB_NAME),
        'mongodb_generated': check_mongodb_connection(gen_mongo_client, config.GEN_MONGO_DB_NAME),
        'redis': check_redis_connection(_redis_client),
        'cloudinary': check_cloudinary_connection(),
    }
    
    # Determine overall status
    critical_services = ['mongodb_voters', 'mongodb_generated', 'cloudinary']
    all_healthy = all(
        checks[svc]['status'] == 'healthy' 
        for svc in critical_services
    )
    
    status_code = 200 if all_healthy else 503
    
    return jsonify({
        'status': 'ready' if all_healthy else 'not_ready',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'checks': checks
    }), status_code


@health_bp.route('/health/live')
def liveness_check():
    """
    Liveness check - returns 200 if app process is alive.
    Used by orchestrators to detect if app needs restart.
    """
    return jsonify({
        'status': 'alive',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'python_version': sys.version,
        'uptime_seconds': 0  # Could track actual uptime if needed
    })


@health_bp.route('/health/metrics')
def metrics():
    """
    Basic metrics endpoint for monitoring.
    Returns key performance indicators.
    """
    from app import voters_col, gen_voters_col, stats_col, rate_limiter
    
    try:
        metrics_data = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'database': {
                'total_voters': voters_col.estimated_document_count(),
                'total_generated': gen_voters_col.estimated_document_count(),
                'total_stats': stats_col.estimated_document_count(),
            },
            'rate_limiter': {
                'active_keys': len(rate_limiter.requests),
            }
        }
        return jsonify(metrics_data)
    except Exception as e:
        return jsonify({
            'error': 'Failed to collect metrics',
            'message': str(e)
        }), 500
