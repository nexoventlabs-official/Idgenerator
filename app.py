"""
We The Leaders — Voter ID Card Generator v5.0
==============================================
Database : MongoDB Atlas (all collections)
Photos   : Cloudinary
Cards    : Cloudinary
"""
import io, json, os, re, secrets, sys, threading, uuid
from datetime import datetime, timezone, timedelta

from flask import (Flask, Blueprint, render_template, request,
                   redirect, url_for, flash, jsonify, session)
from PIL import Image
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

import requests as http_requests
import cloudinary, cloudinary.uploader, cloudinary.api
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

import config
from generate_cards import (setup_logging, generate_card, generate_back_card,
                             generate_serial_number,
                             load_bold_font, get_text_width, load_member_photo)
from security_fixes import (hash_pin, verify_pin, rate_limit, rate_limiter,
                             validate_mobile, validate_epic, validate_pin,
                             validate_otp, sanitize_search, validate_file_upload,
                             login_tracker)
from health_check import health_bp
from face_detection import validate_photo_for_id_card

# ── Celery (optional — card-status polling only) ──────────────────
try:
    from tasks import celery, generate_card_async
    _celery_available = True
except Exception:
    _celery_available = False

from pybreaker import CircuitBreaker
cloudinary_breaker = CircuitBreaker(fail_max=5, reset_timeout=60, name='cloudinary')
sms_breaker        = CircuitBreaker(fail_max=3, reset_timeout=120, name='sms_api')

# ── Flask App ─────────────────────────────────────────────────────
app = Flask(__name__,
            template_folder=os.path.join(config.BASE_DIR, 'templates'),
            static_folder=os.path.join(config.BASE_DIR, 'static'))
app.secret_key = os.getenv('FLASK_SECRET', 'voter-id-gen-secret-2026')
app.wsgi_app   = ProxyFix(app.wsgi_app, x_for=2, x_proto=1, x_host=1, x_prefix=1)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

ALLOWED_IMG = {'png', 'jpg', 'jpeg', 'bmp'}
logger = setup_logging()

# ── Rate limiter (in-memory) ──────────────────────────────────────
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
limiter = Limiter(app=app, key_func=get_remote_address,
                  storage_uri='memory://',
                  default_limits=["2000 per day", "500 per hour"],
                  strategy="fixed-window")

# ── Session config ────────────────────────────────────────────────
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE']   = os.getenv('FLASK_ENV') != 'development'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400

# ── HTTPS / CORS ──────────────────────────────────────────────────
from flask_talisman import Talisman
if os.getenv('FLASK_ENV') != 'development':
    Talisman(app, force_https=True,
             strict_transport_security=True,
             strict_transport_security_max_age=31536000,
             content_security_policy={
                 'default-src': ["'self'", 'https://res.cloudinary.com'],
                 'img-src':     ["'self'", 'https://res.cloudinary.com', 'data:'],
                 'style-src':   ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com', 'https://cdn.jsdelivr.net'],
                 'script-src':  ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net'],
                 'font-src':    ["'self'", 'https://fonts.gstatic.com', 'https://cdn.jsdelivr.net'],
                 'connect-src': ["'self'", 'https://cdn.jsdelivr.net'],
             })

from flask_cors import CORS
CORS(app, resources={r"/api/*": {
    "origins": os.getenv('ALLOWED_ORIGINS', '*').split(','),
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"],
}})

# ── Security headers ──────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options']  = 'nosniff'
    response.headers['X-Frame-Options']          = 'DENY'
    response.headers['X-XSS-Protection']         = '1; mode=block'
    response.headers['Referrer-Policy']          = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']       = 'geolocation=(), microphone=(), camera=()'
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif request.path.startswith('/admin') or request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private, max-age=0'
        response.headers['Pragma']  = 'no-cache'
        response.headers['Expires'] = '0'
    elif os.getenv('FLASK_ENV') == 'development':
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    else:
        response.headers['Cache-Control'] = 'public, max-age=300'
    return response

for d in [config.MEMBER_PHOTOS_DIR, config.DATA_DIR, config.UPLOADS_DIR]:
    os.makedirs(d, exist_ok=True)

IST = timezone(timedelta(hours=5, minutes=30))

@app.template_filter('to_ist')
def to_ist(dt_str):
    if not dt_str:
        return '-'
    try:
        s  = str(dt_str).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s) if isinstance(dt_str, str) else dt_str
        if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(dt_str)[:19].replace('T', ' ') if dt_str else '-'

# ══════════════════════════════════════════════════════════════════
#  MONGODB SETUP
# ══════════════════════════════════════════════════════════════════
_mongo_client: MongoClient | None = None

def _get_db():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=10000,
                                    maxPoolSize=50, minPoolSize=5)
        logger.info("MongoDB Atlas connected: %s", config.MONGO_DB)
    return _mongo_client[config.MONGO_DB]

def _ensure_mongo_indexes():
    try:
        db = _get_db()
        db.voters.create_index([("EPIC_NO", ASCENDING)], unique=True, background=True)
        db.voters.create_index([("ASSEMBLY_NAME", ASCENDING)], background=True)
        db.voters.create_index([("DISTRICT_NAME", ASCENDING)], background=True)
        db.voters.create_index([("FM_NAME_EN", ASCENDING)], background=True)
        db.voters.create_index([("VOTER_NAME", ASCENDING)], background=True)
        db.voters.create_index([("MOBILE_NO", ASCENDING)], background=True)
        db.generated_voters.create_index([("EPIC_NO", ASCENDING)], unique=True, background=True)
        db.generated_voters.create_index([("MOBILE_NO", ASCENDING)], background=True)
        db.generated_voters.create_index([("ptc_code", ASCENDING)], unique=True, sparse=True, background=True)
        db.generated_voters.create_index([("referred_by_ptc", ASCENDING)], background=True)
        db.generation_stats.create_index([("epic_no", ASCENDING)], unique=True, background=True)
        db.generation_stats.create_index([("auth_mobile", ASCENDING)], background=True)
        db.otp_sessions.create_index([("mobile", ASCENDING)], unique=True, background=True)
        db.otp_sessions.create_index([("created_at", ASCENDING)], expireAfterSeconds=600, background=True)
        db.volunteer_requests.create_index([("ptc_code", ASCENDING)], background=True)
        db.booth_agent_requests.create_index([("ptc_code", ASCENDING)], background=True)
        logger.info("MongoDB indexes ensured.")
    except Exception as e:
        logger.warning("Index setup warning: %s", e)

threading.Thread(target=_ensure_mongo_indexes, daemon=True).start()

# ── Cloudinary ────────────────────────────────────────────────────
cloudinary.config(cloud_name=config.CLOUDINARY_CLOUD_NAME,
                  api_key=config.CLOUDINARY_API_KEY,
                  api_secret=config.CLOUDINARY_API_SECRET,
                  secure=True)

# ── Cache (in-memory dict, no Redis) ─────────────────────────────
_cache: dict = {}

def _cache_get(key):
    entry = _cache.get(key)
    if not entry:
        return None
    if entry['expires'] < datetime.now(timezone.utc).timestamp():
        del _cache[key]
        return None
    return entry['value']

def _cache_set(key, value, ttl=60):
    _cache[key] = {
        'value':   value,
        'expires': datetime.now(timezone.utc).timestamp() + ttl,
    }

# ══════════════════════════════════════════════════════════════════
#  VOTER HELPERS
# ══════════════════════════════════════════════════════════════════

def _doc_to_voter(doc: dict) -> dict | None:
    if not doc:
        return None
    voter_name = ((doc.get('FM_NAME_EN') or '') + ' ' + (doc.get('LASTNAME_EN') or '')).strip()
    if not voter_name:
        voter_name = doc.get('VOTER_NAME', '')
    rel_name = ((doc.get('RLN_FM_NM_EN') or '') + ' ' + (doc.get('RLN_L_NM_EN') or '')).strip()
    if not rel_name:
        rel_name = doc.get('RELATION_NAME', '')
    rel_name_v1 = ((doc.get('RLN_FM_NM_V1') or '') + ' ' + (doc.get('RLN_L_NM_V1') or '')).strip()
    name_v1 = ((doc.get('FM_NAME_V1') or '') + ' ' + (doc.get('LASTNAME_V1') or '')).strip()
    return {
        'epic_no':        doc.get('EPIC_NO', ''),
        'name':           voter_name,
        'assembly':       str(doc.get('AC_NO') or doc.get('ASSEMBLY_NO') or ''),
        'assembly_name':  doc.get('ASSEMBLY_NAME', ''),
        'district':       doc.get('DISTRICT_NAME') or doc.get('DISTRICT', ''),
        'age':            doc.get('AGE', ''),
        'sex':            doc.get('GENDER', ''),
        'relation_type':  doc.get('RLN_TYPE', ''),
        'relation_name':  rel_name,
        'relation_name_v1': rel_name_v1,
        'mobile':         doc.get('MOBILE_NO') or doc.get('MOBILE_NUMBER', ''),
        'part_no':        str(doc.get('PART_NO') or ''),
        'section_no':     str(doc.get('SECTION_NO') or ''),
        'slno_in_part':   str(doc.get('SLNOINPART') or ''),
        'house_no':       doc.get('C_HOUSE_NO') or doc.get('HOUSE_NO', ''),
        'house_no_v1':    doc.get('C_HOUSE_NO_V1', ''),
        'dob':            doc.get('DOB', ''),
        'name_v1':        name_v1,
        'org_list_no':    str(doc.get('ORG_LIST_NO') or ''),
        'district_id':    doc.get('DISTRICT_ID', ''),
        'id':             str(doc.get('_id', '')),
        # Raw uppercase keys for card generation
        'FM_NAME_EN':    doc.get('FM_NAME_EN', ''),
        'LASTNAME_EN':   doc.get('LASTNAME_EN', ''),
        'FM_NAME_V1':    doc.get('FM_NAME_V1', ''),
        'LASTNAME_V1':   doc.get('LASTNAME_V1', ''),
        'AC_NO':         doc.get('AC_NO') or doc.get('ASSEMBLY_NO', ''),
        'ASSEMBLY_NAME': doc.get('ASSEMBLY_NAME', ''),
        'DISTRICT_NAME': doc.get('DISTRICT_NAME') or doc.get('DISTRICT', ''),
        'PART_NO':       doc.get('PART_NO'),
        'SECTION_NO':    doc.get('SECTION_NO'),
        'SLNOINPART':    doc.get('SLNOINPART'),
        'C_HOUSE_NO':    doc.get('C_HOUSE_NO'),
        'C_HOUSE_NO_V1': doc.get('C_HOUSE_NO_V1'),
        'RLN_TYPE':      doc.get('RLN_TYPE', ''),
        'RLN_FM_NM_EN':  doc.get('RLN_FM_NM_EN', ''),
        'RLN_L_NM_EN':   doc.get('RLN_L_NM_EN', ''),
        'RLN_FM_NM_V1':  doc.get('RLN_FM_NM_V1', ''),
        'RLN_L_NM_V1':   doc.get('RLN_L_NM_V1', ''),
        'EPIC_NO':       doc.get('EPIC_NO', ''),
        'GENDER':        doc.get('GENDER', ''),
        'AGE':           doc.get('AGE', ''),
        'DOB':           doc.get('DOB', ''),
        'MOBILE_NO':     doc.get('MOBILE_NO') or doc.get('MOBILE_NUMBER', ''),
        'ORG_LIST_NO':   doc.get('ORG_LIST_NO', ''),
    }


def _gen_doc_to_dict(doc: dict) -> dict | None:
    if not doc:
        return None
    base = _doc_to_voter(doc) or {}
    base.update({
        'ptc_code':                 doc.get('ptc_code', ''),
        'photo_url':                doc.get('photo_url', ''),
        'card_url':                 doc.get('card_url', ''),
        'secret_pin':               doc.get('secret_pin'),
        'referral_id':              doc.get('referral_id'),
        'referral_link':            doc.get('referral_link'),
        'referred_by_ptc':          doc.get('referred_by_ptc'),
        'referred_by_referral_id':  doc.get('referred_by_referral_id'),
        'referred_members_count':   doc.get('referred_members_count', 0),
        'source':                   doc.get('source'),
        'generated_at':             doc.get('generated_at'),
        'created_at':               doc.get('created_at'),
        'id':                       str(doc.get('_id', '')),
        'volunteer_status':         doc.get('volunteer_status', ''),
        'booth_agent_status':       doc.get('booth_agent_status', ''),
    })
    return base


def find_voter_by_epic(epic_no: str) -> dict | None:
    epic_no = epic_no.strip().upper()
    if not epic_no:
        return None
    cache_key = f'wtl:epic:{epic_no}'
    cached = _cache_get(cache_key)
    if cached:
        return cached if cached.get('epic_no') else None
    db  = _get_db()
    doc = db.voters.find_one({"EPIC_NO": epic_no})
    if doc:
        result = _doc_to_voter(doc)
        _cache_set(cache_key, result, 600)
        return result
    _cache_set(cache_key, {'epic_no': ''}, 120)
    return None


def generate_ptc_code() -> str:
    return 'PTC-' + uuid.uuid4().hex[:7].upper()


def get_voter_gen_count(epic_no: str) -> int:
    db  = _get_db()
    doc = db.generation_stats.find_one({"epic_no": epic_no}, {"count": 1})
    if doc and doc.get('count'):
        return int(doc['count'])
    return db.generated_voters.count_documents({"EPIC_NO": epic_no})


def get_voter_card_url(epic_no: str) -> str:
    db  = _get_db()
    doc = db.generation_stats.find_one({"epic_no": epic_no}, {"card_url": 1})
    if doc and doc.get('card_url'):
        return doc['card_url']
    doc = db.generated_voters.find_one({"EPIC_NO": epic_no},
                                        {"card_url": 1},
                                        sort=[("generated_at", DESCENDING)])
    return (doc or {}).get('card_url', '')


def get_voter_photo_url(epic_no: str) -> str:
    db  = _get_db()
    doc = db.generation_stats.find_one({"epic_no": epic_no}, {"photo_url": 1})
    return (doc or {}).get('photo_url', '')


def upload_photo_to_cloudinary(image: Image.Image, epic_no: str) -> str:
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=90)
    buf.seek(0)
    result = cloudinary.uploader.upload(buf, folder=config.CLOUDINARY_PHOTO_FOLDER,
                                         public_id=epic_no, overwrite=True, resource_type='image')
    return result.get('secure_url', '')


def upload_card_to_cloudinary(card_image: Image.Image, epic_no: str) -> str:
    safe_id = epic_no.replace('/', '_').replace('\\', '_')
    buf = io.BytesIO()
    card_image.save(buf, format='JPEG', quality=config.JPEG_QUALITY)
    buf.seek(0)
    result = cloudinary.uploader.upload(buf, folder=config.CLOUDINARY_CARDS_FOLDER,
                                         public_id=safe_id, overwrite=True,
                                         invalidate=True, resource_type='image')
    return result.get('secure_url', '')


def allowed_file(filename, exts):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in exts


def _get_cached_dropdowns(source: str, cache_key: str):
    cached = _cache_get(cache_key)
    if cached:
        return cached.get('assemblies', []), cached.get('districts', [])
    db = _get_db()
    col = db.voters if source == 'voters' else db.generated_voters
    assemblies = sorted([v for v in col.distinct("ASSEMBLY_NAME") if v])
    districts  = sorted([v for v in col.distinct("DISTRICT_NAME") if v])
    _cache_set(cache_key, {'assemblies': assemblies, 'districts': districts}, 300)
    return assemblies, districts


def _get_external_stats() -> dict:
    cached = _cache_get('wtl:external_stats')
    if cached:
        return cached
    result = {'db1_size_mb': 0, 'db2_size_mb': 0, 'db2_objects': 0,
              'cloudinary_credits': 'N/A', 'sms_balance': 'N/A'}
    try:
        stats = _get_db().command("dbStats")
        total_mb = round(stats.get('dataSize', 0) / 1024 / 1024, 2)
        result['db1_size_mb'] = total_mb
        result['db2_size_mb'] = total_mb
        result['db2_objects'] = _get_db().generated_voters.estimated_document_count()
    except Exception:
        pass
    try:
        cli_usage = cloudinary.api.usage()
        result['cloudinary_credits'] = str(round(cli_usage.get('credits', {}).get('usage', 0), 2))
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
    _cache_set('wtl:external_stats', result, 300)
    return result


def get_dashboard_stats():
    cached = _cache_get('wtl:dashboard_stats')
    if cached:
        return cached
    try:
        db = _get_db()
        total_voters       = db.voters.estimated_document_count()
        generated_count    = db.generated_voters.estimated_document_count()
        pipeline = [{"$group": {"_id": None,
                                "total_generated":  {"$sum": {"$cond": [{"$gt": ["$count", 0]}, 1, 0]}},
                                "total_generations":{"$sum": "$count"},
                                "cards_on_cloud":   {"$sum": {"$cond": [{"$and": [{"$ne": ["$card_url", ""]}, {"$ne": ["$card_url", None]}]}, 1, 0]}}}}]
        agg = list(db.generation_stats.aggregate(pipeline))
        sa  = agg[0] if agg else {}
        total_generated  = sa.get('total_generated', 0)
        total_generations= sa.get('total_generations', 0)
        cards_on_cloud   = sa.get('cards_on_cloud', 0)
        total_referrals  = list(db.generated_voters.aggregate([
            {"$group": {"_id": None, "total": {"$sum": "$referred_members_count"}}}]))
        total_referrals  = (total_referrals[0]['total'] if total_referrals else 0)
        pending_vols    = db.volunteer_requests.count_documents({"status": "pending"})
        confirmed_vols  = db.volunteer_requests.count_documents({"status": "confirmed"})
        pending_ba      = db.booth_agent_requests.count_documents({"status": "pending"})
        confirmed_ba    = db.booth_agent_requests.count_documents({"status": "confirmed"})
        db_connected    = True
    except Exception:
        total_voters = total_generated = total_generations = cards_on_cloud = 0
        generated_count = total_referrals = 0
        pending_vols = confirmed_vols = pending_ba = confirmed_ba = 0
        db_connected = False

    result = {
        'total_voters': total_voters,
        'total_generated': total_generated,
        'total_generations': total_generations,
        'cards_on_cloud': cards_on_cloud,
        'generated_voters_count': generated_count,
        'db_connected': db_connected,
        'total_referrals': total_referrals,
        'pending_volunteers': pending_vols,
        'confirmed_volunteers': confirmed_vols,
        'pending_booth_agents': pending_ba,
        'confirmed_booth_agents': confirmed_ba,
        'cloudinary_credits': '...',
        'sms_balance': '...',
    }
    _cache_set('wtl:dashboard_stats', result, 60)
    return result

# ══════════════════════════════════════════════════════════════════
#  PUBLIC ROUTES
# ══════════════════════════════════════════════════════════════════

@app.route('/favicon.ico')
def favicon():
    from flask import send_from_directory
    return send_from_directory(app.static_folder, 'newfavicon.png', mimetype='image/png')

@app.route('/google17d450ee87a4cb34.html')
def google_site_verification():
    return 'google-site-verification: google17d450ee87a4cb34.html', 200, {'Content-Type': 'text/html'}

@app.route('/robots.txt')
def robots_txt():
    return ("User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /api/\nDisallow: /card/\n\n"
            f"Sitemap: {config.BASE_URL}/sitemap.xml\n"), 200, {'Content-Type': 'text/plain'}

@app.route('/sitemap.xml')
def sitemap_xml():
    content = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
               f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               f'  <url><loc>{config.BASE_URL}/</loc>'
               f'<lastmod>2026-03-07</lastmod>'
               f'<changefreq>weekly</changefreq><priority>1.0</priority></url>\n'
               f'</urlset>')
    return content, 200, {'Content-Type': 'application/xml'}

@app.route('/cronjob')
def cronjob():
    return 'OK', 200, {'Content-Type': 'text/plain'}

@app.route('/')
def user_home():
    resp = app.make_response(render_template('user/chatbot.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

# ══════════════════════════════════════════════════════════════════
#  CHATBOT API ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@app.route('/api/chat/send-otp', methods=['POST'])
@limiter.limit("3 per 5 minutes")
def chat_send_otp():
    data   = request.get_json()
    mobile = data.get('mobile', '').strip()
    valid, result = validate_mobile(mobile)
    if not valid:
        return jsonify({'success': False, 'message': result}), 400
    mobile = result

    db  = _get_db()
    doc = db.otp_sessions.find_one({"mobile": mobile}, {"created_at": 1})
    if doc and doc.get('created_at'):
        try:
            created = doc['created_at']
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

    otp      = str(secrets.randbelow(900000) + 100000)
    otp_sent = False
    sms_api_key = os.getenv('SMS_API_KEY', '')
    if sms_api_key:
        try:
            @sms_breaker
            def send_sms():
                resp = http_requests.get(
                    f'https://2factor.in/API/V1/{sms_api_key}/SMS/{mobile}/{otp}', timeout=15)
                return resp.status_code == 200 and resp.json().get('Status') == 'Success'
            otp_sent = send_sms()
        except Exception as e:
            logger.warning("OTP send failed: %s", e)

    if not otp_sent:
        return jsonify({'success': False, 'message': 'Could not send OTP. Please try again.'}), 500

    now = datetime.now(timezone.utc)
    db.otp_sessions.update_one({"mobile": mobile},
                                {"$set": {"otp": otp, "created_at": now, "verified": False, "purpose": None}},
                                upsert=True)
    return jsonify({'success': True})


@app.route('/api/chat/verify-otp', methods=['POST'])
@rate_limit(max_requests=5, window_seconds=300)
def chat_verify_otp():
    data   = request.get_json()
    mobile = data.get('mobile', '').strip()
    otp    = data.get('otp', '').strip()
    valid_m, mobile = validate_mobile(mobile)
    if not valid_m:
        return jsonify({'success': False, 'message': mobile}), 400
    valid_o, otp = validate_otp(otp)
    if not valid_o:
        return jsonify({'success': False, 'message': otp}), 400

    db  = _get_db()
    doc = db.otp_sessions.find_one({"mobile": mobile})
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

    db.otp_sessions.update_one({"mobile": mobile}, {"$set": {"verified": True}})
    session['verified_mobile'] = mobile
    session.permanent = True

    stat    = db.generation_stats.find_one({"auth_mobile": mobile})
    gen_doc = db.generated_voters.find_one({"MOBILE_NO": mobile}, sort=[("generated_at", DESCENDING)])

    if (stat and stat.get('card_url')) or (gen_doc and gen_doc.get('card_url')):
        s = stat or gen_doc
        g = gen_doc or {}
        name = ((g.get('FM_NAME_EN') or '') + ' ' + (g.get('LASTNAME_EN') or '')).strip()
        return jsonify({
            'success': True, 'has_card': True,
            'epic_no':     s.get('epic_no') or g.get('EPIC_NO', ''),
            'card_url':    s.get('card_url', ''),
            'voter_name':  name,
            'photo_url':   g.get('photo_url', ''),
        })
    return jsonify({'success': True, 'has_card': False})


@app.route('/api/chat/check-mobile', methods=['POST'])
def chat_check_mobile():
    data   = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400
    db  = _get_db()
    stat    = db.generation_stats.find_one({"auth_mobile": mobile})
    gen_doc = db.generated_voters.find_one({"MOBILE_NO": mobile}, sort=[("generated_at", DESCENDING)])
    has_card = bool((stat and stat.get('card_url')) or (gen_doc and gen_doc.get('card_url')))
    if has_card:
        s = stat or {}
        g = gen_doc or {}
        has_pin = bool(s.get('secret_pin') or g.get('secret_pin'))
        result  = {'success': True, 'has_card': True, 'has_pin': has_pin}
        if not has_pin:
            result['epic_no']    = s.get('epic_no') or g.get('EPIC_NO', '')
            result['card_url']   = s.get('card_url') or g.get('card_url', '')
            name = ((g.get('FM_NAME_EN') or '') + ' ' + (g.get('LASTNAME_EN') or '')).strip()
            result['voter_name'] = name
            result['photo_url']  = g.get('photo_url', '')
        return jsonify(result)
    return jsonify({'success': True, 'has_card': False, 'has_pin': False})


@app.route('/api/chat/verify-pin', methods=['POST'])
@rate_limit(max_requests=5, window_seconds=300)
def chat_verify_pin():
    data   = request.get_json()
    mobile = data.get('mobile', '').strip()
    pin    = data.get('pin', '').strip()
    valid_m, mobile = validate_mobile(mobile)
    if not valid_m:
        return jsonify({'success': False, 'message': mobile}), 400
    valid_p, pin = validate_pin(pin)
    if not valid_p:
        return jsonify({'success': False, 'message': pin}), 400

    db   = _get_db()
    stat = db.generation_stats.find_one({"auth_mobile": mobile})
    if not stat or not stat.get('secret_pin'):
        return jsonify({'success': False, 'message': 'No PIN found for this mobile.'}), 404
    if not verify_pin(pin, stat['secret_pin']):
        return jsonify({'success': False, 'message': 'Invalid PIN. Please try again.'}), 400

    gen_doc = db.generated_voters.find_one({"MOBILE_NO": mobile})
    name    = ((gen_doc.get('FM_NAME_EN') or '') + ' ' + (gen_doc.get('LASTNAME_EN') or '')).strip() if gen_doc else ''
    return jsonify({
        'success': True, 'has_card': True,
        'epic_no':    stat.get('epic_no', ''),
        'card_url':   stat.get('card_url', ''),
        'voter_name': name,
        'photo_url':  (gen_doc or {}).get('photo_url', ''),
    })


@app.route('/api/chat/forgot-pin', methods=['POST'])
def chat_forgot_pin():
    data   = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400
    db  = _get_db()
    has_acct = (db.generation_stats.find_one({"auth_mobile": mobile}) or
                db.generated_voters.find_one({"MOBILE_NO": mobile}))
    if not has_acct:
        return jsonify({'success': False, 'message': 'No account found for this mobile.'}), 404

    doc = db.otp_sessions.find_one({"mobile": mobile}, {"created_at": 1})
    if doc and doc.get('created_at'):
        try:
            created = doc['created_at']
            if isinstance(created, str):
                created = datetime.fromisoformat(created)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - created).total_seconds() < 60:
                wait = int(60 - (datetime.now(timezone.utc) - created).total_seconds())
                return jsonify({'success': False, 'message': f'Please wait {wait}s.'}), 429
        except Exception:
            pass

    otp      = str(secrets.randbelow(900000) + 100000)
    otp_sent = False
    sms_key  = os.getenv('SMS_API_KEY', '')
    if sms_key:
        try:
            resp = http_requests.get(f'https://2factor.in/API/V1/{sms_key}/SMS/{mobile}/{otp}', timeout=15)
            otp_sent = resp.status_code == 200 and resp.json().get('Status') == 'Success'
        except Exception:
            pass
    if not otp_sent:
        return jsonify({'success': False, 'message': 'Could not send OTP. Please try again.'}), 500

    db.otp_sessions.update_one({"mobile": mobile},
                                {"$set": {"otp": otp, "created_at": datetime.now(timezone.utc),
                                          "verified": False, "purpose": "pin_reset"}},
                                upsert=True)
    return jsonify({'success': True})


@app.route('/api/chat/verify-forgot-otp', methods=['POST'])
def chat_verify_forgot_otp():
    data   = request.get_json()
    mobile = data.get('mobile', '').strip()
    otp    = data.get('otp', '').strip()
    if not mobile or not otp:
        return jsonify({'success': False, 'message': 'Mobile and OTP required'}), 400
    db  = _get_db()
    doc = db.otp_sessions.find_one({"mobile": mobile})
    if not doc or doc.get('otp') != otp:
        return jsonify({'success': False, 'message': 'Invalid OTP'}), 400
    try:
        created = doc['created_at']
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            return jsonify({'success': False, 'message': 'OTP expired.'}), 400
    except Exception:
        pass
    return jsonify({'success': True})


@app.route('/api/chat/reset-pin', methods=['POST'])
@rate_limit(max_requests=3, window_seconds=300)
def chat_reset_pin():
    data   = request.get_json()
    mobile = data.get('mobile', '').strip()
    otp    = data.get('otp', '').strip()
    valid_m, mobile = validate_mobile(mobile)
    if not valid_m:
        return jsonify({'success': False, 'message': mobile}), 400
    valid_o, otp = validate_otp(otp)
    if not valid_o:
        return jsonify({'success': False, 'message': otp}), 400

    db  = _get_db()
    doc = db.otp_sessions.find_one({"mobile": mobile})
    if not doc or doc.get('otp') != otp:
        return jsonify({'success': False, 'message': 'Invalid OTP'}), 400
    try:
        created = doc['created_at']
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            return jsonify({'success': False, 'message': 'OTP expired.'}), 400
    except Exception:
        pass

    new_pin = data.get('new_pin', '').strip()
    valid_p, new_pin = validate_pin(new_pin)
    if not valid_p:
        return jsonify({'success': False, 'message': new_pin}), 400

    hashed = hash_pin(new_pin)
    db.generation_stats.update_one({"auth_mobile": mobile}, {"$set": {"secret_pin": hashed}})
    db.generated_voters.update_many({"MOBILE_NO": mobile}, {"$set": {"secret_pin": hashed}})
    db.otp_sessions.delete_one({"mobile": mobile})

    stat    = db.generation_stats.find_one({"auth_mobile": mobile})
    gen_doc = db.generated_voters.find_one({"MOBILE_NO": mobile})
    name    = ((gen_doc.get('FM_NAME_EN') or '') + ' ' + (gen_doc.get('LASTNAME_EN') or '')).strip() if gen_doc else ''
    return jsonify({
        'success': True, 'has_card': True,
        'epic_no':    (stat or {}).get('epic_no', ''),
        'card_url':   (stat or {}).get('card_url', ''),
        'voter_name': name,
        'photo_url':  (gen_doc or {}).get('photo_url', ''),
    })


@app.route('/api/chat/set-pin', methods=['POST'])
@rate_limit(max_requests=3, window_seconds=300)
def chat_set_pin():
    data    = request.get_json()
    mobile  = data.get('mobile', '').strip()
    pin     = data.get('pin', '').strip()
    epic_no = data.get('epic_no', '').strip().upper()
    valid_m, mobile = validate_mobile(mobile)
    if not valid_m:
        return jsonify({'success': False, 'message': mobile}), 400
    valid_p, pin = validate_pin(pin)
    if not valid_p:
        return jsonify({'success': False, 'message': pin}), 400

    hashed = hash_pin(pin)
    db = _get_db()
    if epic_no:
        db.generation_stats.update_one({"epic_no": epic_no},
                                        {"$set": {"secret_pin": hashed, "auth_mobile": mobile},
                                         "$setOnInsert": {"epic_no": epic_no}},
                                        upsert=True)
    else:
        db.generation_stats.update_one({"auth_mobile": mobile}, {"$set": {"secret_pin": hashed}})
    db.generated_voters.update_many({"MOBILE_NO": mobile}, {"$set": {"secret_pin": hashed}})
    return jsonify({'success': True})


@app.route('/api/chat/validate-epic', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=60)
def chat_validate_epic():
    data    = request.get_json()
    epic_no = data.get('epic_no', '').strip().upper()
    valid, result = validate_epic(epic_no)
    if not valid:
        return jsonify({'success': False, 'message': result}), 400
    voter = find_voter_by_epic(result)
    if not voter:
        return jsonify({'success': False, 'message': 'EPIC Number not found. Please check and try again.'}), 404
    display = {k: str(v) if v else '' for k, v in voter.items() if not k.startswith('_')}
    return jsonify({'success': True, 'voter': display})


@app.route('/api/chat/generate-card', methods=['POST'])
@limiter.limit("5 per 5 minutes")
def chat_generate_card():
    """Generate ID card — EPIC required, passport photo required."""
    # Accept both JSON and form-data
    epic_no = (request.form.get('epic_no') or '').strip().upper()
    if not epic_no:
        data    = request.get_json(silent=True) or {}
        epic_no = data.get('epic_no', '').strip().upper()

    valid_epic, epic_result = validate_epic(epic_no)
    if not valid_epic:
        return jsonify({'success': False, 'message': epic_result}), 400
    epic_no = epic_result

    voter = find_voter_by_epic(epic_no)
    if not voter:
        return jsonify({'success': False, 'message': 'EPIC Number not found.'}), 404

    # ── Photo is required ─────────────────────────────────────────
    if 'photo' not in request.files or not request.files['photo'].filename:
        return jsonify({'success': False, 'message': 'Please upload your passport photo.'}), 400

    file = request.files['photo']
    valid_file, file_error = validate_file_upload(file, ALLOWED_IMG, max_size_mb=10)
    if not valid_file:
        return jsonify({'success': False, 'message': file_error}), 400

    try:
        # ── Validate face in photo ────────────────────────────────
        is_valid, face_msg, photo_image = validate_photo_for_id_card(file.stream)
        if not is_valid:
            return jsonify({
                'success': False,
                'message': face_msg,
                'error_type': 'face_detection_failed'
            }), 400

        # ── Upload photo to Cloudinary ────────────────────────────
        photo_url = ''
        try:
            photo_buf = io.BytesIO()
            photo_image.save(photo_buf, format='JPEG', quality=95)
            photo_buf.seek(0)
            up = cloudinary.uploader.upload(
                photo_buf.getvalue(),
                folder='member_photos', public_id=epic_no,
                overwrite=True, resource_type='image'
            )
            photo_url = up['secure_url']
            logger.info("Photo uploaded for %s: %s", epic_no, photo_url)
        except Exception as e:
            logger.error("Photo upload failed for %s: %s", epic_no, e)

        # ── Generate card ─────────────────────────────────────────
        ptc_code           = generate_ptc_code()
        voter['ptc_code']  = ptc_code
        voter['verify_url']= f"{config.BASE_URL}/verify/{epic_no}"

        # template arg kept for backward compat — new design builds its own canvas
        template   = None
        card_image = generate_card(voter, template, photo_image)

        # ── Upload front card ─────────────────────────────────────
        card_buf = io.BytesIO()
        card_image.save(card_buf, format='JPEG', quality=95)
        card_buf.seek(0)
        card_up  = cloudinary.uploader.upload(
            card_buf.getvalue(),
            folder='generated_cards', public_id=epic_no,
            overwrite=True, resource_type='image'
        )
        card_url = card_up['secure_url']
        logger.info("Card generated for %s: %s", epic_no, card_url)

        # ── Create combined front+back download image ─────────────
        combined_url = card_url  # fallback to front only
        back_url     = ''
        try:
            # Generate back card with newfavicon watermark
            back_img = generate_back_card()
            front_w, front_h = card_image.size
            back_resized = back_img.resize((front_w, front_h), Image.LANCZOS)

            # Upload back card separately (used for flip card in UI)
            back_buf = io.BytesIO()
            back_resized.save(back_buf, format='JPEG', quality=95)
            back_buf.seek(0)
            back_up = cloudinary.uploader.upload(
                back_buf.getvalue(),
                folder='generated_cards',
                public_id=f"{epic_no}_back",
                overwrite=True, resource_type='image'
            )
            back_url = back_up['secure_url']
            logger.info("Back card uploaded for %s: %s", epic_no, back_url)

            GAP = 30   # px gap between front and back
            combined_w = front_w + GAP + front_w
            combined   = Image.new('RGB', (combined_w, front_h), (240, 240, 240))
            combined.paste(card_image,   (0,             0))
            combined.paste(back_resized, (front_w + GAP, 0))

            # Upload combined image
            comb_buf = io.BytesIO()
            combined.save(comb_buf, format='JPEG', quality=95)
            comb_buf.seek(0)
            comb_up = cloudinary.uploader.upload(
                comb_buf.getvalue(),
                folder='generated_cards',
                public_id=f"{epic_no}_combined",
                overwrite=True, resource_type='image'
            )
            combined_url = comb_up['secure_url']
            logger.info("Combined card uploaded for %s", epic_no)
        except Exception as ce:
            logger.warning("Combined card creation failed for %s: %s", epic_no, ce)

        now = datetime.now(timezone.utc)
        db  = _get_db()

        db.generated_voters.update_one(
            {"EPIC_NO": epic_no},
            {"$set": {
                "EPIC_NO":        epic_no,
                "ptc_code":       ptc_code,
                "photo_url":      photo_url,
                "card_url":       card_url,
                "back_url":       back_url,
                "combined_url":   combined_url,
                "generated_at":   now,
                "FM_NAME_EN":     voter.get('FM_NAME_EN', ''),
                "LASTNAME_EN":    voter.get('LASTNAME_EN', ''),
                "ASSEMBLY_NAME":  voter.get('ASSEMBLY_NAME', ''),
                "DISTRICT_NAME":  voter.get('DISTRICT_NAME', ''),
                "AC_NO":          voter.get('AC_NO', ''),
            },
            "$setOnInsert": {"created_at": now}},
            upsert=True
        )
        db.generation_stats.update_one(
            {"epic_no": epic_no},
            {"$set":  {"card_url": card_url, "back_url": back_url,
                       "combined_url": combined_url,
                       "photo_url": photo_url, "last_generated": now},
             "$inc":  {"count": 1},
             "$setOnInsert": {"epic_no": epic_no}},
            upsert=True
        )

        return jsonify({
            'success':      True,
            'card_url':     card_url,
            'back_url':     back_url,
            'combined_url': combined_url,
            'photo_url':    photo_url,
            'epic_no':      epic_no,
            'voter_name':   voter.get('name', ''),
            'message':      'Card generated successfully',
        })

    except Exception as e:
        logger.error("Card generation error for %s: %s", epic_no, e)
        return jsonify({'success': False, 'message': 'Card generation failed. Please try again.'}), 500


@app.route('/api/chat/card-status/<job_id>')
def check_card_status(job_id):
    if not _celery_available:
        return jsonify({'status': 'error', 'message': 'Async jobs not available'}), 503
    try:
        from celery.result import AsyncResult
        task = AsyncResult(job_id, app=celery)
        if task.state == 'PENDING':
            return jsonify({'status': 'pending', 'message': 'Waiting to be processed'})
        elif task.state == 'PROCESSING':
            return jsonify({'status': 'processing', 'message': (task.info or {}).get('status', 'Processing...')})
        elif task.state == 'SUCCESS':
            result = task.result or {}
            if result.get('success') and result.get('epic_no'):
                mob = session.get('verified_mobile')
                if mob:
                    db = _get_db()
                    db.verified_mobiles.update_one(
                        {"mobile": mob},
                        {"$set": {"epic_no": result['epic_no'], "verified_at": datetime.now(timezone.utc)}},
                        upsert=True)
            return jsonify({'status': 'completed', **result})
        elif task.state == 'FAILURE':
            return jsonify({'status': 'failed', 'message': str(task.info) if task.info else 'Failed'})
        return jsonify({'status': task.state.lower()})
    except Exception as e:
        logger.error("Card status check error: %s", e)
        return jsonify({'status': 'error', 'message': 'Failed to check job status'}), 500


@app.route('/card/<epic_no>')
def user_card_page(epic_no):
    epic_no = epic_no.strip().upper()
    voter   = find_voter_by_epic(epic_no)
    if not voter:
        flash('Voter not found.', 'danger')
        return redirect(url_for('user_home'))
    card_url  = get_voter_card_url(epic_no)
    if not card_url:
        flash('Card not generated yet.', 'warning')
        return redirect(url_for('user_home'))
    gen_count = get_voter_gen_count(epic_no)
    # Get combined URL from DB
    db  = _get_db()
    gen = db.generation_stats.find_one({"epic_no": epic_no}, {"combined_url": 1, "back_url": 1}) or {}
    combined_url = gen.get('combined_url') or card_url
    back_url     = gen.get('back_url') or ''
    return render_template('user/card.html', epic_no=epic_no, voter=voter,
                           gen_count=gen_count, card_url=card_url,
                           combined_url=combined_url, back_url=back_url)


@app.route('/mycard/<epic_no>')
def user_serve_card(epic_no):
    mobile = session.get('verified_mobile')
    if not mobile:
        return jsonify({'error': 'Unauthorized'}), 401
    epic_no = epic_no.strip().upper()
    db  = _get_db()
    doc = db.generated_voters.find_one({"EPIC_NO": epic_no, "MOBILE_NO": mobile}, {"_id": 1})
    if not doc:
        return jsonify({'error': 'Forbidden'}), 403
    card_url = get_voter_card_url(epic_no)
    if card_url:
        return redirect(card_url)
    return jsonify({'error': 'Card not found.'}), 404


@app.route('/mycard/<epic_no>/download')
def user_download_card(epic_no):
    mobile = session.get('verified_mobile')
    if not mobile:
        return jsonify({'error': 'Unauthorized'}), 401
    epic_no = epic_no.strip().upper()
    db  = _get_db()
    doc = db.generated_voters.find_one({"EPIC_NO": epic_no, "MOBILE_NO": mobile}, {"_id": 1})
    if not doc:
        return jsonify({'error': 'Forbidden'}), 403
    card_url = get_voter_card_url(epic_no)
    if card_url:
        dl_url = card_url.replace('/upload/', f'/upload/fl_attachment:{epic_no}_VoterID/') \
                 if '/upload/' in card_url else card_url
        return redirect(dl_url)
    return jsonify({'error': 'Card not found.'}), 404


@app.route('/verify/<epic_no>')
def verify_voter(epic_no):
    epic_no = epic_no.strip().upper()
    voter   = find_voter_by_epic(epic_no)
    if not voter:
        flash('Voter ID not found.', 'danger')
        return redirect(url_for('user_home'))
    db      = _get_db()
    stat    = db.generation_stats.find_one({"epic_no": epic_no}) or {}
    gen_doc = db.generated_voters.find_one({"EPIC_NO": epic_no}) or {}
    vol_req = db.volunteer_requests.find_one({"epic_no": epic_no},
                                              sort=[("requested_at", DESCENDING)]) or {}
    ba_req  = db.booth_agent_requests.find_one({"epic_no": epic_no},
                                                sort=[("requested_at", DESCENDING)]) or {}
    voter['gen_count']      = stat.get('count', 0)
    voter['last_generated'] = stat.get('last_generated', '')
    voter['photo_url']      = stat.get('photo_url', gen_doc.get('photo_url', ''))
    voter['card_url']       = stat.get('card_url', gen_doc.get('card_url', ''))
    mobile = stat.get('auth_mobile', '')
    voter['auth_mobile_masked'] = f"****{mobile[-4:]}" if mobile and len(mobile) >= 4 else ''
    voter['ptc_code']            = gen_doc.get('ptc_code', '')
    voter['volunteer_status']    = vol_req.get('status', '')
    voter['booth_agent_status']  = ba_req.get('status', '')
    return render_template('user/verify.html', voter=voter)


@app.route('/refer/<ptc_code>/<referral_id>')
def referral_landing(ptc_code, referral_id):
    db  = _get_db()
    doc = db.generated_voters.find_one({"ptc_code": ptc_code, "referral_id": referral_id},
                                        {"FM_NAME_EN": 1, "LASTNAME_EN": 1})
    if not doc:
        flash('Invalid referral link.', 'danger')
        return redirect(url_for('user_home'))
    name         = ((doc.get('FM_NAME_EN') or '') + ' ' + (doc.get('LASTNAME_EN') or '')).strip()
    referrer_name= name or 'A We The Leaders Member'
    redirect_url = url_for('user_home') + f'?ref={ptc_code}&rid={referral_id}'
    banner_url   = f"{config.BASE_URL}/static/banner.jpg"
    html = (f'<!DOCTYPE html><html><head><meta charset="UTF-8">'
            f'<meta property="og:title" content="We The Leaders — Become a Member!">'
            f'<meta property="og:description" content="{referrer_name} invites you to join We The Leaders! Generate your free Digital Member ID Card now.">'
            f'<meta property="og:image" content="{banner_url}">'
            f'<meta property="og:url" content="{config.BASE_URL}/refer/{ptc_code}/{referral_id}">'
            f'<meta name="twitter:card" content="summary_large_image">'
            f'<meta name="twitter:title" content="We The Leaders — Become a Member!">'
            f'<meta name="twitter:image" content="{banner_url}">'
            f'<meta http-equiv="refresh" content="0;url={redirect_url}">'
            f'<title>We The Leaders — Join Now!</title>'
            f'</head><body style="font-family:sans-serif;text-align:center;padding:40px;">'
            f'<p>Redirecting to We The Leaders...</p>'
            f'<script>window.location.href="{redirect_url}";</script>'
            f'</body></html>')
    return html


# ── Chatbot Profile / Referral / Volunteer / Booth APIs ──────────

@app.route('/api/chat/profile', methods=['POST'])
def chat_profile():
    data    = request.get_json()
    epic_no = data.get('epic_no', '').strip().upper()
    mobile  = data.get('mobile', '').strip()
    if not epic_no:
        return jsonify({'success': False, 'message': 'EPIC required'}), 400
    voter = find_voter_by_epic(epic_no)
    if not voter:
        return jsonify({'success': False, 'message': 'Voter not found'}), 404
    db      = _get_db()
    gen_doc = db.generated_voters.find_one({"EPIC_NO": epic_no}) or {}
    stat    = db.generation_stats.find_one({"epic_no": epic_no}) or {}
    mob     = stat.get('auth_mobile', mobile)
    return jsonify({
        'success':    True,
        'name':       voter.get('name', ''),
        'epic_no':    epic_no,
        'assembly':   voter.get('assembly_name', ''),
        'district':   voter.get('district', ''),
        'ptc_code':   gen_doc.get('ptc_code', ''),
        'card_url':   stat.get('card_url') or gen_doc.get('card_url', ''),
        'photo_url':  stat.get('photo_url') or gen_doc.get('photo_url', ''),
        'auth_mobile_masked': f"****{mob[-4:]}" if mob and len(mob) >= 4 else '',
    })


@app.route('/api/chat/booth', methods=['POST'])
def chat_booth():
    data    = request.get_json()
    epic_no = data.get('epic_no', '').strip().upper()
    voter   = find_voter_by_epic(epic_no)
    if not voter:
        return jsonify({'success': False, 'message': 'Voter not found'}), 404
    return jsonify({'success': True,
                    'part_no': voter.get('part_no', ''),
                    'part_name': '',
                    'polling_station': ''})


@app.route('/api/chat/get-referral-link', methods=['POST'])
def chat_get_referral_link():
    data     = request.get_json()
    ptc_code = data.get('ptc_code', '').strip()
    if not ptc_code:
        return jsonify({'success': False, 'message': 'PTC code required'}), 400
    db  = _get_db()
    doc = db.generated_voters.find_one({"ptc_code": ptc_code}, {"referral_id": 1, "referral_link": 1})
    if not doc:
        return jsonify({'success': False, 'message': 'Member not found'}), 404
    if doc.get('referral_id'):
        return jsonify({'success': True, 'referral_id': doc['referral_id'], 'referral_link': doc['referral_link']})
    rid  = 'REF-' + uuid.uuid4().hex[:8].upper()
    link = f"{config.BASE_URL}/refer/{ptc_code}/{rid}"
    db.generated_voters.update_one({"ptc_code": ptc_code},
                                    {"$set": {"referral_id": rid, "referral_link": link}})
    return jsonify({'success': True, 'referral_id': rid, 'referral_link': link})


@app.route('/api/chat/my-members', methods=['POST'])
def chat_my_members():
    data     = request.get_json()
    ptc_code = data.get('ptc_code', '').strip()
    if not ptc_code:
        return jsonify({'success': False, 'message': 'PTC code required'}), 400
    db      = _get_db()
    members = list(db.generated_voters.find(
        {"referred_by_ptc": ptc_code},
        {"FM_NAME_EN": 1, "LASTNAME_EN": 1, "EPIC_NO": 1, "ptc_code": 1, "generated_at": 1}
    ).sort("generated_at", DESCENDING).limit(50))
    result = []
    for m in members:
        name = ((m.get('FM_NAME_EN') or '') + ' ' + (m.get('LASTNAME_EN') or '')).strip()
        result.append({'name': name, 'epic_no': m.get('EPIC_NO', ''), 'ptc_code': m.get('ptc_code', '')})
    return jsonify({'success': True, 'members': result, 'total': len(result)})


@app.route('/api/chat/request-volunteer', methods=['POST'])
def chat_request_volunteer():
    data    = request.get_json()
    ptc_code= data.get('ptc_code', '').strip()
    epic_no = data.get('epic_no', '').strip().upper()
    if not ptc_code:
        return jsonify({'success': False, 'message': 'PTC code required'}), 400
    db  = _get_db()
    gen = db.generated_voters.find_one({"ptc_code": ptc_code}) or {}
    name     = ((gen.get('FM_NAME_EN') or '') + ' ' + (gen.get('LASTNAME_EN') or '')).strip()
    existing = db.volunteer_requests.find_one({"ptc_code": ptc_code})
    if existing:
        return jsonify({'success': False, 'message': f'Already submitted. Status: {existing["status"]}'}), 400
    db.volunteer_requests.insert_one({
        "ptc_code": ptc_code, "epic_no": epic_no, "name": name,
        "mobile": gen.get('MOBILE_NO', ''),
        "assembly": gen.get('ASSEMBLY_NAME', ''),
        "district": gen.get('DISTRICT_NAME', ''),
        "status": "pending", "requested_at": datetime.now(timezone.utc),
    })
    return jsonify({'success': True, 'message': 'Volunteer request submitted!'})


@app.route('/api/chat/request-booth-agent', methods=['POST'])
def chat_request_booth_agent():
    data     = request.get_json()
    ptc_code = data.get('ptc_code', '').strip()
    epic_no  = data.get('epic_no', '').strip().upper()
    booth_no = data.get('booth_no', '').strip()
    if not ptc_code:
        return jsonify({'success': False, 'message': 'PTC code required'}), 400
    db  = _get_db()
    gen = db.generated_voters.find_one({"ptc_code": ptc_code}) or {}
    name     = ((gen.get('FM_NAME_EN') or '') + ' ' + (gen.get('LASTNAME_EN') or '')).strip()
    existing = db.booth_agent_requests.find_one({"ptc_code": ptc_code})
    if existing:
        return jsonify({'success': False, 'message': f'Already submitted. Status: {existing["status"]}'}), 400
    db.booth_agent_requests.insert_one({
        "ptc_code": ptc_code, "epic_no": epic_no, "name": name,
        "mobile": gen.get('MOBILE_NO', ''), "booth_no": booth_no,
        "assembly": gen.get('ASSEMBLY_NAME', ''),
        "district": gen.get('DISTRICT_NAME', ''),
        "status": "pending", "requested_at": datetime.now(timezone.utc),
    })
    return jsonify({'success': True, 'message': 'Booth agent request submitted!'})


@app.route('/api/whatsapp-channel')
def api_whatsapp_channel():
    url = os.getenv('WHATSAPP_CHANNEL_URL', '')
    if url:
        return redirect(url)
    return jsonify({'error': 'WhatsApp channel not configured'}), 404


# ══════════════════════════════════════════════════════════════════
#  ADMIN
# ══════════════════════════════════════════════════════════════════

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def require_admin_login():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin.login'))

@admin_bp.before_request
def before_admin():
    if request.endpoint not in ('admin.login',):
        if not session.get('admin_logged_in'):
            if request.is_json or request.path.startswith('/admin/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('admin.login'))


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    ip = request.remote_addr or 'unknown'
    locked, retry_after = login_tracker.is_locked(ip)
    if locked:
        flash(f'Too many failed attempts. Try again in {retry_after}s.', 'danger')
        return render_template('admin/login.html')
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD:
            session.clear()
            session['admin_logged_in'] = True
            session.permanent = True
            login_tracker.reset(ip)
            return redirect(url_for('admin.dashboard'))
        login_tracker.record_attempt(ip, username, False)
        flash('Invalid credentials.', 'danger')
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin.login'))


@admin_bp.route('/dashboard')
def dashboard():
    stats = get_dashboard_stats()
    return render_template('admin/dashboard.html', stats=stats)


@admin_bp.route('/voters')
def voters_list():
    assemblies, districts = _get_cached_dropdowns('voters', 'wtl:dropdown:voters')
    return render_template('admin/voters.html', voters=[], page=1,
                           total_pages=1, total=0, per_page=20,
                           search='', assemblies=assemblies, districts=districts)


@admin_bp.route('/api/stats')
def api_stats():
    return jsonify(get_dashboard_stats())


@admin_bp.route('/api/external-stats')
def api_external_stats():
    return jsonify(_get_external_stats())


@admin_bp.route('/voters/<epic_no>')
def voter_detail(epic_no):
    epic_no = epic_no.strip().upper()
    voter   = find_voter_by_epic(epic_no)
    if not voter:
        flash('Voter not found.', 'danger')
        return redirect(url_for('admin.voters_list'))
    db      = _get_db()
    stat    = db.generation_stats.find_one({"epic_no": epic_no}) or {}
    gen_doc = db.generated_voters.find_one({"EPIC_NO": epic_no}) or {}
    voter['gen_count']      = stat.get('count', 0)
    voter['last_generated'] = stat.get('last_generated', '')
    voter['photo_url']      = stat.get('photo_url', gen_doc.get('photo_url', ''))
    voter['card_url']       = stat.get('card_url', gen_doc.get('card_url', ''))
    mobile = stat.get('auth_mobile', '')
    voter['auth_mobile_masked'] = f"****{mobile[-4:]}" if mobile and len(mobile) >= 4 else ''
    voter['ptc_code']     = gen_doc.get('ptc_code', '')
    return render_template('admin/voter_detail.html', voter=voter)


@admin_bp.route('/api/voters')
@rate_limit(max_requests=30, window_seconds=60)
def api_voters():
    search   = sanitize_search(request.args.get('search', '').strip())
    assembly = request.args.get('assembly', '').strip()
    district = request.args.get('district', '').strip()
    page     = request.args.get('page', 1, type=int)
    per_page = min(max(request.args.get('per_page', 20, type=int), 5), 100)

    assemblies, districts = _get_cached_dropdowns('voters', 'wtl:dropdown:voters')

    db     = _get_db()
    filt   = {}
    if assembly:
        filt['ASSEMBLY_NAME'] = assembly
    if district:
        filt['DISTRICT_NAME'] = district
    if search:
        filt['$or'] = [
            {'EPIC_NO':    {'$regex': search, '$options': 'i'}},
            {'FM_NAME_EN': {'$regex': search, '$options': 'i'}},
            {'VOTER_NAME': {'$regex': search, '$options': 'i'}},
            {'LASTNAME_EN':{'$regex': search, '$options': 'i'}},
        ]

    total      = db.voters.count_documents(filt) if filt else db.voters.estimated_document_count()
    total_pages= max(1, (total + per_page - 1) // per_page)
    page       = min(page, total_pages)
    offset     = (page - 1) * per_page

    docs   = list(db.voters.find(filt).skip(offset).limit(per_page))
    voters = [_doc_to_voter(d) for d in docs]

    # Attach generation stats for this page
    epic_nos   = [v['epic_no'] for v in voters if v.get('epic_no')]
    stats_docs = {}
    if epic_nos:
        for s in db.generation_stats.find({"epic_no": {"$in": epic_nos}}):
            stats_docs[s['epic_no']] = s
    for v in voters:
        s = stats_docs.get(v.get('epic_no', ''), {})
        v['gen_count']     = s.get('count', 0)
        v['last_generated']= str(s.get('last_generated', '')) if s.get('last_generated') else ''
        v['photo_url']     = s.get('photo_url', '')
        v['card_url']      = s.get('card_url', '')
        v['auth_mobile']   = s.get('auth_mobile', '')

    return jsonify({'voters': voters, 'total': total, 'per_page': per_page,
                    'page': page, 'total_pages': total_pages,
                    'assemblies': assemblies, 'districts': districts,
                    'cursor_mode': False})


@admin_bp.route('/generated-voters')
def generated_voters_list():
    total = _get_db().generated_voters.estimated_document_count()
    return render_template('admin/generated_voters.html', voters=[], page=1,
                           total_pages=1, total=total, per_page=20, search='')


@admin_bp.route('/api/generated-voters')
@rate_limit(max_requests=30, window_seconds=60)
def api_generated_voters():
    search   = sanitize_search(request.args.get('search', '').strip())
    assembly = request.args.get('assembly', '').strip()
    page     = request.args.get('page', 1, type=int)
    per_page = min(max(request.args.get('per_page', 20, type=int), 5), 100)

    assemblies, districts = _get_cached_dropdowns('generated_voters', 'wtl:dropdown:gen_voters')

    db   = _get_db()
    filt = {}
    if assembly:
        filt['ASSEMBLY_NAME'] = assembly
    if search:
        filt['$or'] = [
            {'EPIC_NO':    {'$regex': search, '$options': 'i'}},
            {'FM_NAME_EN': {'$regex': search, '$options': 'i'}},
            {'LASTNAME_EN':{'$regex': search, '$options': 'i'}},
            {'ptc_code':   {'$regex': search, '$options': 'i'}},
            {'MOBILE_NO':  {'$regex': search, '$options': 'i'}},
        ]

    total      = db.generated_voters.count_documents(filt) if filt else db.generated_voters.estimated_document_count()
    total_pages= max(1, (total + per_page - 1) // per_page)
    page       = min(page, total_pages)
    offset     = (page - 1) * per_page

    docs   = list(db.generated_voters.find(filt).sort("generated_at", DESCENDING).skip(offset).limit(per_page))
    voters = [_gen_doc_to_dict(d) for d in docs]

    return jsonify({'voters': voters, 'total': total, 'page': page,
                    'per_page': per_page, 'total_pages': total_pages,
                    'assemblies': assemblies, 'districts': districts,
                    'cursor_mode': False})


@admin_bp.route('/generated-voters/<ptc_code>')
def generated_voter_detail(ptc_code):
    db    = _get_db()
    doc   = db.generated_voters.find_one({"ptc_code": ptc_code})
    voter = _gen_doc_to_dict(doc)
    if not voter:
        flash('Generated voter not found.', 'danger')
        return redirect(url_for('admin.generated_voters_list'))
    referred   = list(db.generated_voters.find({"referred_by_ptc": ptc_code}).sort("generated_at", DESCENDING))
    referred   = [_gen_doc_to_dict(r) for r in referred]
    vol_req    = db.volunteer_requests.find_one({"ptc_code": ptc_code})
    ba_req     = db.booth_agent_requests.find_one({"ptc_code": ptc_code})
    return render_template('admin/generated_voter_detail.html', voter=voter,
                           referred=referred, volunteer_req=vol_req, booth_agent_req=ba_req)


@admin_bp.route('/volunteer-requests')
def volunteer_requests_page():
    return render_template('admin/volunteer_requests.html')


@admin_bp.route('/api/volunteer-requests')
def api_volunteer_requests():
    search   = request.args.get('search', '').strip()
    status   = request.args.get('status', '').strip()
    page     = request.args.get('page', 1, type=int)
    per_page = min(max(request.args.get('per_page', 20, type=int), 5), 100)
    db       = _get_db()
    filt     = {}
    if status:
        filt['status'] = status
    if search:
        filt['$or'] = [{'name':     {'$regex': search, '$options': 'i'}},
                       {'ptc_code': {'$regex': search, '$options': 'i'}},
                       {'epic_no':  {'$regex': search, '$options': 'i'}},
                       {'mobile':   {'$regex': search, '$options': 'i'}}]
    total      = db.volunteer_requests.count_documents(filt)
    total_pages= max(1, (total + per_page - 1) // per_page)
    page       = min(page, total_pages)
    items = list(db.volunteer_requests.find(filt)
                   .sort("requested_at", DESCENDING)
                   .skip((page-1)*per_page).limit(per_page))
    for item in items:
        item['_id'] = str(item['_id'])
        if item.get('requested_at'):
            item['requested_at'] = str(item['requested_at'])
        if item.get('reviewed_at'):
            item['reviewed_at'] = str(item['reviewed_at'])
    return jsonify({'items': items, 'total': total, 'page': page,
                    'per_page': per_page, 'total_pages': total_pages})


@admin_bp.route('/api/volunteer-requests/<ptc_code>/confirm', methods=['POST'])
def confirm_volunteer(ptc_code):
    db = _get_db()
    r  = db.volunteer_requests.update_one(
        {"ptc_code": ptc_code, "status": "pending"},
        {"$set": {"status": "confirmed", "reviewed_at": datetime.now(timezone.utc), "reviewed_by": config.ADMIN_USERNAME}})
    return jsonify({'success': bool(r.modified_count)})


@admin_bp.route('/api/volunteer-requests/<ptc_code>/reject', methods=['POST'])
def reject_volunteer(ptc_code):
    db = _get_db()
    r  = db.volunteer_requests.update_one(
        {"ptc_code": ptc_code, "status": "pending"},
        {"$set": {"status": "rejected", "reviewed_at": datetime.now(timezone.utc), "reviewed_by": config.ADMIN_USERNAME}})
    return jsonify({'success': bool(r.modified_count)})


@admin_bp.route('/confirmed-volunteers')
def confirmed_volunteers_page():
    return render_template('admin/confirmed_volunteers.html')


@admin_bp.route('/api/confirmed-volunteers')
def api_confirmed_volunteers():
    search   = request.args.get('search', '').strip()
    page     = request.args.get('page', 1, type=int)
    per_page = min(max(request.args.get('per_page', 20, type=int), 5), 100)
    db       = _get_db()
    filt     = {"status": "confirmed"}
    if search:
        filt['$or'] = [{'name': {'$regex': search, '$options': 'i'}},
                       {'ptc_code': {'$regex': search, '$options': 'i'}}]
    total      = db.volunteer_requests.count_documents(filt)
    total_pages= max(1, (total + per_page - 1) // per_page)
    page       = min(page, total_pages)
    items = list(db.volunteer_requests.find(filt).sort("reviewed_at", DESCENDING)
                   .skip((page-1)*per_page).limit(per_page))
    for item in items:
        item['_id'] = str(item['_id'])
        if item.get('requested_at'): item['requested_at'] = str(item['requested_at'])
        if item.get('reviewed_at'):  item['reviewed_at']  = str(item['reviewed_at'])
    return jsonify({'items': items, 'total': total, 'page': page,
                    'per_page': per_page, 'total_pages': total_pages})


@admin_bp.route('/booth-agent-requests')
def booth_agent_requests_page():
    return render_template('admin/booth_agent_requests.html')


@admin_bp.route('/api/booth-agent-requests')
def api_booth_agent_requests():
    search   = request.args.get('search', '').strip()
    status   = request.args.get('status', '').strip()
    page     = request.args.get('page', 1, type=int)
    per_page = min(max(request.args.get('per_page', 20, type=int), 5), 100)
    db       = _get_db()
    filt     = {}
    if status:
        filt['status'] = status
    if search:
        filt['$or'] = [{'name':     {'$regex': search, '$options': 'i'}},
                       {'ptc_code': {'$regex': search, '$options': 'i'}},
                       {'epic_no':  {'$regex': search, '$options': 'i'}},
                       {'mobile':   {'$regex': search, '$options': 'i'}}]
    total      = db.booth_agent_requests.count_documents(filt)
    total_pages= max(1, (total + per_page - 1) // per_page)
    page       = min(page, total_pages)
    items = list(db.booth_agent_requests.find(filt).sort("requested_at", DESCENDING)
                   .skip((page-1)*per_page).limit(per_page))
    for item in items:
        item['_id'] = str(item['_id'])
        if item.get('requested_at'): item['requested_at'] = str(item['requested_at'])
        if item.get('reviewed_at'):  item['reviewed_at']  = str(item['reviewed_at'])
    return jsonify({'items': items, 'total': total, 'page': page,
                    'per_page': per_page, 'total_pages': total_pages})


@admin_bp.route('/api/booth-agent-requests/<ptc_code>/confirm', methods=['POST'])
def confirm_booth_agent(ptc_code):
    db = _get_db()
    r  = db.booth_agent_requests.update_one(
        {"ptc_code": ptc_code, "status": "pending"},
        {"$set": {"status": "confirmed", "reviewed_at": datetime.now(timezone.utc), "reviewed_by": config.ADMIN_USERNAME}})
    return jsonify({'success': bool(r.modified_count)})


@admin_bp.route('/api/booth-agent-requests/<ptc_code>/reject', methods=['POST'])
def reject_booth_agent(ptc_code):
    db = _get_db()
    r  = db.booth_agent_requests.update_one(
        {"ptc_code": ptc_code, "status": "pending"},
        {"$set": {"status": "rejected", "reviewed_at": datetime.now(timezone.utc), "reviewed_by": config.ADMIN_USERNAME}})
    return jsonify({'success': bool(r.modified_count)})


@admin_bp.route('/confirmed-booth-agents')
def confirmed_booth_agents_page():
    return render_template('admin/confirmed_booth_agents.html')


@admin_bp.route('/api/confirmed-booth-agents')
def api_confirmed_booth_agents():
    search   = request.args.get('search', '').strip()
    page     = request.args.get('page', 1, type=int)
    per_page = min(max(request.args.get('per_page', 20, type=int), 5), 100)
    db       = _get_db()
    filt     = {"status": "confirmed"}
    if search:
        filt['$or'] = [{'name': {'$regex': search, '$options': 'i'}},
                       {'ptc_code': {'$regex': search, '$options': 'i'}}]
    total      = db.booth_agent_requests.count_documents(filt)
    total_pages= max(1, (total + per_page - 1) // per_page)
    page       = min(page, total_pages)
    items = list(db.booth_agent_requests.find(filt).sort("reviewed_at", DESCENDING)
                   .skip((page-1)*per_page).limit(per_page))
    for item in items:
        item['_id'] = str(item['_id'])
        if item.get('requested_at'): item['requested_at'] = str(item['requested_at'])
        if item.get('reviewed_at'):  item['reviewed_at']  = str(item['reviewed_at'])
    return jsonify({'items': items, 'total': total, 'page': page,
                    'per_page': per_page, 'total_pages': total_pages})


# ── Register blueprints ───────────────────────────────────────────
app.register_blueprint(admin_bp)
app.register_blueprint(health_bp)

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_ENV') == 'development', host='0.0.0.0', port=5000)
