"""
Voter ID Card Generator v4.0 - Web App
========================================
- User  : /       Enter Epic Number + optional photo -> Generate & Download Card
- Admin : /admin  View voters & generation stats

Database : MySQL (all data - voters, generated cards, stats, OTP, etc.)
Photos   : Cloudinary (user uploads)
Cards    : Cloudinary (generated_cards folder)
"""

import io
import json
import os
import random
import re
import secrets
import sys
import time
import threading
from datetime import datetime, timezone

from flask import (
    Flask, Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, session
)
from PIL import Image
from werkzeug.utils import secure_filename

import requests as http_requests
import cloudinary
import cloudinary.uploader
import cloudinary.api
import pymysql
from dbutils.pooled_db import PooledDB

try:
    import redis as _redis_lib
except ImportError:
    _redis_lib = None

import string

import config
from generate_cards import (
    setup_logging, generate_card, generate_serial_number,
    load_bold_font, get_text_width, load_member_photo
)
from security_fixes import (
    hash_pin, verify_pin, rate_limit, rate_limiter,
    validate_mobile, validate_epic, validate_pin, validate_otp,
    sanitize_search, validate_file_upload, login_tracker
)
from health_check import health_bp
from cloudinary_secure import generate_signed_url, generate_download_url
from face_detection import validate_photo_for_id_card, detect_face_in_image

# ══════════════════════════════════════════════════════════════════
#  PHASE 1: CELERY & CIRCUIT BREAKERS
# ══════════════════════════════════════════════════════════════════

# Import Celery tasks
from tasks import celery, generate_card_async

# Circuit breakers for external services
from pybreaker import CircuitBreaker

# Cloudinary circuit breaker
cloudinary_breaker = CircuitBreaker(
    fail_max=5,  # Open circuit after 5 failures
    reset_timeout=60,  # Keep circuit open for 60 seconds
    name='cloudinary'
)

# SMS API circuit breaker
sms_breaker = CircuitBreaker(
    fail_max=3,
    reset_timeout=120,
    name='sms_api'
)

# ── App Setup ────────────────────────────────────────────────────
app = Flask(__name__,
            template_folder=os.path.join(config.BASE_DIR, 'templates'),
            static_folder=os.path.join(config.BASE_DIR, 'static'))
app.secret_key = os.getenv('FLASK_SECRET', 'voter-id-gen-secret-2026')

# Trust proxy headers (Nginx → Varnish → Apache → PHP proxy → Gunicorn)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1, x_host=1, x_prefix=1)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max upload

# ══════════════════════════════════════════════════════════════════
#  PHASE 1: REDIS-BASED RATE LIMITER
# ══════════════════════════════════════════════════════════════════

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Replace in-memory rate limiter with Redis-based limiter
_limiter_storage = os.getenv('REDIS_URL') if os.getenv('REDIS_URL') else 'memory://'
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri=_limiter_storage,
    default_limits=["2000 per day", "500 per hour"],
    storage_options={"socket_connect_timeout": 5, "max_connections": 3} if os.getenv('REDIS_URL') else {},
    strategy="fixed-window"
)

ALLOWED_IMG = {'png', 'jpg', 'jpeg', 'bmp'}

logger = setup_logging()

# ══════════════════════════════════════════════════════════════════
#  PHASE 2: REDIS SESSION STORE (For Horizontal Scaling)
# ══════════════════════════════════════════════════════════════════

# Session configuration — use Flask's default signed cookie sessions
# This works reliably through proxy chains (Nginx → Varnish → Apache → PHP → Gunicorn)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') != 'development'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours
logger.info("Using Flask signed cookie sessions")

# ══════════════════════════════════════════════════════════════════
#  SECURITY: HTTPS ENFORCEMENT & CORS
# ══════════════════════════════════════════════════════════════════

# SECURITY FIX: Enforce HTTPS in production
from flask_talisman import Talisman
if os.getenv('FLASK_ENV') != 'development':
    Talisman(app, 
             force_https=True,
             strict_transport_security=True,
             strict_transport_security_max_age=31536000,
             content_security_policy={
                 'default-src': ["'self'", 'https://res.cloudinary.com', 'https://2factor.in'],
                 'img-src': ["'self'", 'https://res.cloudinary.com', 'data:'],
                 'style-src': ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com', 'https://cdn.jsdelivr.net'],
                 'script-src': ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net'],
                 'font-src': ["'self'", 'https://fonts.gstatic.com', 'https://cdn.jsdelivr.net'],
                 'connect-src': ["'self'", 'https://cdn.jsdelivr.net']
             })

# SECURITY FIX: Configure CORS for API endpoints
from flask_cors import CORS
CORS(app, resources={
    r"/api/*": {
        "origins": os.getenv('ALLOWED_ORIGINS', '*').split(','),
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# ══════════════════════════════════════════════════════════════════
#  SECURITY HEADERS & CDN CACHE CONTROL
# ══════════════════════════════════════════════════════════════════

@app.after_request
def set_security_headers(response):
    """SECURITY FIX: Add security headers to all responses."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    
    # PHASE 2: Add cache control headers for CDN
    # Static assets get long cache
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    # Admin/login/API responses must NEVER be cached (cookies/sessions break)
    elif request.path.startswith('/admin') or request.path.startswith('/api/') or 'session' in (response.headers.get('Set-Cookie', '').lower()):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    # HTML pages — no cache in dev, short cache in production
    elif os.getenv('FLASK_ENV') == 'development':
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    else:
        response.headers['Cache-Control'] = 'public, max-age=300'  # 5 minutes
    
    return response

for d in [config.MEMBER_PHOTOS_DIR, config.DATA_DIR, config.UPLOADS_DIR]:
    os.makedirs(d, exist_ok=True)

# ── IST Timezone Filter ──────────────────────────────────────
from datetime import timedelta
IST = timezone(timedelta(hours=5, minutes=30))

@app.template_filter('to_ist')
def to_ist(dt_str):
    """Convert a UTC ISO timestamp string to IST formatted string."""
    if not dt_str:
        return '-'
    try:
        s = str(dt_str).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ist_dt = dt.astimezone(IST)
        return ist_dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(dt_str)[:19].replace('T', ' ') if dt_str else '-'

# ── MySQL Setup (Voter rolls — READ-ONLY, separate DB) ────────────
mysql_voters_pool = PooledDB(
    creator=pymysql,
    maxconnections=20,
    mincached=2,
    maxcached=10,
    blocking=True,
    host=config.MYSQL_HOST,
    port=config.MYSQL_PORT,
    user=config.MYSQL_USER,
    passwd=config.MYSQL_PASSWORD,
    db=config.MYSQL_VOTERS_DB,
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor,
    autocommit=True,
)
VOTERS_TABLE = config.MYSQL_VOTERS_TABLE
logger.info("MySQL connection pool created for voter data (read-only).")

# ── Load assembly → table_name mapping at startup ─────────────────
_ASSEMBLY_TABLES = []   # list of {'table_name':..., 'assembly_name':..., 'assembly_no':..., 'district_name':...}
_TABLE_BY_ASSEMBLY = {} # assembly_name (lower) → table_name
try:
    _tmp_conn = mysql_voters_pool.connection()
    try:
        with _tmp_conn.cursor() as _cur:
            _cur.execute("SELECT table_name, assembly_name, assembly_no, district_name, total_voters FROM tbl_assembly_consitituency ORDER BY assembly_no")
            _ASSEMBLY_TABLES = list(_cur.fetchall())
            for _row in _ASSEMBLY_TABLES:
                _TABLE_BY_ASSEMBLY[(_row.get('assembly_name') or '').lower()] = _row['table_name']
    finally:
        _tmp_conn.close()
    logger.info("Loaded %d assembly table mappings.", len(_ASSEMBLY_TABLES))
except Exception as _e:
    logger.warning("Could not load assembly table mapping: %s", _e)

def _get_voter_tables(assembly: str = '', district: str = '') -> list[str]:
    """Return list of voter table names, optionally filtered by assembly/district."""
    if assembly:
        tbl = _TABLE_BY_ASSEMBLY.get(assembly.lower())
        return [tbl] if tbl else []
    if district:
        return [r['table_name'] for r in _ASSEMBLY_TABLES
                if (r.get('district_name') or '').lower() == district.lower()]
    return [r['table_name'] for r in _ASSEMBLY_TABLES]

# ── MySQL Setup (Generated data — READ/WRITE) ────────────────────
mysql_pool = PooledDB(
    creator=pymysql,
    maxconnections=50,
    mincached=5,
    maxcached=20,
    blocking=True,
    host=config.MYSQL_HOST,
    port=config.MYSQL_PORT,
    user=config.MYSQL_USER,
    passwd=config.MYSQL_PASSWORD,
    db=config.MYSQL_DB,
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor,
    autocommit=True,
)
logger.info("MySQL connection pool created for generated data.")

# ── Cloudinary Setup ─────────────────────────────────────────────
cloudinary.config(
    cloud_name=config.CLOUDINARY_CLOUD_NAME,
    api_key=config.CLOUDINARY_API_KEY,
    api_secret=config.CLOUDINARY_API_SECRET,
    secure=True,
)


# ══════════════════════════════════════════════════════════════════
#  REDIS CACHE SETUP  (P2 - optional, graceful degradation)
# ══════════════════════════════════════════════════════════════════

_redis_client = None
REDIS_DASHBOARD_KEY = 'voter_app:dashboard_stats'
REDIS_DASHBOARD_TTL = 60  # seconds
REDIS_EXTERNAL_STATS_KEY = 'voter_app:external_stats'
REDIS_EXTERNAL_STATS_TTL = 300  # 5 min for Cloudinary/SMS/dbstats (slow HTTP calls)
REDIS_DROPDOWN_VOTERS_KEY = 'voter_app:dropdown:voters'
REDIS_DROPDOWN_GEN_KEY = 'voter_app:dropdown:gen_voters'
REDIS_DROPDOWN_TTL = 300  # 5 min - assembly/district lists change very rarely

try:
    _redis_url = os.getenv('REDIS_URL')
    if _redis_lib and _redis_url:
        _cache_pool = _redis_lib.ConnectionPool.from_url(_redis_url, max_connections=3, socket_connect_timeout=2, decode_responses=True)
        _redis_client = _redis_lib.Redis(connection_pool=_cache_pool)
        _redis_client.ping()
        logger.info(f"Redis cache connected: {_redis_url}")
    else:
        logger.info("Redis not configured - dashboard cache disabled")
except Exception as _re:
    _redis_client = None
    logger.info(f"Redis not available (dashboard cache disabled): {_re}")


def _cache_get(key: str) -> dict | None:
    """Get a JSON value from Redis cache. Returns None on miss or if Redis unavailable."""
    if not _redis_client:
        return None
    try:
        raw = _redis_client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _cache_set(key: str, value: dict, ttl: int = 60):
    """Set a JSON value in Redis cache with TTL. No-op if Redis unavailable."""
    if not _redis_client:
        return
    try:
        _redis_client.setex(key, ttl, json.dumps(value))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  FULLTEXT INDEX SETUP (run once at startup — idempotent)
# ══════════════════════════════════════════════════════════════════

def _ensure_indexes():
    """Create indexes for fast search. Safe to call repeatedly — skips if already exists.
    - Voter tables (234): B-tree on EPIC_NO only (FULLTEXT too slow to create on 234 tables)
    - Generated voters (1 table): FULLTEXT + B-tree indexes
    """
    def _has_index(cur, db, table, index_name):
        cur.execute(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s LIMIT 1",
            (db, table, index_name)
        )
        return cur.fetchone() is not None

    # ── Voter assembly tables: B-tree on EPIC_NO for fast exact lookups ──
    try:
        conn = _get_voters_mysql()
        try:
            with conn.cursor() as cur:
                for tbl in _get_voter_tables():
                    if not _has_index(cur, config.MYSQL_VOTERS_DB, tbl, 'idx_epic_no'):
                        try:
                            cur.execute(f"ALTER TABLE `{tbl}` ADD INDEX `idx_epic_no` (`EPIC_NO`)")
                            logger.info("Created idx_epic_no on %s", tbl)
                        except Exception:
                            pass
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Could not create voter indexes: %s", e)

    # ── Generated voters table: FULLTEXT + B-tree ──
    try:
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                if not _has_index(cur, config.MYSQL_DB, 'generated_voters', 'ft_gen_search'):
                    try:
                        cur.execute(
                            "ALTER TABLE `generated_voters` ADD FULLTEXT INDEX `ft_gen_search` "
                            "(`EPIC_NO`, `FM_NAME_EN`, `LASTNAME_EN`, `ptc_code`, `MOBILE_NO`)"
                        )
                        logger.info("Created FULLTEXT index on generated_voters")
                    except Exception as e:
                        logger.debug("FULLTEXT index skip generated_voters: %s", e)
                for idx_name, cols in [
                    ('idx_gv_assembly', '`ASSEMBLY_NAME`'),
                    ('idx_gv_district', '`DISTRICT_NAME`'),
                    ('idx_gv_epic', '`EPIC_NO`'),
                ]:
                    if not _has_index(cur, config.MYSQL_DB, 'generated_voters', idx_name):
                        try:
                            cur.execute(f"ALTER TABLE `generated_voters` ADD INDEX `{idx_name}` ({cols})")
                        except Exception:
                            pass
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Could not create generated_voters indexes: %s", e)

# Thread is started later (after _get_voters_mysql and _get_voter_tables are defined)

# ══════════════════════════════════════════════════════════════════
#  SQL SEARCH / FILTER HELPERS
# ══════════════════════════════════════════════════════════════════

def _build_search_where(search: str, fields: list[str]) -> tuple[str, list]:
    """Build a WHERE clause for searching across multiple fields. Returns (clause, params)."""
    if not search:
        return '', []
    like_val = f'%{search}%'
    parts = [f'`{f}` LIKE %s' for f in fields]
    return f"({' OR '.join(parts)})", [like_val] * len(parts)


def _build_fulltext_where(search: str, ft_index_cols: str = 'EPIC_NO, FM_NAME_EN, LASTNAME_EN, ASSEMBLY_NAME') -> tuple[str, list]:
    """Build a FULLTEXT MATCH...AGAINST WHERE clause with LIKE fallback for short/partial queries.
    Returns (where_clause, params).  Used only for `generated_voters` (single table)."""
    if not search:
        return '', []
    # FULLTEXT works best with 3+ chars; for shorter use LIKE
    if len(search) >= 3:
        ft_term = '+' + search + '*'
        return f"MATCH({ft_index_cols}) AGAINST(%s IN BOOLEAN MODE)", [ft_term]
    return "`EPIC_NO` LIKE %s", [f"{search}%"]


def _like_where_generated(search: str) -> tuple[str, list]:
    """LIKE-based fallback for generated_voters search (when FULLTEXT unavailable)."""
    if not search:
        return '', []
    pat = f"%{search}%"
    return ("(`EPIC_NO` LIKE %s OR `FM_NAME_EN` LIKE %s OR `LASTNAME_EN` LIKE %s "
            "OR `ptc_code` LIKE %s OR `MOBILE_NO` LIKE %s)"), [pat]*5


def _gen_mysql_upsert(table: str, data: dict, unique_keys: list[str]) -> tuple[str, list]:
    """Build INSERT ... ON DUPLICATE KEY UPDATE query."""
    cols = list(data.keys())
    placeholders = ', '.join(['%s'] * len(cols))
    col_names = ', '.join(f'`{c}`' for c in cols)
    update_parts = ', '.join(f'`{c}` = VALUES(`{c}`)' for c in cols if c not in unique_keys)
    sql = f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_parts}"
    return sql, list(data.values())



# ══════════════════════════════════════════════════════════════════
#  MYSQL DATABASE HELPERS
# ══════════════════════════════════════════════════════════════════

def _get_voters_mysql():
    """Get a connection from the voters MySQL pool (read-only DB)."""
    return mysql_voters_pool.connection()


def _get_mysql():
    """Get a connection from the generated-data MySQL pool (read/write DB)."""
    return mysql_pool.connection()

# Now that _get_voters_mysql, _get_mysql, _get_voter_tables are defined, start index setup
threading.Thread(target=_ensure_indexes, daemon=True).start()


def _translate_voter_row(row: dict) -> dict | None:
    """Map MySQL column names to the internal field names used across the app.
    Also preserves raw uppercase DB column values for generated_voters storage."""
    if not row:
        return None
    r = {
        'epic_no': row.get('EPIC_NO') or '',
        'name': f"{row.get('FM_NAME_EN') or ''} {row.get('LASTNAME_EN') or ''}".strip(),
        'assembly': str(row.get('AC_NO') or ''),
        'age': row.get('AGE') or '',
        'sex': row.get('GENDER') or '',
        'relation_type': row.get('RLN_TYPE') or '',
        'relation_name': f"{row.get('RLN_FM_NM_EN') or ''} {row.get('RLN_L_NM_EN') or ''}".strip(),
        'mobile': row.get('MOBILE_NO') or '',
        'part_no': str(row.get('PART_NO') or ''),
        'section_no': str(row.get('SECTION_NO') or ''),
        'slno_in_part': str(row.get('SLNOINPART') or ''),
        'house_no': row.get('C_HOUSE_NO') or '',
        'dob': row.get('DOB') or '',
        'name_v1': f"{row.get('FM_NAME_V1') or ''} {row.get('LASTNAME_V1') or ''}".strip(),
        'relation_name_v1': f"{row.get('RLN_FM_NM_V1') or ''} {row.get('RLN_L_NM_V1') or ''}".strip(),
        'house_no_v1': row.get('C_HOUSE_NO_V1') or '',
        'org_list_no': str(row.get('ORG_LIST_NO') or ''),
        'assembly_name': row.get('ASSEMBLY_NAME') or '',
        'district': row.get('DISTRICT_NAME') or '',
        'district_id': row.get('DISTRICT_ID') or '',
        'id': row.get('id', ''),
    }
    # Preserve raw DB column values (needed for generated_voters upsert)
    for k in ('AC_NO','ASSEMBLY_NAME','PART_NO','SECTION_NO','SLNOINPART','C_HOUSE_NO','C_HOUSE_NO_V1',
              'FM_NAME_EN','LASTNAME_EN','FM_NAME_V1','LASTNAME_V1','RLN_TYPE',
              'RLN_FM_NM_EN','RLN_L_NM_EN','RLN_FM_NM_V1','RLN_L_NM_V1',
              'EPIC_NO','GENDER','AGE','DOB','MOBILE_NO','ORG_LIST_NO','DISTRICT_ID','DISTRICT_NAME'):
        if k in row:
            r[k] = row[k]
    return r


def _translate_gen_row(row: dict) -> dict | None:
    """Translate a generated_voters row to internal format (lowercase keys)."""
    if not row:
        return None
    r = _translate_voter_row(row)
    for k in ('ptc_code','photo_url','card_url','secret_pin','referral_id','referral_link',
              'referred_by_ptc','referred_by_referral_id','referred_members_count',
              'source','generated_at','created_at'):
        r[k] = row.get(k)
    return r


def load_voters_from_db() -> list[dict]:
    """Return all voters from MySQL (across all assembly tables)."""
    conn = _get_voters_mysql()
    try:
        all_voters = []
        with conn.cursor() as cur:
            for tbl in _get_voter_tables():
                cur.execute(f"SELECT * FROM `{tbl}`")
                all_voters.extend(_translate_voter_row(r) for r in cur.fetchall())
        return all_voters
    finally:
        conn.close()


def find_voter_by_epic(epic_no: str) -> dict | None:
    """Find a voter by EPIC number across all assembly tables.
    Optimized: uses parallel batched queries instead of sequential table iteration.
    Results are cached in Redis for 10 min since voter data is read-only."""
    epic_no = epic_no.strip().upper()
    if not epic_no:
        return None

    # Check Redis cache first
    cache_key = f'voter_app:epic:{epic_no}'
    cached = _cache_get(cache_key)
    if cached:
        return cached if cached.get('epic_no') else None

    tables = _get_voter_tables()
    if not tables:
        return None

    conn = _get_voters_mysql()
    try:
        with conn.cursor() as cur:
            # Query tables in batches using UNION ALL (each subquery needs parentheses for MariaDB)
            batch_size = 30
            for i in range(0, len(tables), batch_size):
                batch = tables[i:i + batch_size]
                unions = []
                params = []
                for tbl in batch:
                    unions.append(f"(SELECT * FROM `{tbl}` WHERE `EPIC_NO` = %s LIMIT 1)")
                    params.append(epic_no)
                sql = " UNION ALL ".join(unions) + " LIMIT 1"
                cur.execute(sql, tuple(params))
                row = cur.fetchone()
                if row:
                    result = _translate_voter_row(row)
                    _cache_set(cache_key, result, 600)  # Cache for 10 min
                    return result
    finally:
        conn.close()

    # Cache negative result too (avoid repeated lookups for invalid EPICs)
    _cache_set(cache_key, {'epic_no': ''}, 120)  # Cache miss for 2 min
    return None


def _mysql_count(where: str = '', params: tuple = (), tables: list[str] | None = None) -> int:
    """Return count of voters matching optional WHERE clause across assembly tables.
    Optimized: uses tbl_assembly_consitituency.total_voters when no WHERE filter."""
    target_tables = tables or _get_voter_tables()
    if not target_tables:
        return 0
    # Fast path: no WHERE clause → use pre-computed totals from mapping table
    if not where:
        target_set = set(target_tables)
        return sum(
            int(r.get('total_voters') or 0) for r in _ASSEMBLY_TABLES
            if r['table_name'] in target_set
        )
    # Slow path: search filter → query only target tables
    conn = _get_voters_mysql()
    try:
        total = 0
        with conn.cursor() as cur:
            for tbl in target_tables:
                sql = f"SELECT COUNT(*) AS cnt FROM `{tbl}`"
                if where:
                    sql += f" WHERE {where}"
                cur.execute(sql, params)
                row = cur.fetchone()
                total += row['cnt'] if row else 0
        return total
    finally:
        conn.close()


def _mysql_distinct(column: str) -> list[str]:
    """Return sorted distinct non-empty values for a column using assembly mapping."""
    if column == 'ASSEMBLY_NAME':
        return sorted(set(r.get('assembly_name') or '' for r in _ASSEMBLY_TABLES) - {''})
    if column == 'DISTRICT_NAME':
        return sorted(set(r.get('district_name') or '' for r in _ASSEMBLY_TABLES) - {''})
    # fallback: scan tables
    conn = _get_voters_mysql()
    try:
        values = set()
        with conn.cursor() as cur:
            for tbl in _get_voter_tables():
                cur.execute(
                    f"SELECT DISTINCT `{column}` FROM `{tbl}` "
                    f"WHERE `{column}` IS NOT NULL AND `{column}` != '' "
                )
                for row in cur.fetchall():
                    values.add(str(row[column]))
        return sorted(values)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
#  GENERATION STATS (MySQL)
# ══════════════════════════════════════════════════════════════════

def generate_ptc_code() -> str:
    """Generate a unique PTC-XXXXXXX code (collision-free under concurrent load)."""
    import uuid
    uid = uuid.uuid4().hex[:7].upper()
    return f'PTC-{uid}'


def save_generated_voter(voter: dict, mobile: str, photo_url: str, card_url: str, ptc_code: str,
                        referred_by_ptc: str = '', referred_by_referral_id: str = '',
                        secret_pin: str = ''):
    """Save a generated voter record to MySQL with upsert on (EPIC_NO, MOBILE_NO)."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    hashed = hash_pin(secret_pin) if secret_pin else None
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO generated_voters
                   (ptc_code, AC_NO, ASSEMBLY_NAME, PART_NO, SECTION_NO, SLNOINPART,
                    C_HOUSE_NO, C_HOUSE_NO_V1,
                    FM_NAME_EN, LASTNAME_EN, FM_NAME_V1, LASTNAME_V1,
                    RLN_TYPE, RLN_FM_NM_EN, RLN_L_NM_EN, RLN_FM_NM_V1, RLN_L_NM_V1,
                    EPIC_NO, GENDER, AGE, DOB, MOBILE_NO, ORG_LIST_NO, DISTRICT_NAME,
                    photo_url, card_url, secret_pin,
                    referred_by_ptc, referred_by_referral_id, generated_at, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     ptc_code=VALUES(ptc_code),
                     AC_NO=VALUES(AC_NO), ASSEMBLY_NAME=VALUES(ASSEMBLY_NAME),
                     PART_NO=VALUES(PART_NO),
                     SECTION_NO=VALUES(SECTION_NO), SLNOINPART=VALUES(SLNOINPART),
                     C_HOUSE_NO=VALUES(C_HOUSE_NO), C_HOUSE_NO_V1=VALUES(C_HOUSE_NO_V1),
                     FM_NAME_EN=VALUES(FM_NAME_EN), LASTNAME_EN=VALUES(LASTNAME_EN),
                     FM_NAME_V1=VALUES(FM_NAME_V1), LASTNAME_V1=VALUES(LASTNAME_V1),
                     RLN_TYPE=VALUES(RLN_TYPE), RLN_FM_NM_EN=VALUES(RLN_FM_NM_EN),
                     RLN_L_NM_EN=VALUES(RLN_L_NM_EN), RLN_FM_NM_V1=VALUES(RLN_FM_NM_V1),
                     RLN_L_NM_V1=VALUES(RLN_L_NM_V1),
                     GENDER=VALUES(GENDER), AGE=VALUES(AGE), DOB=VALUES(DOB),
                     ORG_LIST_NO=VALUES(ORG_LIST_NO), DISTRICT_NAME=VALUES(DISTRICT_NAME),
                     photo_url=VALUES(photo_url), card_url=VALUES(card_url),
                     secret_pin=COALESCE(VALUES(secret_pin), secret_pin),
                     referred_by_ptc=VALUES(referred_by_ptc),
                     referred_by_referral_id=VALUES(referred_by_referral_id),
                     generated_at=VALUES(generated_at)""",
                (ptc_code,
                 voter.get('AC_NO'), voter.get('ASSEMBLY_NAME'),
                 voter.get('PART_NO'),
                 voter.get('SECTION_NO'), voter.get('SLNOINPART'),
                 voter.get('C_HOUSE_NO'), voter.get('C_HOUSE_NO_V1'),
                 voter.get('FM_NAME_EN', ''), voter.get('LASTNAME_EN', ''),
                 voter.get('FM_NAME_V1', ''), voter.get('LASTNAME_V1', ''),
                 voter.get('RLN_TYPE', ''),
                 voter.get('RLN_FM_NM_EN', ''), voter.get('RLN_L_NM_EN', ''),
                 voter.get('RLN_FM_NM_V1', ''), voter.get('RLN_L_NM_V1', ''),
                 voter.get('EPIC_NO', voter.get('epic_no', '')),
                 voter.get('GENDER', ''), voter.get('AGE'),
                 voter.get('DOB', ''), mobile, voter.get('ORG_LIST_NO'),
                 voter.get('DISTRICT_NAME'),
                 photo_url, card_url, hashed,
                 referred_by_ptc or None, referred_by_referral_id or None,
                 now, now)
            )
            if referred_by_ptc:
                cur.execute(
                    "UPDATE generated_voters SET referred_members_count = referred_members_count + 1 WHERE ptc_code = %s",
                    (referred_by_ptc,)
                )
    finally:
        conn.close()


def get_or_create_referral(ptc_code: str) -> dict | None:
    """Return { referral_id, referral_link } - idempotent."""
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT referral_id, referral_link FROM generated_voters WHERE ptc_code = %s", (ptc_code,))
            row = cur.fetchone()
            if not row:
                return None
            if row.get('referral_id'):
                return {'referral_id': row['referral_id'], 'referral_link': row['referral_link']}
            import uuid
            rid = 'REF-' + uuid.uuid4().hex[:8].upper()
            link = f"{config.BASE_URL}/refer/{ptc_code}/{rid}"
            cur.execute(
                "UPDATE generated_voters SET referral_id=%s, referral_link=%s WHERE ptc_code=%s",
                (rid, link, ptc_code)
            )
            return {'referral_id': rid, 'referral_link': link}
    finally:
        conn.close()


def generate_secret_pin() -> str:
    """Generate a cryptographically secure 4-digit PIN."""
    return f"{secrets.randbelow(10000):04d}"


def increment_generation_count(epic_no: str, photo_url: str = '', card_url: str = '',
                               auth_mobile: str = '', secret_pin: str = ''):
    """Increment generation count; optionally update photo_url, card_url, auth_mobile, secret_pin."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    hashed = hash_pin(secret_pin) if secret_pin else None
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM generation_stats WHERE epic_no = %s", (epic_no,))
            if cur.fetchone():
                parts = ['`count` = `count` + 1', 'last_generated = %s']
                vals = [now]
                if photo_url:
                    parts.append('photo_url = %s'); vals.append(photo_url)
                if card_url:
                    parts.append('card_url = %s'); vals.append(card_url)
                if auth_mobile:
                    parts.append('auth_mobile = %s'); vals.append(auth_mobile)
                if hashed:
                    parts.append('secret_pin = %s'); vals.append(hashed)
                vals.append(epic_no)
                cur.execute(f"UPDATE generation_stats SET {', '.join(parts)} WHERE epic_no = %s", vals)
            else:
                cur.execute(
                    """INSERT INTO generation_stats (epic_no, `count`, last_generated, photo_url, card_url, auth_mobile, secret_pin)
                       VALUES (%s, 1, %s, %s, %s, %s, %s)""",
                    (epic_no, now, photo_url or '', card_url or '', auth_mobile or '', hashed)
                )
    finally:
        conn.close()


def get_voter_gen_count(epic_no: str) -> int:
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT `count` FROM generation_stats WHERE epic_no = %s", (epic_no,))
            row = cur.fetchone()
            return row['count'] if row else 0
    finally:
        conn.close()


def get_voter_photo_url(epic_no: str) -> str:
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT photo_url FROM generation_stats WHERE epic_no = %s", (epic_no,))
            row = cur.fetchone()
            return row['photo_url'] if row else ''
    finally:
        conn.close()


def get_voter_card_url(epic_no: str) -> str:
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT card_url FROM generation_stats WHERE epic_no = %s", (epic_no,))
            row = cur.fetchone()
            return row['card_url'] if row else ''
    finally:
        conn.close()


def get_all_stats() -> dict:
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no, `count`, last_generated, photo_url, card_url, auth_mobile FROM generation_stats")
            result = {}
            for row in cur.fetchall():
                result[row['epic_no']] = {
                    'count': row.get('count', 0),
                    'last_generated': str(row.get('last_generated', '')) if row.get('last_generated') else '',
                    'photo_url': row.get('photo_url', ''),
                    'card_url': row.get('card_url', ''),
                    'auth_mobile': row.get('auth_mobile', ''),
                }
            return result
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
#  CLOUDINARY UPLOAD
# ══════════════════════════════════════════════════════════════════

def upload_photo_to_cloudinary(image: Image.Image, epic_no: str) -> str:
    """Upload a PIL image to Cloudinary, return the secure URL."""
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=90)
    buf.seek(0)

    result = cloudinary.uploader.upload(
        buf,
        folder=config.CLOUDINARY_PHOTO_FOLDER,
        public_id=epic_no,
        overwrite=True,
        resource_type='image',
    )
    return result.get('secure_url', '')


def upload_card_to_cloudinary(card_image: Image.Image, epic_no: str) -> str:
    """Upload generated card image to Cloudinary (overwrite mode - no delete needed)."""
    safe_id = epic_no.replace('/', '_').replace('\\', '_')

    buf = io.BytesIO()
    card_image.save(buf, format='JPEG', quality=config.JPEG_QUALITY)
    buf.seek(0)

    result = cloudinary.uploader.upload(
        buf,
        folder=config.CLOUDINARY_CARDS_FOLDER,
        public_id=safe_id,
        overwrite=True,
        invalidate=True,
        resource_type='image',
    )
    url = result.get('secure_url', '')
    logger.info(f"Uploaded card to Cloudinary: {url}")
    return url


# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def allowed_file(filename, exts):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in exts


def _get_external_stats() -> dict:
    """Fetch slow external stats (Cloudinary, SMS, dbstats) - cached 5 min."""
    cached = _cache_get(REDIS_EXTERNAL_STATS_KEY)
    if cached and 'db1_size_mb' in cached:
        return cached

    result = {'mysql_size_mb': 0, 'db1_size_mb': 0, 'db2_size_mb': 0, 'db2_objects': 0,
              'cloudinary_credits': 'N/A', 'sms_balance': 'N/A'}
    try:
        # Voters DB size
        conn1 = _get_voters_mysql()
        try:
            with conn1.cursor() as cur:
                cur.execute(
                    "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb "
                    "FROM information_schema.TABLES WHERE table_schema = %s",
                    (config.MYSQL_VOTERS_DB,)
                )
                row = cur.fetchone()
                result['db1_size_mb'] = float(row['size_mb']) if row and row['size_mb'] else 0
        finally:
            conn1.close()

        # Generated data DB size + row count
        conn2 = _get_mysql()
        try:
            with conn2.cursor() as cur:
                cur.execute(
                    "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb, "
                    "SUM(table_rows) AS total_rows "
                    "FROM information_schema.TABLES WHERE table_schema = %s",
                    (config.MYSQL_DB,)
                )
                row = cur.fetchone()
                result['db2_size_mb'] = float(row['size_mb']) if row and row['size_mb'] else 0
                result['db2_objects'] = int(row['total_rows']) if row and row['total_rows'] else 0
        finally:
            conn2.close()

        result['mysql_size_mb'] = round(result['db1_size_mb'] + result['db2_size_mb'], 2)
    except Exception:
        pass

    try:
        cli_usage = cloudinary.api.usage()
        used = cli_usage.get('credits', {}).get('usage', 0)
        result['cloudinary_credits'] = f"{round(used, 2)}"
    except Exception:
        pass

    sms_api_key = os.getenv('SMS_API_KEY', '')
    if sms_api_key:
        try:
            resp = http_requests.get(f"https://2factor.in/API/V1/{sms_api_key}/BAL/SMS", timeout=3)
            if resp.status_code == 200:
                result['sms_balance'] = resp.json().get('Details', 'N/A')
        except Exception:
            pass

    _cache_set(REDIS_EXTERNAL_STATS_KEY, result, REDIS_EXTERNAL_STATS_TTL)
    return result


def _get_cached_dropdowns(table_or_name: str, cache_key: str) -> tuple[list, list]:
    """Return (assemblies, districts) from cache or distinct() scan.
    table_or_name is now a MySQL table name or 'mysql_voters'."""
    cached = _cache_get(cache_key)
    if cached:
        return cached.get('assemblies', []), cached.get('districts', [])
    if table_or_name == 'mysql_voters':
        assemblies = _mysql_distinct('ASSEMBLY_NAME')
        districts = _mysql_distinct('DISTRICT_NAME')
    else:
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT DISTINCT ASSEMBLY_NAME FROM `{table_or_name}` WHERE ASSEMBLY_NAME IS NOT NULL AND ASSEMBLY_NAME != '' ORDER BY ASSEMBLY_NAME")
                assemblies = [str(r['ASSEMBLY_NAME']) for r in cur.fetchall()]
                cur.execute(f"SELECT DISTINCT DISTRICT_NAME FROM `{table_or_name}` WHERE DISTRICT_NAME IS NOT NULL AND DISTRICT_NAME != '' ORDER BY DISTRICT_NAME")
                districts = [str(r['DISTRICT_NAME']) for r in cur.fetchall()]
        finally:
            conn.close()
    _cache_set(cache_key, {'assemblies': assemblies, 'districts': districts}, REDIS_DROPDOWN_TTL)
    return assemblies, districts


def get_dashboard_stats():
    # P2: Try Redis cache first - instant dashboard load
    cached = _cache_get(REDIS_DASHBOARD_KEY)
    if cached:
        return cached

    try:
        # MySQL voter count
        total_voters = _mysql_count()

        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                # Stats aggregation
                cur.execute("""SELECT
                    COUNT(CASE WHEN `count` > 0 THEN 1 END) AS total_generated,
                    COALESCE(SUM(`count`), 0) AS total_generations,
                    COUNT(CASE WHEN card_url != '' AND card_url IS NOT NULL THEN 1 END) AS cards_on_cloud
                    FROM generation_stats""")
                sa = cur.fetchone()
                total_generated = sa['total_generated']
                total_generations = sa['total_generations']
                cards_on_cloud = sa['cards_on_cloud']

                # Generated voters count
                cur.execute("SELECT COUNT(*) AS cnt FROM generated_voters")
                generated_voters_count = cur.fetchone()['cnt']

                # Referral total
                cur.execute("SELECT COALESCE(SUM(referred_members_count), 0) AS total FROM generated_voters")
                total_referrals = cur.fetchone()['total']

                # Volunteer status counts
                cur.execute("SELECT status, COUNT(*) AS cnt FROM volunteer_requests GROUP BY status")
                vol_counts = {r['status']: r['cnt'] for r in cur.fetchall()}
                pending_volunteers = vol_counts.get('pending', 0)
                confirmed_volunteers = vol_counts.get('confirmed', 0)

                # Booth agent status counts
                cur.execute("SELECT status, COUNT(*) AS cnt FROM booth_agent_requests GROUP BY status")
                ba_counts = {r['status']: r['cnt'] for r in cur.fetchall()}
                pending_booth_agents = ba_counts.get('pending', 0)
                confirmed_booth_agents = ba_counts.get('confirmed', 0)
        finally:
            conn.close()

        db_connected = True

    except Exception:
        total_voters = 0
        total_generated = 0
        total_generations = 0
        cards_on_cloud = 0
        generated_voters_count = 0
        db_connected = False
        total_referrals = 0
        pending_volunteers = 0
        confirmed_volunteers = 0
        pending_booth_agents = 0
        confirmed_booth_agents = 0

    # External stats loaded via AJAX separately (/api/external-stats)
    # Don't block dashboard render on slow external calls

    result = {
        'total_voters': total_voters,
        'total_generated': total_generated,
        'total_generations': total_generations,
        'cards_on_cloud': cards_on_cloud,
        'generated_voters_count': generated_voters_count,
        'db_connected': db_connected,
        'mysql_size_mb': '...',  # loaded via external stats AJAX
        'cloudinary_credits': '...',
        'sms_balance': '...',
        'total_referrals': total_referrals,
        'pending_volunteers': pending_volunteers,
        'confirmed_volunteers': confirmed_volunteers,
        'pending_booth_agents': pending_booth_agents,
        'confirmed_booth_agents': confirmed_booth_agents,
    }

    # P2: Cache in Redis for 60s
    _cache_set(REDIS_DASHBOARD_KEY, result, REDIS_DASHBOARD_TTL)
    return result


# ══════════════════════════════════════════════════════════════════
#  PUBLIC USER ROUTES  (/)
# ══════════════════════════════════════════════════════════════════

@app.route('/favicon.ico')
def favicon():
    from flask import send_from_directory
    return send_from_directory(app.static_folder, 'favicon.jpg', mimetype='image/jpeg')


@app.route('/google17d450ee87a4cb34.html')
def google_site_verification():
    return 'google-site-verification: google17d450ee87a4cb34.html', 200, {'Content-Type': 'text/html'}


@app.route('/robots.txt')
def robots_txt():
    robots_content = """User-agent: *
Allow: /
Disallow: /admin/
Disallow: /api/
Disallow: /card/

Sitemap: https://www.puratchithaai.org/sitemap.xml
"""
    return robots_content, 200, {'Content-Type': 'text/plain'}


@app.route('/sitemap.xml')
def sitemap_xml():
    sitemap_content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.puratchithaai.org/</loc>
    <lastmod>2026-03-07</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return sitemap_content, 200, {'Content-Type': 'application/xml'}


@app.route('/cronjob')
def cronjob():
    return 'OK', 200, {'Content-Type': 'text/plain'}


@app.route('/')
def user_home():
    resp = app.make_response(render_template('user/chatbot.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ── Chatbot API Endpoints ────────────────────────────────────────

@app.route('/api/chat/send-otp', methods=['POST'])
@limiter.limit("3 per 5 minutes")  # PHASE 1: Use Redis-based rate limiter
def chat_send_otp():
    """Generate and send OTP to mobile number via 2Factor.in - rate-limited."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    
    # Validate mobile number
    valid, result = validate_mobile(mobile)
    if not valid:
        return jsonify({'success': False, 'message': result}), 400
    mobile = result

    # Rate limit: max 1 OTP per mobile per 60 seconds
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM otp_sessions WHERE mobile = %s", (mobile,))
            existing = cur.fetchone()
    finally:
        conn.close()
    if existing:
        try:
            created = existing['created_at']
            if isinstance(created, str):
                created = datetime.fromisoformat(created)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - created).total_seconds()
            if elapsed < 60:
                wait = int(60 - elapsed)
                return jsonify({'success': False, 'message': f'Please wait {wait}s before requesting another OTP.'}), 429
        except Exception:
            pass

    # SECURITY FIX: Use cryptographically secure random for OTP
    otp = str(secrets.randbelow(900000) + 100000)

    # Send OTP via 2Factor.in FIRST, only store in DB if SMS succeeds
    otp_sent = False
    sms_api_key = os.getenv('SMS_API_KEY', '')
    if sms_api_key:
        try:
            @sms_breaker
            def send_sms():
                resp = http_requests.get(
                    f'https://2factor.in/API/V1/{sms_api_key}/SMS/{mobile}/{otp}',
                    timeout=15
                )
                if resp.status_code == 200 and resp.json().get('Status') == 'Success':
                    return True
                return False

            otp_sent = send_sms()
            if otp_sent:
                logger.info(f"OTP call sent to {mobile[:2]}****{mobile[-2:]}")
        except Exception as e:
            logger.warning(f"OTP send failed for {mobile[:2]}****{mobile[-2:]}: {e}")
            if 'CircuitBreakerError' in str(type(e).__name__):
                logger.warning("SMS API circuit breaker is OPEN - service unavailable")

    if not otp_sent:
        logger.warning(f"OTP not sent for {mobile[:2]}****{mobile[-2:]}")
        return jsonify({'success': False, 'message': 'Could not send OTP. Please try again.'}), 500

    # Store OTP in DB only after SMS was sent successfully
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO otp_sessions (mobile, otp, created_at, verified)
                   VALUES (%s, %s, %s, 0)
                   ON DUPLICATE KEY UPDATE otp = VALUES(otp), created_at = VALUES(created_at), verified = 0, purpose = NULL""",
                (mobile, otp, now)
            )
    finally:
        conn.close()

    return jsonify({'success': True})


@app.route('/api/chat/verify-otp', methods=['POST'])
@rate_limit(max_requests=5, window_seconds=300)  # 5 attempts per 5 minutes
def chat_verify_otp():
    """Verify OTP for mobile number."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    otp = data.get('otp', '').strip()

    # Validate inputs
    valid_mobile, mobile_result = validate_mobile(mobile)
    if not valid_mobile:
        return jsonify({'success': False, 'message': mobile_result}), 400
    mobile = mobile_result
    
    valid_otp, otp_result = validate_otp(otp)
    if not valid_otp:
        return jsonify({'success': False, 'message': otp_result}), 400
    otp = otp_result

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT otp, created_at FROM otp_sessions WHERE mobile = %s", (mobile,))
            doc = cur.fetchone()
    finally:
        conn.close()
    if not doc:
        logger.warning(f"OTP verify: no session found for mobile {mobile[:2]}****{mobile[-2:]}")
        return jsonify({'success': False, 'message': 'Invalid OTP'}), 400
    if doc.get('otp') != otp:
        logger.warning(f"OTP verify: mismatch for {mobile[:2]}****{mobile[-2:]} — sent={otp!r} db={doc.get('otp')!r}")
        return jsonify({'success': False, 'message': 'Invalid OTP'}), 400

    # Check expiry (5 minutes)
    try:
        created = doc['created_at']
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            return jsonify({'success': False, 'message': 'OTP expired. Please request a new one.'}), 400
    except Exception:
        pass

    # Mark OTP as verified (but don't mark mobile as verified yet - that happens after card generation)
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE otp_sessions SET verified = 1 WHERE mobile = %s", (mobile,))
    finally:
        conn.close()
    
    # SECURITY FIX: Store verified mobile in session for authorization checks
    session['verified_mobile'] = mobile
    session.permanent = True

    # Check if this mobile already has a linked card
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no, card_url, name FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
            gen_doc = None
            if stat and stat.get('card_url'):
                cur.execute("SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, photo_url FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
                gen_doc = cur.fetchone()
    finally:
        conn.close()
    if stat and stat.get('card_url'):
        return jsonify({
            'success': True,
            'has_card': True,
            'epic_no': stat.get('epic_no', ''),
            'card_url': stat.get('card_url', ''),
            'voter_name': (gen_doc.get('name', '') if gen_doc else '') or stat.get('name', ''),
            'photo_url': (gen_doc.get('photo_url', '') if gen_doc else '')
        })

    return jsonify({'success': True, 'has_card': False})


@app.route('/api/chat/check-mobile', methods=['POST'])
def chat_check_mobile():
    """Check if a mobile number already has a linked card (and thus a PIN)."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no, card_url, secret_pin FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
    finally:
        conn.close()
    if stat and stat.get('card_url'):
        has_pin = bool(stat.get('secret_pin'))
        return jsonify({'success': True, 'has_card': True, 'has_pin': has_pin})
    return jsonify({'success': True, 'has_card': False, 'has_pin': False})


@app.route('/api/chat/verify-pin', methods=['POST'])
@rate_limit(max_requests=5, window_seconds=300)  # 5 attempts per 5 minutes
def chat_verify_pin():
    """Verify the 4-digit secret PIN for a returning user."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    pin = data.get('pin', '').strip()

    # Validate inputs
    valid_mobile, mobile_result = validate_mobile(mobile)
    if not valid_mobile:
        return jsonify({'success': False, 'message': mobile_result}), 400
    mobile = mobile_result
    
    valid_pin, pin_result = validate_pin(pin)
    if not valid_pin:
        return jsonify({'success': False, 'message': pin_result}), 400
    pin = pin_result

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no, card_url, secret_pin FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
    finally:
        conn.close()
    if not stat or not stat.get('secret_pin'):
        return jsonify({'success': False, 'message': 'No PIN found for this mobile.'}), 404

    # SECURITY: Verify hashed PIN
    if not verify_pin(pin, stat['secret_pin']):
        return jsonify({'success': False, 'message': 'Invalid PIN. Please try again.'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, photo_url FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            gen_doc = cur.fetchone()
    finally:
        conn.close()
    return jsonify({
        'success': True,
        'has_card': True,
        'epic_no': stat.get('epic_no', ''),
        'card_url': stat.get('card_url', ''),
        'voter_name': (gen_doc.get('name', '') if gen_doc else ''),
        'photo_url': (gen_doc.get('photo_url', '') if gen_doc else '')
    })


@app.route('/api/chat/forgot-pin', methods=['POST'])
def chat_forgot_pin():
    """Send voice OTP for PIN reset."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    # Verify this mobile actually has a card
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
    finally:
        conn.close()
    if not stat:
        return jsonify({'success': False, 'message': 'No account found for this mobile.'}), 404

    # Rate limit: max 1 OTP per mobile per 60 seconds
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM otp_sessions WHERE mobile = %s", (mobile,))
            existing = cur.fetchone()
    finally:
        conn.close()
    if existing:
        try:
            created = existing['created_at']
            if isinstance(created, str):
                created = datetime.fromisoformat(created)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - created).total_seconds()
            if elapsed < 60:
                wait = int(60 - elapsed)
                return jsonify({'success': False, 'message': f'Please wait {wait}s before requesting another OTP.'}), 429
        except Exception:
            pass

    # SECURITY FIX: Use cryptographically secure random for OTP
    otp = str(secrets.randbelow(900000) + 100000)

    # Send OTP via SMS FIRST, only store in DB if sent successfully
    otp_sent = False
    sms_api_key = os.getenv('SMS_API_KEY', '')
    if sms_api_key:
        try:
            resp = http_requests.get(
                f'https://2factor.in/API/V1/{sms_api_key}/SMS/{mobile}/{otp}',
                timeout=15
            )
            if resp.status_code == 200 and resp.json().get('Status') == 'Success':
                otp_sent = True
                logger.info(f"PIN reset OTP sent to {mobile[:2]}****{mobile[-2:]}")
        except Exception as e:
            logger.warning(f"PIN reset OTP send failed for {mobile[:2]}****{mobile[-2:]}: {e}")

    if not otp_sent:
        return jsonify({'success': False, 'message': 'Could not send OTP. Please try again.'}), 500

    # Store OTP in DB only after SMS was sent successfully
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO otp_sessions (mobile, otp, created_at, verified, purpose)
                   VALUES (%s, %s, %s, 0, 'pin_reset')
                   ON DUPLICATE KEY UPDATE otp=VALUES(otp), created_at=VALUES(created_at), verified=0, purpose='pin_reset'""",
                (mobile, otp, now)
            )
    finally:
        conn.close()

    return jsonify({'success': True})


@app.route('/api/chat/reset-pin', methods=['POST'])
@rate_limit(max_requests=3, window_seconds=300)
def chat_reset_pin():
    """Verify OTP and save user-chosen new PIN."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    otp = data.get('otp', '').strip()

    # Validate inputs
    valid_mobile, mobile_result = validate_mobile(mobile)
    if not valid_mobile:
        return jsonify({'success': False, 'message': mobile_result}), 400
    mobile = mobile_result
    
    valid_otp, otp_result = validate_otp(otp)
    if not valid_otp:
        return jsonify({'success': False, 'message': otp_result}), 400
    otp = otp_result

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT otp, created_at FROM otp_sessions WHERE mobile = %s", (mobile,))
            doc = cur.fetchone()
    finally:
        conn.close()
    if not doc or doc.get('otp') != otp:
        return jsonify({'success': False, 'message': 'Invalid OTP'}), 400

    # Check expiry (5 minutes)
    try:
        created = doc['created_at']
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            return jsonify({'success': False, 'message': 'OTP expired. Please request a new one.'}), 400
    except Exception:
        pass

    # Accept user-chosen PIN
    new_pin = data.get('new_pin', '').strip()
    valid_pin, pin_result = validate_pin(new_pin)
    if not valid_pin:
        return jsonify({'success': False, 'message': pin_result}), 400
    new_pin = pin_result

    # SECURITY: Hash PIN before saving
    hashed_pin = hash_pin(new_pin)
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE generation_stats SET secret_pin = %s WHERE auth_mobile = %s", (hashed_pin, mobile))
            cur.execute("UPDATE generated_voters SET secret_pin = %s WHERE MOBILE_NO = %s", (hashed_pin, mobile))
            # Clean up OTP
            cur.execute("DELETE FROM otp_sessions WHERE mobile = %s", (mobile,))
            # Get card info to return
            cur.execute("SELECT epic_no, card_url FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
            cur.execute("SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, photo_url FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            gen_doc = cur.fetchone()
    finally:
        conn.close()

    logger.info(f"PIN reset successful for {mobile}")

    return jsonify({
        'success': True,
        'has_card': True,
        'epic_no': stat.get('epic_no', '') if stat else '',
        'card_url': stat.get('card_url', '') if stat else '',
        'voter_name': (gen_doc.get('name', '') if gen_doc else ''),
        'photo_url': (gen_doc.get('photo_url', '') if gen_doc else '')
    })


@app.route('/api/chat/set-pin', methods=['POST'])
@rate_limit(max_requests=3, window_seconds=300)
def chat_set_pin():
    """Set the 4-digit PIN for a user during registration or after card exists."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    pin = data.get('pin', '').strip()
    epic_no = data.get('epic_no', '').strip().upper()

    # Validate inputs
    valid_mobile, mobile_result = validate_mobile(mobile)
    if not valid_mobile:
        return jsonify({'success': False, 'message': mobile_result}), 400
    mobile = mobile_result
    
    valid_pin, pin_result = validate_pin(pin)
    if not valid_pin:
        return jsonify({'success': False, 'message': pin_result}), 400
    pin = pin_result

    if epic_no:
        valid_epic, epic_result = validate_epic(epic_no)
        if not valid_epic:
            return jsonify({'success': False, 'message': epic_result}), 400
        epic_no = epic_result

    # SECURITY: Hash PIN before saving
    hashed_pin = hash_pin(pin)

    # Upsert into generation_stats so PIN is saved even before card generation
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            if epic_no:
                cur.execute(
                    "INSERT INTO generation_stats (epic_no, secret_pin, auth_mobile) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE secret_pin = VALUES(secret_pin), auth_mobile = VALUES(auth_mobile)",
                    (epic_no, hashed_pin, mobile)
                )
            else:
                cur.execute(
                    "UPDATE generation_stats SET secret_pin = %s WHERE auth_mobile = %s",
                    (hashed_pin, mobile)
                )
            cur.execute("UPDATE generated_voters SET secret_pin = %s WHERE MOBILE_NO = %s", (hashed_pin, mobile))
    finally:
        conn.close()

    logger.info(f"PIN set for {mobile}")
    return jsonify({'success': True})


@app.route('/api/chat/verify-forgot-otp', methods=['POST'])
def chat_verify_forgot_otp():
    """Verify the OTP sent for forgot-PIN flow (does NOT delete OTP or reset PIN)."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    otp = data.get('otp', '').strip()

    if not mobile or not otp:
        return jsonify({'success': False, 'message': 'Mobile and OTP required'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT otp, created_at FROM otp_sessions WHERE mobile = %s", (mobile,))
            doc = cur.fetchone()
    finally:
        conn.close()
    if not doc or doc.get('otp') != otp:
        return jsonify({'success': False, 'message': 'Invalid OTP'}), 400

    try:
        created = doc['created_at']
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            return jsonify({'success': False, 'message': 'OTP expired. Please request a new one.'}), 400
    except Exception:
        pass

    return jsonify({'success': True})


@app.route('/api/chat/validate-epic', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=60)
def chat_validate_epic():
    """Validate EPIC number and return voter details."""
    data = request.get_json()
    epic_no = data.get('epic_no', '').strip().upper()
    
    # Validate EPIC format
    valid, result = validate_epic(epic_no)
    if not valid:
        return jsonify({'success': False, 'message': result}), 400
    epic_no = result

    voter = find_voter_by_epic(epic_no)
    if not voter:
        return jsonify({'success': False, 'message': 'EPIC Number not found. Please check and try again.'}), 404

    display_fields = {}
    for key, val in voter.items():
        if key.startswith('_'):
            continue
        display_fields[key] = str(val) if val else ''

    return jsonify({'success': True, 'voter': display_fields})


@app.route('/api/chat/generate-card', methods=['POST'])
@limiter.limit("5 per 5 minutes")  # PHASE 1: Use Redis-based rate limiter
def chat_generate_card():
    """Upload photo and generate ID card via chatbot — synchronous."""
    epic_no = request.form.get('epic_no', '').strip().upper()
    mobile_num = request.form.get('mobile', '').strip()

    # Validate EPIC
    valid_epic, epic_result = validate_epic(epic_no)
    if not valid_epic:
        return jsonify({'success': False, 'message': epic_result}), 400
    epic_no = epic_result

    voter = find_voter_by_epic(epic_no)
    if not voter:
        return jsonify({'success': False, 'message': 'EPIC Number not found.'}), 404

    # Photo is required
    if 'photo' not in request.files or not request.files['photo'].filename:
        return jsonify({'success': False, 'message': 'Photo is required.'}), 400

    file = request.files['photo']
    
    # SECURITY: Validate file upload
    valid_file, file_error = validate_file_upload(file, ALLOWED_IMG, max_size_mb=10)
    if not valid_file:
        return jsonify({'success': False, 'message': file_error}), 400

    try:
        # FACE DETECTION: Validate photo contains a clear face
        is_valid_face, face_message, photo_image = validate_photo_for_id_card(file.stream)
        
        if not is_valid_face:
            logger.warning(f"Face detection failed for {epic_no}: {face_message}")
            return jsonify({
                'success': False, 
                'message': face_message,
                'error_type': 'face_detection_failed'
            }), 400
        
        logger.info(f"Face detected successfully for {epic_no}")
        
        # Upload photo to Cloudinary
        photo_buffer = io.BytesIO()
        photo_image.save(photo_buffer, format='JPEG', quality=95)
        photo_buffer.seek(0)
        photo_data = photo_buffer.getvalue()

        photo_url = ''
        try:
            upload_result = cloudinary.uploader.upload(
                photo_data,
                folder='member_photos',
                public_id=epic_no,
                overwrite=True,
                resource_type='image'
            )
            photo_url = upload_result['secure_url']
            logger.info(f"Photo uploaded for {epic_no}: {photo_url}")
        except Exception as e:
            logger.error(f"Photo upload failed for {epic_no}: {e}")

        # Generate unique PTC code and set verify URL before card generation
        ptc_code = generate_ptc_code()
        voter['ptc_code'] = ptc_code
        voter['verify_url'] = f"{config.BASE_URL}/verify/{epic_no}"

        # Generate card image
        template = Image.open(config.TEMPLATE_PATH)
        card_image = generate_card(voter, template, photo_image)

        # Upload card to Cloudinary
        card_buffer = io.BytesIO()
        card_image.save(card_buffer, format='JPEG', quality=95)
        card_buffer.seek(0)

        card_upload = cloudinary.uploader.upload(
            card_buffer.getvalue(),
            folder='generated_cards',
            public_id=epic_no,
            overwrite=True,
            resource_type='image'
        )
        card_url = card_upload['secure_url']
        logger.info(f"Card generated for {epic_no}: {card_url}")

        # Get referral info
        ref_ptc = request.form.get('ref_ptc', '').strip()
        ref_rid = request.form.get('ref_rid', '').strip()

        # Save to generated voters collection
        from security_fixes import hash_pin

        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                # Upsert generated voter
                cur.execute(
                    "INSERT INTO generated_voters (ptc_code, "
                    "AC_NO, ASSEMBLY_NAME, PART_NO, SECTION_NO, SLNOINPART, "
                    "C_HOUSE_NO, C_HOUSE_NO_V1, "
                    "FM_NAME_EN, LASTNAME_EN, FM_NAME_V1, LASTNAME_V1, "
                    "RLN_TYPE, RLN_FM_NM_EN, RLN_L_NM_EN, RLN_FM_NM_V1, RLN_L_NM_V1, "
                    "EPIC_NO, GENDER, AGE, DOB, MOBILE_NO, ORG_LIST_NO, DISTRICT_NAME, "
                    "photo_url, card_url, generated_at, referred_by_ptc, referred_by_referral_id, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE ptc_code=VALUES(ptc_code), "
                    "AC_NO=VALUES(AC_NO), ASSEMBLY_NAME=VALUES(ASSEMBLY_NAME), "
                    "PART_NO=VALUES(PART_NO), "
                    "SECTION_NO=VALUES(SECTION_NO), SLNOINPART=VALUES(SLNOINPART), "
                    "C_HOUSE_NO=VALUES(C_HOUSE_NO), C_HOUSE_NO_V1=VALUES(C_HOUSE_NO_V1), "
                    "FM_NAME_EN=VALUES(FM_NAME_EN), LASTNAME_EN=VALUES(LASTNAME_EN), "
                    "FM_NAME_V1=VALUES(FM_NAME_V1), LASTNAME_V1=VALUES(LASTNAME_V1), "
                    "RLN_TYPE=VALUES(RLN_TYPE), RLN_FM_NM_EN=VALUES(RLN_FM_NM_EN), "
                    "RLN_L_NM_EN=VALUES(RLN_L_NM_EN), RLN_FM_NM_V1=VALUES(RLN_FM_NM_V1), "
                    "RLN_L_NM_V1=VALUES(RLN_L_NM_V1), "
                    "GENDER=VALUES(GENDER), AGE=VALUES(AGE), DOB=VALUES(DOB), "
                    "ORG_LIST_NO=VALUES(ORG_LIST_NO), DISTRICT_NAME=VALUES(DISTRICT_NAME), "
                    "photo_url=VALUES(photo_url), card_url=VALUES(card_url), "
                    "generated_at=VALUES(generated_at), referred_by_ptc=VALUES(referred_by_ptc), "
                    "referred_by_referral_id=VALUES(referred_by_referral_id)",
                    (ptc_code,
                     voter.get('AC_NO'), voter.get('ASSEMBLY_NAME'),
                     voter.get('PART_NO'),
                     voter.get('SECTION_NO'), voter.get('SLNOINPART'),
                     voter.get('C_HOUSE_NO'), voter.get('C_HOUSE_NO_V1'),
                     voter.get('FM_NAME_EN', ''), voter.get('LASTNAME_EN', ''),
                     voter.get('FM_NAME_V1', ''), voter.get('LASTNAME_V1', ''),
                     voter.get('RLN_TYPE', ''),
                     voter.get('RLN_FM_NM_EN', ''), voter.get('RLN_L_NM_EN', ''),
                     voter.get('RLN_FM_NM_V1', ''), voter.get('RLN_L_NM_V1', ''),
                     epic_no, voter.get('GENDER', ''), voter.get('AGE'),
                     voter.get('DOB', ''), mobile_num, voter.get('ORG_LIST_NO'),
                     voter.get('DISTRICT_NAME'),
                     photo_url, card_url, now_str,
                     ref_ptc or None, ref_rid or None, now_str)
                )

                # Increment referrer count
                if ref_ptc:
                    cur.execute(
                        "UPDATE generated_voters SET referred_members_count = referred_members_count + 1 "
                        "WHERE ptc_code = %s", (ref_ptc,)
                    )

                # Update stats
                cur.execute(
                    "INSERT INTO generation_stats (epic_no, card_url, photo_url, last_generated, auth_mobile, count) "
                    "VALUES (%s, %s, %s, %s, %s, 1) "
                    "ON DUPLICATE KEY UPDATE card_url=VALUES(card_url), photo_url=VALUES(photo_url), "
                    "last_generated=VALUES(last_generated), auth_mobile=VALUES(auth_mobile), count=count+1",
                    (epic_no, card_url, photo_url, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), mobile_num)
                )

                # Mark mobile as verified
                cur.execute(
                    "INSERT INTO verified_mobiles (mobile, epic_no, verified_at) "
                    "VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE epic_no=VALUES(epic_no), verified_at=VALUES(verified_at)",
                    (mobile_num, epic_no, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
                )
        finally:
            conn.close()

        logger.info(f"Card generation completed for {epic_no}")
        
        return jsonify({
            'success': True,
            'card_url': card_url,
            'photo_url': photo_url,
            'epic_no': epic_no,
            'voter_name': voter.get('name', ''),
            'message': 'Card generated successfully'
        })
        
    except Exception as e:
        logger.error(f"Card generation error for {epic_no}: {e}")
        return jsonify({'success': False, 'message': 'Card generation failed. Please try again.'}), 500


@app.route('/api/chat/card-status/<job_id>')
def check_card_status(job_id):
    """Check status of async card generation job."""
    try:
        from celery.result import AsyncResult
        task = AsyncResult(job_id, app=celery)
        
        if task.state == 'PENDING':
            response = {
                'status': 'pending',
                'message': 'Job is waiting to be processed'
            }
        elif task.state == 'PROCESSING':
            response = {
                'status': 'processing',
                'message': task.info.get('status', 'Processing...') if task.info else 'Processing...'
            }
        elif task.state == 'SUCCESS':
            result = task.result
            response = {
                'status': 'completed',
                'success': result.get('success', False),
                'card_url': result.get('card_url', ''),
                'photo_url': result.get('photo_url', ''),
                'epic_no': result.get('epic_no', ''),
                'voter_name': result.get('voter_name', ''),
                'message': result.get('message', 'Card generated successfully')
            }
            
            # Mark mobile as verified after successful generation
            if result.get('success') and result.get('epic_no'):
                mobile = session.get('verified_mobile')
                if mobile:
                    conn = _get_mysql()
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO verified_mobiles (mobile, epic_no, verified_at) "
                                "VALUES (%s, %s, %s) "
                                "ON DUPLICATE KEY UPDATE epic_no=VALUES(epic_no), verified_at=VALUES(verified_at)",
                                (mobile, result['epic_no'], datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
                            )
                    finally:
                        conn.close()
        elif task.state == 'FAILURE':
            response = {
                'status': 'failed',
                'message': str(task.info) if task.info else 'Card generation failed'
            }
        else:
            response = {
                'status': task.state.lower(),
                'message': f'Job status: {task.state}'
            }
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error checking job status {job_id}: {e}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to check job status'
        }), 500


@app.route('/card/<epic_no>')
def user_card_page(epic_no):
    """GET page showing generated card - safe to refresh (no re-generation)."""
    epic_no = epic_no.strip().upper()
    voter = find_voter_by_epic(epic_no)
    if not voter:
        flash('Voter not found.', 'danger')
        return redirect(url_for('user_home'))

    card_url = get_voter_card_url(epic_no)
    if not card_url:
        flash('Card not generated yet. Please generate first.', 'warning')
        return redirect(url_for('user_home'))

    gen_count = get_voter_gen_count(epic_no)
    return render_template('user/card.html',
                           epic_no=epic_no,
                           voter=voter,
                           gen_count=gen_count,
                           card_url=card_url)


@app.route('/mycard/<epic_no>')
def user_serve_card(epic_no):
    """Redirect to the Cloudinary-hosted card image - REQUIRES AUTHORIZATION."""
    # SECURITY FIX: Verify user owns this card
    mobile = session.get('verified_mobile')
    if not mobile:
        return jsonify({'error': 'Unauthorized. Please verify your mobile number first.'}), 401
    
    # Verify this mobile generated this card
    epic_no = epic_no.strip().upper()
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM generated_voters WHERE EPIC_NO = %s AND MOBILE_NO = %s", (epic_no, mobile))
            gen_doc = cur.fetchone()
    finally:
        conn.close()
    if not gen_doc:
        return jsonify({'error': 'Forbidden. You do not have access to this card.'}), 403
    
    card_url = get_voter_card_url(epic_no)
    if card_url:
        return redirect(card_url)
    return jsonify({'error': 'Card not found.'}), 404


@app.route('/mycard/<epic_no>/download')
def user_download_card(epic_no):
    """Redirect to Cloudinary card with download flag - REQUIRES AUTHORIZATION."""
    # SECURITY FIX: Verify user owns this card
    mobile = session.get('verified_mobile')
    if not mobile:
        return jsonify({'error': 'Unauthorized. Please verify your mobile number first.'}), 401
    
    # Verify this mobile generated this card
    epic_no = epic_no.strip().upper()
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM generated_voters WHERE EPIC_NO = %s AND MOBILE_NO = %s", (epic_no, mobile))
            gen_doc = cur.fetchone()
    finally:
        conn.close()
    if not gen_doc:
        return jsonify({'error': 'Forbidden. You do not have access to this card.'}), 403
    
    card_url = get_voter_card_url(epic_no)
    if card_url:
        # Add Cloudinary fl_attachment transformation for download
        if '/upload/' in card_url:
            dl_url = card_url.replace('/upload/', f'/upload/fl_attachment:{epic_no}_VoterID/')
        else:
            dl_url = card_url
        return redirect(dl_url)
    return jsonify({'error': 'Card not found.'}), 404


@app.route('/verify/<epic_no>')
def verify_voter(epic_no):
    """Public verification page - opened when QR code is scanned on mobile."""
    epic_no = epic_no.strip().upper()
    voter = find_voter_by_epic(epic_no)
    if not voter:
        flash('Voter ID not found in database.', 'danger')
        return redirect(url_for('user_home'))

    # Attach stats (exclude secret_pin — never expose in QR/verify page)
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no, count, last_generated, photo_url, card_url, auth_mobile FROM generation_stats WHERE epic_no = %s", (epic_no,))
            s = cur.fetchone() or {}
            cur.execute("SELECT ptc_code FROM generated_voters WHERE EPIC_NO = %s LIMIT 1", (epic_no,))
            gen_doc = cur.fetchone()

            # Volunteer request status
            cur.execute("SELECT status, requested_at FROM volunteer_requests WHERE epic_no = %s ORDER BY requested_at DESC LIMIT 1", (epic_no,))
            vol_req = cur.fetchone()

            # Booth agent request status
            cur.execute("SELECT status, requested_at FROM booth_agent_requests WHERE epic_no = %s ORDER BY requested_at DESC LIMIT 1", (epic_no,))
            ba_req = cur.fetchone()
    finally:
        conn.close()
    voter['gen_count'] = s.get('count', 0)
    voter['last_generated'] = s.get('last_generated', '')
    voter['photo_url'] = s.get('photo_url', '')
    voter['card_url'] = s.get('card_url', '')
    
    # SECURITY FIX: Don't expose full mobile number publicly - show last 4 digits only
    mobile = s.get('auth_mobile', '')
    if mobile and len(mobile) >= 4:
        voter['auth_mobile_masked'] = f"****{mobile[-4:]}"
    else:
        voter['auth_mobile_masked'] = ''

    # Attach PTC code from generated voters DB
    voter['ptc_code'] = gen_doc.get('ptc_code', '') if gen_doc else ''

    # Attach volunteer and booth agent request status
    voter['volunteer_status'] = vol_req.get('status', '') if vol_req else ''
    voter['volunteer_requested_at'] = str(vol_req.get('requested_at', '')) if vol_req else ''
    voter['booth_agent_status'] = ba_req.get('status', '') if ba_req else ''
    voter['booth_agent_requested_at'] = str(ba_req.get('requested_at', '')) if ba_req else ''

    # Separate core fields from extra fields
    core_keys = {'epic_no', 'name', 'assembly', 'age', 'sex', 'relation_type',
                 'relation_name', 'mobile', 'part_no', 'dob', 'section_no',
                 'slno_in_part', 'house_no', 'name_v1', 'relation_name_v1',
                 'house_no_v1', 'org_list_no', 'id', 'gen_count',
                 'last_generated', 'photo_url', 'card_url', 'serial_number',
                 'verify_url', 'auth_mobile', 'ptc_code',
                 'auth_mobile_masked',
                 'volunteer_status', 'volunteer_requested_at',
                 'booth_agent_status', 'booth_agent_requested_at',
                 'AC_NO','PART_NO','SECTION_NO','SLNOINPART','C_HOUSE_NO','C_HOUSE_NO_V1',
                 'FM_NAME_EN','LASTNAME_EN','FM_NAME_V1','LASTNAME_V1','RLN_TYPE',
                 'RLN_FM_NM_EN','RLN_L_NM_EN','RLN_FM_NM_V1','RLN_L_NM_V1',
                 'EPIC_NO','GENDER','AGE','DOB','MOBILE_NO','ORG_LIST_NO','DISTRICT_ID','DISTRICT_NAME',
                 'district'}
    extra_fields = {k: v for k, v in voter.items() if k not in core_keys and v}

    return render_template('user/verify.html',
                           voter=voter,
                           extra_fields=extra_fields)


# ── Referral Landing Page ────────────────────────────────────────

@app.route('/refer/<ptc_code>/<referral_id>')
def referral_landing(ptc_code, referral_id):
    """Serve OG-tagged page for WhatsApp preview, then redirect to chatbot."""
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name FROM generated_voters WHERE ptc_code = %s AND referral_id = %s", (ptc_code, referral_id))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter:
        flash('Invalid referral link.', 'danger')
        return redirect(url_for('user_home'))

    referrer_name = voter.get('name', 'A PuratchiThaai Member')
    redirect_url = url_for('user_home') + f'?ref={ptc_code}&rid={referral_id}'
    banner_url = f"{config.BASE_URL}/static/banner.jpg"

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta property="og:title" content="PuratchiThaai — Become a Member!">
<meta property="og:description" content="{referrer_name} invites you to join PuratchiThaai! Generate your free Digital Member ID Card now and become a proud member.">
<meta property="og:image" content="{banner_url}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:type" content="website">
<meta property="og:url" content="{config.BASE_URL}/refer/{ptc_code}/{referral_id}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="PuratchiThaai — Become a Member!">
<meta name="twitter:description" content="{referrer_name} invites you to join PuratchiThaai! Generate your free Digital Member ID Card now.">
<meta name="twitter:image" content="{banner_url}">
<meta http-equiv="refresh" content="0;url={redirect_url}">
<title>PuratchiThaai — Join Now!</title>
</head><body style="font-family:sans-serif;text-align:center;padding:40px;">
<p>Redirecting to PuratchiThaai...</p>
<script>window.location.href="{redirect_url}";</script>
</body></html>"""
    return html


# ── New Chatbot API Endpoints ────────────────────────────────────

@app.route('/api/chat/profile', methods=['POST'])
def chat_profile():
    """Return voter profile details for sidebar profile view."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            gen_doc = cur.fetchone()
    finally:
        conn.close()
    if not gen_doc:
        return jsonify({'success': False, 'message': 'Profile not found.'}), 404

    gen_doc = _translate_gen_row(gen_doc)

    # Remove secret_pin from gen_doc
    gen_doc.pop('secret_pin', None)

    # Also fetch original voter data from MySQL
    voter_doc = None
    epic = gen_doc.get('epic_no', '')
    if epic:
        voter_doc = find_voter_by_epic(epic)

    # Merge: gen_doc fields take priority, but include extra voter_doc fields
    profile = {}
    if voter_doc:
        profile.update(voter_doc)
    profile.update({k: v for k, v in gen_doc.items() if v})

    # Remove sensitive fields
    profile.pop('secret_pin', None)
    profile.pop('_id', None)

    return jsonify({'success': True, 'profile': profile})


@app.route('/api/chat/booth', methods=['POST'])
def chat_booth():
    """Return booth / polling station info for navigation.
    Auto-detects lat,long and address from any voter field."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            gen_doc = cur.fetchone()
    finally:
        conn.close()
    if not gen_doc:
        return jsonify({'success': False, 'message': 'Profile not found.'}), 404

    gen_doc = _translate_gen_row(gen_doc)
    epic = gen_doc.get('epic_no', '')
    voter_doc = find_voter_by_epic(epic) if epic else None

    # Merge to get all fields
    merged = {}
    if voter_doc:
        merged.update(voter_doc)
    merged.update({k: v for k, v in gen_doc.items() if v})

    # Skip these keys when scanning for booth data
    skip_keys = {'_id', 'epic_no', 'name', 'mobile', 'photo_url', 'card_url',
                 'ptc_code', 'secret_pin', 'referral_id', 'referred_by_ptc',
                 'referred_by_referral_id', 'generated_at', 'referred_members_count'}

    # Regex: lat,long like 13.2296228,79.6741222
    latlong_re = re.compile(
        r'^[-+]?([1-8]?\d(\.\d+)?|90(\.0+)?)\s*,\s*[-+]?(180(\.0+)?|(1[0-7]\d|\d{1,2})(\.\d+)?)$'
    )
    # Address heuristic: contains a 6-digit pincode, or is long text (>30 chars)
    # with words like school, room, road, street, building, village, panchayat, ward, etc.
    addr_keywords = re.compile(
        r'school|room|road|street|building|village|panchayat|ward|nagar|colony|'
        r'block|floor|hall|office|temple|church|mosque|community|union|elementary|'
        r'middle|higher|secondary|primary|facing|north|south|east|west',
        re.IGNORECASE
    )

    detected_latlong = None
    detected_address = None
    latlong_key = None
    address_key = None

    for key, val in merged.items():
        if key in skip_keys or not val:
            continue
        s = str(val).strip()
        if not s:
            continue

        # Check for lat,long pattern
        if not detected_latlong and latlong_re.match(s):
            detected_latlong = s
            latlong_key = key
            continue

        # Check for address-like value
        if not detected_address:
            has_pincode = bool(re.search(r'\b\d{6}\b', s))
            has_keywords = bool(addr_keywords.search(s))
            if (has_pincode or has_keywords) and len(s) > 20:
                detected_address = s
                address_key = key

    # Also check explicit fields from column mapping
    if not detected_address:
        for f in ('polling_station', 'booth_address'):
            if merged.get(f):
                detected_address = merged[f]
                address_key = f
                break

    booth = {
        'latlong': detected_latlong or '',
        'address': detected_address or '',
        'latlong_field': latlong_key or '',
        'address_field': address_key or '',
        'assembly': merged.get('assembly', ''),
        'part_no': merged.get('part_no', ''),
    }

    has_data = bool(detected_latlong or detected_address)
    return jsonify({'success': True, 'booth': booth, 'has_booth_data': has_data})


@app.route('/api/chat/get-referral-link', methods=['POST'])
def chat_get_referral_link():
    """Get or create unique referral link for a voter."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ptc_code FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter or not voter.get('ptc_code'):
        return jsonify({'success': False, 'message': 'Voter not found.'}), 404

    result = get_or_create_referral(voter['ptc_code'])
    if not result:
        return jsonify({'success': False, 'message': 'Could not generate referral link.'}), 500

    return jsonify({
        'success': True,
        'referral_id': result['referral_id'],
        'referral_link': result['referral_link'],
        'ptc_code': voter['ptc_code']
    })


@app.route('/api/chat/my-members', methods=['POST'])
def chat_my_members():
    """Get list of voters referred by this voter."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ptc_code, CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter or not voter.get('ptc_code'):
        return jsonify({'success': False, 'message': 'Voter not found.'}), 404

    ptc_code = voter['ptc_code']
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, "
                "EPIC_NO AS epic_no, CAST(AC_NO AS CHAR) AS assembly, ptc_code, generated_at, MOBILE_NO AS mobile "
                "FROM generated_voters WHERE referred_by_ptc = %s ORDER BY generated_at DESC",
                (ptc_code,)
            )
            members = cur.fetchall()
    finally:
        conn.close()

    return jsonify({
        'success': True,
        'referrer_name': voter.get('name', ''),
        'referrer_ptc': ptc_code,
        'total_referred': len(members),
        'members': members
    })


@app.route('/api/chat/request-volunteer', methods=['POST'])
def chat_request_volunteer():
    """Submit a volunteer request."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ptc_code, EPIC_NO AS epic_no, "
                "CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, "
                "COALESCE(ASSEMBLY_NAME, CAST(AC_NO AS CHAR)) AS assembly, "
                "DISTRICT_NAME AS district, photo_url "
                "FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter or not voter.get('ptc_code'):
        return jsonify({'success': False, 'message': 'Voter not found.'}), 404

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM volunteer_requests WHERE ptc_code = %s", (voter['ptc_code'],))
            existing = cur.fetchone()
    finally:
        conn.close()
    if existing:
        return jsonify({
            'success': True,
            'already_requested': True,
            'status': existing.get('status', 'pending'),
            'message': 'You have already submitted a volunteer request.'
        })

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO volunteer_requests (ptc_code, epic_no, name, mobile, assembly, photo_url, status, requested_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (voter['ptc_code'], voter.get('epic_no', ''), voter.get('name', ''),
                 mobile, voter.get('assembly', ''), voter.get('photo_url', ''),
                 'pending', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            )
    finally:
        conn.close()
    return jsonify({'success': True, 'already_requested': False, 'message': 'Volunteer request submitted.'})


@app.route('/api/chat/request-booth-agent', methods=['POST'])
def chat_request_booth_agent():
    """Submit a booth agent request."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ptc_code, EPIC_NO AS epic_no, "
                "CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, "
                "COALESCE(ASSEMBLY_NAME, CAST(AC_NO AS CHAR)) AS assembly, "
                "DISTRICT_NAME AS district, photo_url "
                "FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter or not voter.get('ptc_code'):
        return jsonify({'success': False, 'message': 'Voter not found.'}), 404

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM booth_agent_requests WHERE ptc_code = %s", (voter['ptc_code'],))
            existing = cur.fetchone()
    finally:
        conn.close()
    if existing:
        return jsonify({
            'success': True,
            'already_requested': True,
            'status': existing.get('status', 'pending'),
            'message': 'You have already submitted a booth agent request.'
        })

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO booth_agent_requests (ptc_code, epic_no, name, mobile, assembly, photo_url, status, requested_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (voter['ptc_code'], voter.get('epic_no', ''), voter.get('name', ''),
                 mobile, voter.get('assembly', ''), voter.get('photo_url', ''),
                 'pending', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            )
    finally:
        conn.close()
    return jsonify({'success': True, 'already_requested': False, 'message': 'Booth agent request submitted.'})


@app.route('/api/whatsapp-channel')
def api_whatsapp_channel():
    """Return WhatsApp channel URL."""
    url = config.WHATSAPP_CHANNEL_URL
    return jsonify({'url': url})


# ══════════════════════════════════════════════════════════════════
#  ADMIN BLUEPRINT  (/admin)
# ══════════════════════════════════════════════════════════════════

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ── Admin Authentication ─────────────────────────────────────────
@admin_bp.before_request
def require_admin_login():
    """Guard all /admin routes - redirect to login if not authenticated."""
    if request.endpoint == 'admin.login':
        return  # allow access to the login page itself
    if not session.get('admin_logged_in'):
        flash('Please log in to access the admin panel.', 'warning')
        return redirect(url_for('admin.login', next=request.url))


@admin_bp.route('/login', methods=['GET', 'POST'])
@rate_limit(max_requests=5, window_seconds=300)  # 5 login attempts per 5 minutes
def login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        ip = request.remote_addr or 'unknown'
        
        # SECURITY: Check if IP is locked out
        is_locked, retry_after = login_tracker.is_locked(ip)
        if is_locked:
            flash(f'Too many failed attempts. Try again in {retry_after} seconds.', 'danger')
            return render_template('admin/login.html')
        
        # Verify credentials
        if username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD:
            # SECURITY FIX: Regenerate session on login to prevent session fixation
            session.clear()
            session['admin_logged_in'] = True
            session.permanent = True
            login_tracker.reset(ip)  # Reset on successful login
            flash('Welcome back, Admin!', 'success')
            next_url = request.args.get('next') or url_for('admin.dashboard')
            return redirect(next_url)
        else:
            # SECURITY: Record failed attempt
            login_tracker.record_attempt(ip, username, success=False)
            flash('Invalid username or password.', 'danger')
    
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    flash('Logged out successfully.', 'info')
    return redirect(url_for('admin.login'))


@admin_bp.route('/')
def dashboard():
    stats = get_dashboard_stats()
    return render_template('admin/dashboard.html', stats=stats)


@admin_bp.route('/voters')
@rate_limit(max_requests=30, window_seconds=60)
def voters_list():
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    # Only fetch lightweight data for the template shell
    # Actual voter data is loaded via AJAX from /api/voters
    assemblies, districts = _get_cached_dropdowns('mysql_voters', REDIS_DROPDOWN_VOTERS_KEY)
    try:
        total = _mysql_count()
    except Exception:
        total = 0

    return render_template('admin/voters.html',
                           voters=[], page=1,
                           total_pages=1, total=total,
                           per_page=per_page, search='',
                           assemblies=assemblies, districts=districts,
                           filter_assembly='',
                           filter_district='')


@admin_bp.route('/api/stats')
def api_stats():
    return jsonify(get_dashboard_stats())


@admin_bp.route('/api/external-stats')
def api_external_stats():
    """Separate endpoint for slow external stats - loaded via AJAX."""
    return jsonify(_get_external_stats())


@admin_bp.route('/voters/<epic_no>')
def voter_detail(epic_no):
    """Show full details for a single voter."""
    voter = find_voter_by_epic(epic_no)
    if not voter:
        flash('Voter not found.', 'danger')
        return redirect(url_for('admin.voters_list'))

    # Attach stats
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM generation_stats WHERE epic_no = %s", (epic_no,))
            s = cur.fetchone() or {}
    finally:
        conn.close()
    voter['gen_count'] = s.get('count', 0)
    voter['last_generated'] = s.get('last_generated', '')
    voter['photo_url'] = s.get('photo_url', '')
    voter['card_url'] = s.get('card_url', '')
    voter['auth_mobile'] = s.get('auth_mobile', '')

    # Separate core fields from extra fields for display
    core_keys = {'epic_no', 'name', 'assembly', 'age', 'sex', 'relation_type',
                 'relation_name', 'mobile', 'part_no', 'dob', 'section_no',
                 'slno_in_part', 'house_no', 'name_v1', 'relation_name_v1',
                 'house_no_v1', 'org_list_no', 'id', 'gen_count',
                 'last_generated', 'photo_url', 'card_url', 'auth_mobile',
                 'AC_NO','ASSEMBLY_NAME','PART_NO','SECTION_NO','SLNOINPART','C_HOUSE_NO','C_HOUSE_NO_V1',
                 'FM_NAME_EN','LASTNAME_EN','FM_NAME_V1','LASTNAME_V1','RLN_TYPE',
                 'RLN_FM_NM_EN','RLN_L_NM_EN','RLN_FM_NM_V1','RLN_L_NM_V1',
                 'EPIC_NO','GENDER','AGE','DOB','MOBILE_NO','ORG_LIST_NO','DISTRICT_ID','DISTRICT_NAME',
                 'district', 'district_id', 'assembly_name'}
    extra_fields = {k: v for k, v in voter.items() if k not in core_keys}

    return render_template('admin/voter_detail.html',
                           voter=voter, extra_fields=extra_fields)

@admin_bp.route('/api/voters')
@rate_limit(max_requests=30, window_seconds=60)
def api_voters():
    """JSON API: search, filter, paginate voters from MySQL (multi-table).
    Optimized: uses indexed LIKE + UNION ALL across tables."""
    search = sanitize_search(request.args.get('search', '').strip())  # SECURITY: Sanitize search
    assembly = request.args.get('assembly', '').strip()
    district = request.args.get('district', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    # Determine which tables to query based on filters
    target_tables = _get_voter_tables(assembly=assembly, district=district)

    # Build WHERE clause using LIKE (reliable across all 234 tables)
    where_parts = []
    params = []
    if search:
        like = f"%{search}%"
        # EPIC_NO prefix search uses B-tree idx_epic_no (fast); name/assembly use LIKE
        where_parts.append(
            "(`EPIC_NO` LIKE %s OR `FM_NAME_EN` LIKE %s OR `LASTNAME_EN` LIKE %s OR `ASSEMBLY_NAME` LIKE %s)"
        )
        params.extend([like, like, like, like])

    where_clause = " AND ".join(where_parts) if where_parts else ""

    # For search queries: use UNION ALL across tables (single round-trip per batch)
    if search and target_tables:
        conn = _get_voters_mysql()
        try:
            with conn.cursor() as cur:
                # Count + fetch in batches to avoid oversized SQL
                total = 0
                batch_size = 30
                for i in range(0, len(target_tables), batch_size):
                    batch = target_tables[i:i + batch_size]
                    count_unions = []
                    batch_params = []
                    for tbl in batch:
                        count_unions.append(f"(SELECT COUNT(*) AS cnt FROM `{tbl}` WHERE {where_clause})")
                        batch_params.extend(params)
                    count_sql = "SELECT SUM(cnt) AS total FROM (" + " UNION ALL ".join(count_unions) + ") AS t"
                    cur.execute(count_sql, tuple(batch_params))
                    total += int(cur.fetchone()['total'] or 0)

                # Paginate
                total_pages = max(1, (total + per_page - 1) // per_page)
                page = min(page, total_pages)
                offset = (page - 1) * per_page

                # Fetch rows using UNION ALL with LIMIT (batched)
                voters = []
                remaining_offset = offset
                remaining_limit = per_page
                for i in range(0, len(target_tables), batch_size):
                    if remaining_limit <= 0:
                        break
                    batch = target_tables[i:i + batch_size]
                    data_unions = []
                    data_params = []
                    for tbl in batch:
                        data_unions.append(f"(SELECT * FROM `{tbl}` WHERE {where_clause})")
                        data_params.extend(params)
                    data_sql = " UNION ALL ".join(data_unions) + f" LIMIT %s OFFSET %s"
                    data_params.extend([remaining_limit + remaining_offset, 0])
                    cur.execute(data_sql, tuple(data_params))
                    rows = cur.fetchall()
                    if remaining_offset >= len(rows):
                        remaining_offset -= len(rows)
                        continue
                    sliced = rows[remaining_offset:remaining_offset + remaining_limit]
                    voters.extend(_translate_voter_row(r) for r in sliced)
                    remaining_offset = 0
                    remaining_limit -= len(sliced)
        finally:
            conn.close()
    elif not search:
        # No search: use pre-computed totals (instant)
        total = _mysql_count(where_clause, tuple(params), tables=target_tables)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        offset = (page - 1) * per_page

        conn = _get_voters_mysql()
        try:
            voters = []
            _tbl_totals = {r['table_name']: int(r.get('total_voters') or 0) for r in _ASSEMBLY_TABLES}
            with conn.cursor() as cur:
                remaining_offset = offset
                remaining_limit = per_page
                for tbl in target_tables:
                    if remaining_limit <= 0:
                        break
                    tbl_count = _tbl_totals.get(tbl, 0)
                    if remaining_offset >= tbl_count:
                        remaining_offset -= tbl_count
                        continue
                    sql = f"SELECT * FROM `{tbl}` ORDER BY `id` ASC LIMIT %s OFFSET %s"
                    cur.execute(sql, (remaining_limit, remaining_offset))
                    rows = cur.fetchall()
                    voters.extend(_translate_voter_row(r) for r in rows)
                    remaining_offset = 0
                    remaining_limit -= len(rows)
        finally:
            conn.close()
    else:
        total = 0
        total_pages = 1
        voters = []

    # Batch-fetch stats for ONLY this page from MySQL
    epic_nos = [v['epic_no'] for v in voters if v.get('epic_no')]
    stats_docs = {}
    if epic_nos:
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                placeholders = ','.join(['%s'] * len(epic_nos))
                cur.execute(f"SELECT * FROM generation_stats WHERE epic_no IN ({placeholders})", epic_nos)
                for row in cur.fetchall():
                    stats_docs[row['epic_no']] = row
        finally:
            conn.close()

    for v in voters:
        s = stats_docs.get(v.get('epic_no', ''), {})
        v['gen_count'] = s.get('count', 0)
        v['last_generated'] = s.get('last_generated', '')
        v['photo_url'] = s.get('photo_url', '')
        v['card_url'] = s.get('card_url', '')
        v['auth_mobile'] = s.get('auth_mobile', '')

    return jsonify({
        'voters': voters,
        'total': total,
        'per_page': per_page,
        'page': page,
        'total_pages': total_pages,
        'cursor_mode': False,
    })


# ── Generated Voters (from new DB) ──────────────────────────────

@admin_bp.route('/generated-voters')
@rate_limit(max_requests=30, window_seconds=60)
def generated_voters_list():
    """Show all voters who generated ID cards via the chatbot."""
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    # Use estimated row count from table statistics (instant, no full scan)
    try:
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT TABLE_ROWS FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'generated_voters'",
                    (config.MYSQL_DB,)
                )
                row = cur.fetchone()
                total = int(row['TABLE_ROWS']) if row and row['TABLE_ROWS'] else 0
        finally:
            conn.close()
    except Exception:
        total = 0

    return render_template('admin/generated_voters.html',
                           voters=[], page=1,
                           total_pages=1, total=total,
                           per_page=per_page, search='')


@admin_bp.route('/api/generated-voters')
@rate_limit(max_requests=30, window_seconds=60)
def api_generated_voters():
    """JSON API for generated voters list - supports cursor-based & page-based pagination.
    Optimized: uses FULLTEXT search, estimated counts for large result sets."""
    search = sanitize_search(request.args.get('search', '').strip())  # SECURITY: Sanitize search
    assembly = request.args.get('assembly', '').strip()
    district = request.args.get('district', '').strip()
    cursor = request.args.get('cursor', '').strip()
    direction = request.args.get('direction', 'next').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    # Cached dropdown options (5 min TTL - distinct() is expensive at scale)
    assemblies, districts = _get_cached_dropdowns('generated_voters', REDIS_DROPDOWN_GEN_KEY)

    # Build MySQL WHERE conditions
    where_parts = []
    params = []
    if assembly:
        where_parts.append("ASSEMBLY_NAME = %s")
        params.append(assembly)
    if search:
        ft_where, ft_params = _build_fulltext_where(
            search, 'EPIC_NO, FM_NAME_EN, LASTNAME_EN, ptc_code, MOBILE_NO'
        )
        if ft_where:
            where_parts.append(ft_where)
            params.extend(ft_params)
    # Keep a LIKE fallback ready in case FULLTEXT index doesn't exist yet
    _use_ft = any('MATCH(' in p for p in where_parts)

    where_clause = " AND ".join(where_parts) if where_parts else ""

    # Server-side count (use estimated count when no filters for speed)
    def _rebuild_like():
        """Rebuild where_clause using LIKE instead of FULLTEXT (fallback)."""
        parts, prms = [], []
        if assembly:
            parts.append("ASSEMBLY_NAME = %s"); prms.append(assembly)
        if search:
            lk_w, lk_p = _like_where_generated(search)
            if lk_w:
                parts.append(lk_w); prms.extend(lk_p)
        return " AND ".join(parts) if parts else "", prms

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            if not where_clause:
                cur.execute(
                    "SELECT TABLE_ROWS FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'generated_voters'",
                    (config.MYSQL_DB,)
                )
                row = cur.fetchone()
                total = int(row['TABLE_ROWS']) if row and row['TABLE_ROWS'] else 0
            else:
                count_sql = f"SELECT COUNT(*) AS cnt FROM generated_voters WHERE {where_clause}"
                try:
                    cur.execute(count_sql, tuple(params))
                except Exception as e:
                    if '1191' in str(e) and _use_ft:
                        where_clause, params = _rebuild_like()
                        count_sql = f"SELECT COUNT(*) AS cnt FROM generated_voters WHERE {where_clause}"
                        cur.execute(count_sql, tuple(params))
                    else:
                        raise
                total = cur.fetchone()['cnt']
    finally:
        conn.close()

    if cursor:
        # ── Cursor-based pagination (using id) ──
        cursor_params = list(params)
        if direction == 'prev':
            cursor_cond = "id > %s"
            order = "ASC"
        else:
            cursor_cond = "id < %s"
            order = "DESC"
        cursor_params.append(int(cursor))

        if where_clause:
            full_where = f"{where_clause} AND {cursor_cond}"
        else:
            full_where = cursor_cond

        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                sql = f"SELECT * FROM generated_voters WHERE {full_where} ORDER BY id {order} LIMIT %s"
                cursor_params.append(per_page)
                try:
                    cur.execute(sql, tuple(cursor_params))
                except Exception as e:
                    if '1191' in str(e) and _use_ft:
                        where_clause, params = _rebuild_like()
                        cursor_params = list(params)
                        cursor_params.append(int(cursor))
                        full_where = f"{where_clause} AND {cursor_cond}" if where_clause else cursor_cond
                        sql = f"SELECT * FROM generated_voters WHERE {full_where} ORDER BY id {order} LIMIT %s"
                        cursor_params.append(per_page)
                        cur.execute(sql, tuple(cursor_params))
                    else:
                        raise
                voters = [_translate_gen_row(r) for r in cur.fetchall()]
        finally:
            conn.close()

        if direction == 'prev':
            voters.reverse()

        next_cursor = str(voters[-1]['id']) if len(voters) == per_page else None
        prev_cursor = str(voters[0]['id']) if voters else None

        return jsonify({
            'voters': voters,
            'total': total,
            'per_page': per_page,
            'next_cursor': next_cursor,
            'prev_cursor': prev_cursor,
            'has_next': len(voters) == per_page,
            'has_prev': bool(cursor),
            'assemblies': assemblies,
            'districts': districts,
            'cursor_mode': True,
        })
    else:
        # ── Page-based (first load / fallback) ──
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        offset = (page - 1) * per_page

        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                sql = "SELECT * FROM generated_voters"
                if where_clause:
                    sql += f" WHERE {where_clause}"
                sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
                try:
                    cur.execute(sql, tuple(params) + (per_page, offset))
                except Exception as e:
                    if '1191' in str(e) and _use_ft:
                        where_clause, params = _rebuild_like()
                        sql = "SELECT * FROM generated_voters"
                        if where_clause:
                            sql += f" WHERE {where_clause}"
                        sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
                        cur.execute(sql, tuple(params) + (per_page, offset))
                    else:
                        raise
                voters = [_translate_gen_row(r) for r in cur.fetchall()]
        finally:
            conn.close()

        next_cursor = str(voters[-1]['id']) if len(voters) == per_page else None
        prev_cursor = None

        return jsonify({
            'voters': voters,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'next_cursor': next_cursor,
            'prev_cursor': prev_cursor,
            'assemblies': assemblies,
            'districts': districts,
            'cursor_mode': False,
        })


# ── Generated Voter Detail ──────────────────────────────────────

@admin_bp.route('/generated-voters/<ptc_code>')
def generated_voter_detail(ptc_code):
    """Show full details for a generated voter including referred voters."""
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM generated_voters WHERE ptc_code = %s", (ptc_code,))
            voter = _translate_gen_row(cur.fetchone())
    finally:
        conn.close()
    if not voter:
        flash('Generated voter not found.', 'danger')
        return redirect(url_for('admin.generated_voters_list'))

    # Get referred voters
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, "
                "EPIC_NO AS epic_no, ptc_code, MOBILE_NO AS mobile, "
                "COALESCE(ASSEMBLY_NAME, CAST(AC_NO AS CHAR)) AS assembly, generated_at, photo_url "
                "FROM generated_voters WHERE referred_by_ptc = %s ORDER BY generated_at DESC",
                (ptc_code,)
            )
            referred = cur.fetchall()

            # Get volunteer request status
            cur.execute("SELECT status, requested_at, reviewed_at FROM volunteer_requests WHERE ptc_code = %s", (ptc_code,))
            volunteer_req = cur.fetchone()

            # Get booth agent request status
            cur.execute("SELECT status, requested_at, reviewed_at FROM booth_agent_requests WHERE ptc_code = %s", (ptc_code,))
            booth_agent_req = cur.fetchone()
    finally:
        conn.close()

    return render_template('admin/generated_voter_detail.html',
                           voter=voter, referred=referred,
                           volunteer_req=volunteer_req,
                           booth_agent_req=booth_agent_req)


# ── Volunteer Requests ───────────────────────────────────────────

@admin_bp.route('/volunteer-requests')
def volunteer_requests_page():
    """Show volunteer requests."""
    return render_template('admin/volunteer_requests.html')


@admin_bp.route('/api/volunteer-requests')
def api_volunteer_requests():
    """JSON API for volunteer requests - server-side search & pagination."""
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    where_parts = []
    params = []
    if status:
        where_parts.append("status = %s")
        params.append(status)
    if search:
        like = f"%{search}%"
        where_parts.append("(name LIKE %s OR ptc_code LIKE %s OR epic_no LIKE %s OR mobile LIKE %s OR assembly LIKE %s OR district LIKE %s)")
        params.extend([like, like, like, like, like, like])
    where_clause = " AND ".join(where_parts) if where_parts else ""

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            count_sql = "SELECT COUNT(*) AS cnt FROM volunteer_requests"
            if where_clause:
                count_sql += f" WHERE {where_clause}"
            cur.execute(count_sql, tuple(params))
            total = cur.fetchone()['cnt']

            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            offset = (page - 1) * per_page

            sql = "SELECT * FROM volunteer_requests"
            if where_clause:
                sql += f" WHERE {where_clause}"
            sql += " ORDER BY requested_at DESC LIMIT %s OFFSET %s"
            cur.execute(sql, tuple(params) + (per_page, offset))
            items = cur.fetchall()
    finally:
        conn.close()

    return jsonify({
        'items': items,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
    })


@admin_bp.route('/api/volunteer-requests/<ptc_code>/confirm', methods=['POST'])
def confirm_volunteer(ptc_code):
    """Confirm a volunteer request."""
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE volunteer_requests SET status = 'confirmed', reviewed_at = %s, reviewed_by = %s "
                "WHERE ptc_code = %s AND status = 'pending'",
                (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), config.ADMIN_USERNAME, ptc_code)
            )
            modified = cur.rowcount
    finally:
        conn.close()
    if modified:
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Not found or already reviewed.'}), 404


@admin_bp.route('/api/volunteer-requests/<ptc_code>/reject', methods=['POST'])
def reject_volunteer(ptc_code):
    """Reject a volunteer request."""
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE volunteer_requests SET status = 'rejected', reviewed_at = %s, reviewed_by = %s "
                "WHERE ptc_code = %s AND status = 'pending'",
                (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), config.ADMIN_USERNAME, ptc_code)
            )
            modified = cur.rowcount
    finally:
        conn.close()
    if modified:
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Not found or already reviewed.'}), 404


@admin_bp.route('/confirmed-volunteers')
def confirmed_volunteers_page():
    """Show confirmed volunteers."""
    return render_template('admin/confirmed_volunteers.html')


@admin_bp.route('/api/confirmed-volunteers')
def api_confirmed_volunteers():
    """JSON API for confirmed volunteers - server-side search & pagination."""
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    where_parts = ["status = 'confirmed'"]
    params = []
    if search:
        like = f"%{search}%"
        where_parts.append("(name LIKE %s OR ptc_code LIKE %s OR epic_no LIKE %s OR mobile LIKE %s)")
        params.extend([like, like, like, like])
    where_clause = " AND ".join(where_parts)

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM volunteer_requests WHERE {where_clause}", tuple(params))
            total = cur.fetchone()['cnt']

            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            offset = (page - 1) * per_page

            cur.execute(
                f"SELECT * FROM volunteer_requests WHERE {where_clause} ORDER BY reviewed_at DESC LIMIT %s OFFSET %s",
                tuple(params) + (per_page, offset)
            )
            items = cur.fetchall()
    finally:
        conn.close()

    return jsonify({
        'items': items, 'total': total,
        'page': page, 'per_page': per_page, 'total_pages': total_pages,
    })


# ── Booth Agent Requests ─────────────────────────────────────────

@admin_bp.route('/booth-agent-requests')
def booth_agent_requests_page():
    """Show booth agent requests."""
    return render_template('admin/booth_agent_requests.html')


@admin_bp.route('/api/booth-agent-requests')
def api_booth_agent_requests():
    """JSON API for booth agent requests - server-side search & pagination."""
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    where_parts = []
    params = []
    if status:
        where_parts.append("status = %s")
        params.append(status)
    if search:
        like = f"%{search}%"
        where_parts.append("(name LIKE %s OR ptc_code LIKE %s OR epic_no LIKE %s OR mobile LIKE %s OR assembly LIKE %s OR district LIKE %s)")
        params.extend([like, like, like, like, like, like])
    where_clause = " AND ".join(where_parts) if where_parts else ""

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            count_sql = "SELECT COUNT(*) AS cnt FROM booth_agent_requests"
            if where_clause:
                count_sql += f" WHERE {where_clause}"
            cur.execute(count_sql, tuple(params))
            total = cur.fetchone()['cnt']

            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            offset = (page - 1) * per_page

            sql = "SELECT * FROM booth_agent_requests"
            if where_clause:
                sql += f" WHERE {where_clause}"
            sql += " ORDER BY requested_at DESC LIMIT %s OFFSET %s"
            cur.execute(sql, tuple(params) + (per_page, offset))
            items = cur.fetchall()
    finally:
        conn.close()

    return jsonify({
        'items': items,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
    })


@admin_bp.route('/api/booth-agent-requests/<ptc_code>/confirm', methods=['POST'])
def confirm_booth_agent(ptc_code):
    """Confirm a booth agent request."""
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE booth_agent_requests SET status = 'confirmed', reviewed_at = %s, reviewed_by = %s "
                "WHERE ptc_code = %s AND status = 'pending'",
                (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), config.ADMIN_USERNAME, ptc_code)
            )
            modified = cur.rowcount
    finally:
        conn.close()
    if modified:
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Not found or already reviewed.'}), 404


@admin_bp.route('/api/booth-agent-requests/<ptc_code>/reject', methods=['POST'])
def reject_booth_agent(ptc_code):
    """Reject a booth agent request."""
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE booth_agent_requests SET status = 'rejected', reviewed_at = %s, reviewed_by = %s "
                "WHERE ptc_code = %s AND status = 'pending'",
                (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), config.ADMIN_USERNAME, ptc_code)
            )
            modified = cur.rowcount
    finally:
        conn.close()
    if modified:
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Not found or already reviewed.'}), 404


@admin_bp.route('/confirmed-booth-agents')
def confirmed_booth_agents_page():
    """Show confirmed booth agents."""
    return render_template('admin/confirmed_booth_agents.html')


@admin_bp.route('/api/confirmed-booth-agents')
def api_confirmed_booth_agents():
    """JSON API for confirmed booth agents - server-side search & pagination."""
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    where_parts = ["status = 'confirmed'"]
    params = []
    if search:
        like = f"%{search}%"
        where_parts.append("(name LIKE %s OR ptc_code LIKE %s OR epic_no LIKE %s OR mobile LIKE %s)")
        params.extend([like, like, like, like])
    where_clause = " AND ".join(where_parts)

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM booth_agent_requests WHERE {where_clause}", tuple(params))
            total = cur.fetchone()['cnt']

            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            offset = (page - 1) * per_page

            cur.execute(
                f"SELECT * FROM booth_agent_requests WHERE {where_clause} ORDER BY reviewed_at DESC LIMIT %s OFFSET %s",
                tuple(params) + (per_page, offset)
            )
            items = cur.fetchall()
    finally:
        conn.close()

    return jsonify({
        'items': items, 'total': total,
        'page': page, 'per_page': per_page, 'total_pages': total_pages,
    })


# Register admin blueprint
app.register_blueprint(admin_bp)

# Register health check blueprint
app.register_blueprint(health_bp)

# Register WhatsApp bot blueprint (exempt from global rate limiter)
from whatsappbot import whatsapp_bp
limiter.exempt(whatsapp_bp)
app.register_blueprint(whatsapp_bp)


# ══════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Voter ID Card Generator v4.0')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--host', type=str, default='127.0.0.1')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    print("=" * 60)
    print("  VOTER ID CARD GENERATOR v4.0")
    print(f"  User  : http://{args.host}:{args.port}/")
    print(f"  Admin : http://{args.host}:{args.port}/admin")
    print(f"  WhatsApp Webhook : http://{args.host}:{args.port}/whatsapp/webhook")
    print("  Database: MySQL | Photos: Cloudinary")
    print("=" * 60)

    app.run(host=args.host, port=args.port, debug=args.debug)
