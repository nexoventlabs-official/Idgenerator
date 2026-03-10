"""
Health Check and Monitoring Endpoints
======================================
Provides system health status for monitoring and alerting.
"""
from flask import jsonify, Blueprint
from datetime import datetime, timezone
import sys

health_bp = Blueprint('health', __name__)


def check_mysql_gen_connection() -> dict:
    """Check MySQL generated-data tables health."""
    try:
        from app import mysql_pool
        conn = mysql_pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = 'voter_id_generator' "
                    "AND TABLE_NAME IN ('generated_voters','generation_stats','otp_sessions','verified_mobiles','volunteer_requests','booth_agent_requests')"
                )
                tbl_count = cur.fetchone()['cnt']
            return {'status': 'healthy', 'database': 'voter_id_generator', 'tables': tbl_count}
        finally:
            conn.close()
    except Exception as e:
        return {'status': 'unhealthy', 'database': 'voter_id_generator', 'error': str(e)}


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


def check_mysql_connection() -> dict:
    """Check MySQL voters connection health."""
    try:
        from app import mysql_voters_pool
        conn = mysql_voters_pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return {'status': 'healthy', 'database': 'mysql_voters'}
        finally:
            conn.close()
    except Exception as e:
        return {'status': 'unhealthy', 'database': 'mysql_voters', 'error': str(e)}


@health_bp.route('/health/ready')
def readiness_check():
    """
    Readiness check - returns 200 if app is ready to serve traffic.
    Checks all critical dependencies.
    """
    from app import _redis_client
    
    checks = {
        'mysql_voters': check_mysql_connection(),
        'mysql_generated': check_mysql_gen_connection(),
        'redis': check_redis_connection(_redis_client),
        'cloudinary': check_cloudinary_connection(),
    }
    
    # Determine overall status
    critical_services = ['mysql_voters', 'mysql_generated', 'cloudinary']
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
    from app import mysql_pool, mysql_voters_pool, rate_limiter
    
    try:
        # Count voters from voters DB (sum across all assembly tables)
        conn = mysql_voters_pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT SUM(total_voters) AS cnt FROM tbl_assembly_consitituency")
                row = cur.fetchone()
                total_voters = row['cnt'] if row and row['cnt'] else 0
        finally:
            conn.close()

        # Count generated data from generated DB
        conn = mysql_pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM generated_voters")
                total_generated = cur.fetchone()['cnt']
                cur.execute("SELECT COUNT(*) AS cnt FROM generation_stats")
                total_stats = cur.fetchone()['cnt']
        finally:
            conn.close()

        metrics_data = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'database': {
                'total_voters': total_voters,
                'total_generated': total_generated,
                'total_stats': total_stats,
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
