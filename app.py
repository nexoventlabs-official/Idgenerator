"""
Voter ID Card Generator v4.0 — Web App
========================================
- User  : /       Enter Epic Number + optional photo → Generate & Download Card
- Admin : /admin  Import XLSX/CSV, view voters & generation stats

Database : MongoDB Atlas
Photos   : Cloudinary (user uploads)
Cards    : Cloudinary (generated_cards folder)
"""

import csv
import io
import json
import os
import random
import sys
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
from pymongo import MongoClient

import string

import config
from generate_cards import (
    setup_logging, generate_card, generate_serial_number,
    load_bold_font, get_text_width, load_member_photo
)

# ── App Setup ────────────────────────────────────────────────────
app = Flask(__name__,
            template_folder=os.path.join(config.BASE_DIR, 'templates'),
            static_folder=os.path.join(config.BASE_DIR, 'static'))
app.secret_key = os.getenv('FLASK_SECRET', 'voter-id-gen-secret-2026')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB max upload

for d in [config.MEMBER_PHOTOS_DIR, config.DATA_DIR, config.UPLOADS_DIR]:
    os.makedirs(d, exist_ok=True)

ALLOWED_IMG = {'png', 'jpg', 'jpeg', 'bmp'}
ALLOWED_DATA = {'xlsx', 'xls', 'csv'}

logger = setup_logging()

# ── MongoDB Setup (Main DB — voter data from XLSX imports) ───────
mongo_client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)
db = mongo_client[config.MONGO_DB_NAME]
voters_col = db[config.MONGO_VOTERS_COLLECTION]
stats_col = db[config.MONGO_STATS_COLLECTION]
verified_mobiles_col = db['verified_mobiles']
otp_col = db['otp_sessions']

# ── MongoDB Setup (Generated Voters DB — cards generated via chatbot) ──
gen_mongo_client = MongoClient(config.GEN_MONGO_URI, serverSelectionTimeoutMS=5000)
gen_db = gen_mongo_client[config.GEN_MONGO_DB_NAME]
gen_voters_col = gen_db[config.GEN_MONGO_COLLECTION]

# Ensure indexes (graceful — don't crash if Atlas is unreachable)
try:
    voters_col.create_index('epic_no', unique=True)
    stats_col.create_index('epic_no', unique=True)
    gen_voters_col.create_index('ptc_code', unique=True)
    gen_voters_col.create_index('epic_no')
    gen_voters_col.create_index('mobile')
    logger.info("MongoDB connected & indexes ensured (both DBs).")
except Exception as e:
    logger.warning(f"MongoDB index creation skipped: {e}")

# ── Cloudinary Setup ─────────────────────────────────────────────
cloudinary.config(
    cloud_name=config.CLOUDINARY_CLOUD_NAME,
    api_key=config.CLOUDINARY_API_KEY,
    api_secret=config.CLOUDINARY_API_SECRET,
    secure=True,
)



# ══════════════════════════════════════════════════════════════════
#  FILE PARSING HELPERS (XLSX / CSV → list of dicts)
# ══════════════════════════════════════════════════════════════════

def _match_column(header: str, candidates: list[str]) -> bool:
    h = header.strip().lower()
    for c in candidates:
        if c.lower() == h or c.lower() in h or h in c.lower():
            return True
    return False


def _safe_str(val):
    """Convert cell value to clean string, return '' for None."""
    if val is None:
        return ''
    return str(val).strip()


def _parse_xlsx(xlsx_path: str) -> list[dict]:
    """Parse an XLSX file into a list of voter dicts with ALL columns."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.active

    headers = []
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        headers.append(str(cell.value).strip() if cell.value else '')

    col_map = {}
    for field, candidates in config.XLSX_COLUMNS.items():
        for idx, h in enumerate(headers):
            if _match_column(h, candidates):
                col_map[field] = idx
                break

    # Indices already used for mapped fields
    mapped_indices = set(col_map.values())

    voters = []
    seen_epics = set()
    for row in ws.iter_rows(min_row=2):
        cells = [cell.value for cell in row]
        epic = _safe_str(cells[col_map['epic_no']] if 'epic_no' in col_map and col_map['epic_no'] < len(cells) else '')
        name = _safe_str(cells[col_map['name']] if 'name' in col_map and col_map['name'] < len(cells) else '')

        if not epic:
            continue

        # Skip duplicate EPIC numbers within the same file
        epic_upper = epic.strip().upper()
        if epic_upper in seen_epics:
            continue
        seen_epics.add(epic_upper)

        assembly = _safe_str(cells[col_map['assembly']] if 'assembly' in col_map and col_map['assembly'] < len(cells) else '') if 'assembly' in col_map else ''
        district = _safe_str(cells[col_map['district']] if 'district' in col_map and col_map['district'] < len(cells) else '') if 'district' in col_map else ''

        voter = {
            'epic_no': epic,
            'name': name,
            'assembly': assembly,
            'district': district,
        }

        # Store ALL remaining columns as extra fields
        for idx, h in enumerate(headers):
            if idx not in mapped_indices and h:
                key = h.replace(' ', '_').lower()
                val = _safe_str(cells[idx] if idx < len(cells) else '')
                if val:
                    voter[key] = val

        voters.append(voter)

    wb.close()
    return voters


def _parse_csv_bytes(raw: bytes) -> list[dict]:
    """Parse CSV bytes into voter dicts with ALL columns."""
    for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode('latin-1')

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 2:
        return []

    headers = [h.strip() for h in rows[0]]
    col_map = {}
    for field, candidates in config.XLSX_COLUMNS.items():
        for idx, h in enumerate(headers):
            if _match_column(h, candidates):
                col_map[field] = idx
                break

    mapped_indices = set(col_map.values())

    voters = []
    seen_epics = set()
    for cells in rows[1:]:
        epic = _safe_str(cells[col_map['epic_no']] if 'epic_no' in col_map and col_map['epic_no'] < len(cells) else '')
        name = _safe_str(cells[col_map['name']] if 'name' in col_map and col_map['name'] < len(cells) else '')
        if not epic:
            continue

        # Skip duplicate EPIC numbers within the same file
        epic_upper = epic.strip().upper()
        if epic_upper in seen_epics:
            continue
        seen_epics.add(epic_upper)
        assembly = _safe_str(cells[col_map['assembly']] if 'assembly' in col_map and col_map['assembly'] < len(cells) else '') if 'assembly' in col_map else ''
        district = _safe_str(cells[col_map['district']] if 'district' in col_map and col_map['district'] < len(cells) else '') if 'district' in col_map else ''

        voter = {'epic_no': epic, 'name': name, 'assembly': assembly, 'district': district}

        # Store ALL remaining columns as extra fields
        for idx, h in enumerate(headers):
            if idx not in mapped_indices and h:
                key = h.replace(' ', '_').lower()
                val = _safe_str(cells[idx] if idx < len(cells) else '')
                if val:
                    voter[key] = val

        voters.append(voter)
    return voters


# ══════════════════════════════════════════════════════════════════
#  MONGODB DATABASE HELPERS
# ══════════════════════════════════════════════════════════════════

def load_voters_from_db() -> list[dict]:
    """Return all voters from MongoDB."""
    return list(voters_col.find({}, {'_id': 0}))


def find_voter_by_epic(epic_no: str) -> dict | None:
    epic_no = epic_no.strip().upper()
    doc = voters_col.find_one({'epic_no': epic_no}, {'_id': 0})
    return doc


def upsert_voters(voter_list: list[dict]) -> int:
    """Bulk-upsert voters into MongoDB. Returns count inserted/updated."""
    from pymongo import UpdateOne
    if not voter_list:
        return 0
    ops = []
    for v in voter_list:
        set_data = {k: val for k, val in v.items() if k != '_id'}
        ops.append(UpdateOne(
            {'epic_no': v['epic_no']},
            {'$set': set_data},
            upsert=True
        ))
    result = voters_col.bulk_write(ops)
    return result.upserted_count + result.modified_count


def replace_all_voters(voter_list: list[dict]) -> int:
    """Drop existing voters and insert fresh set."""
    voters_col.delete_many({})
    if voter_list:
        # Preserve any existing photo_url from stats
        for v in voter_list:
            existing_stat = stats_col.find_one({'epic_no': v['epic_no']}, {'photo_url': 1})
            if existing_stat and existing_stat.get('photo_url'):
                v['photo_url'] = existing_stat['photo_url']
        voters_col.insert_many(voter_list)
    return len(voter_list)


# ══════════════════════════════════════════════════════════════════
#  GENERATION STATS (MongoDB)
# ══════════════════════════════════════════════════════════════════

def generate_ptc_code() -> str:
    """Generate a unique PTC-XXXXXXX code (7 alphanumeric chars, uppercase)."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(100):  # max retries to avoid infinite loop
        code = 'PTC-' + ''.join(random.choices(chars, k=7))
        if not gen_voters_col.find_one({'ptc_code': code}):
            return code
    # Fallback: use timestamp-based
    ts = datetime.now(timezone.utc).strftime('%y%m%d%H%M%S%f')[:7]
    return f'PTC-{ts}'


def save_generated_voter(voter: dict, mobile: str, photo_url: str, card_url: str, ptc_code: str):
    """Save a generated voter record to the new Generated Voters DB."""
    doc = {
        'ptc_code': ptc_code,
        'epic_no': voter.get('epic_no', ''),
        'name': voter.get('name', ''),
        'assembly': voter.get('assembly', ''),
        'district': voter.get('district', ''),
        'mobile': mobile,
        'photo_url': photo_url,
        'card_url': card_url,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
    gen_voters_col.update_one(
        {'epic_no': voter.get('epic_no', ''), 'mobile': mobile},
        {'$set': doc},
        upsert=True
    )


def increment_generation_count(epic_no: str, photo_url: str = '', card_url: str = '', auth_mobile: str = ''):
    """Increment generation count; optionally update photo_url, card_url, auth_mobile."""
    update = {
        '$inc': {'count': 1},
        '$set': {'last_generated': datetime.now(timezone.utc).isoformat()},
        '$setOnInsert': {'epic_no': epic_no},
    }
    if photo_url:
        update['$set']['photo_url'] = photo_url
    if card_url:
        update['$set']['card_url'] = card_url
    if auth_mobile:
        update['$set']['auth_mobile'] = auth_mobile
    stats_col.update_one({'epic_no': epic_no}, update, upsert=True)


def get_voter_gen_count(epic_no: str) -> int:
    doc = stats_col.find_one({'epic_no': epic_no}, {'count': 1})
    return doc.get('count', 0) if doc else 0


def get_voter_photo_url(epic_no: str) -> str:
    """Get Cloudinary photo URL for a voter (from stats collection)."""
    doc = stats_col.find_one({'epic_no': epic_no}, {'photo_url': 1})
    return doc.get('photo_url', '') if doc else ''


def get_voter_card_url(epic_no: str) -> str:
    """Get Cloudinary card URL for a voter."""
    doc = stats_col.find_one({'epic_no': epic_no}, {'card_url': 1})
    return doc.get('card_url', '') if doc else ''


def get_all_stats() -> dict:
    """Return {epic_no: {count, last_generated, photo_url, card_url, auth_mobile}} dict."""
    result = {}
    for doc in stats_col.find({}, {'_id': 0}):
        result[doc['epic_no']] = {
            'count': doc.get('count', 0),
            'last_generated': doc.get('last_generated', ''),
            'photo_url': doc.get('photo_url', ''),
            'card_url': doc.get('card_url', ''),
            'auth_mobile': doc.get('auth_mobile', ''),
        }
    return result


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
    """Upload generated card image to Cloudinary. Replaces any existing card."""
    safe_id = epic_no.replace('/', '_').replace('\\', '_')

    # Delete old card first (if exists) to ensure a clean replacement
    try:
        public_id = f"{config.CLOUDINARY_CARDS_FOLDER}/{safe_id}"
        cloudinary.uploader.destroy(public_id, resource_type='image', invalidate=True)
        logger.info(f"Deleted old card from Cloudinary: {public_id}")
    except Exception:
        pass  # No old card exists, that's fine

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


def get_dashboard_stats():
    try:
        total_voters = voters_col.count_documents({})
        all_stats = get_all_stats()
        total_generated = sum(1 for s in all_stats.values() if s['count'] > 0)
        total_generations = sum(s['count'] for s in all_stats.values())
        cards_on_cloud = sum(1 for s in all_stats.values() if s.get('card_url'))
        db_connected = True

        # Generated voters count from new DB
        try:
            generated_voters_count = gen_voters_col.count_documents({})
        except Exception:
            generated_voters_count = 0
        
        # MongoDB Storage
        db_stats = db.command("dbstats")
        mongodb_size_mb = round(db_stats.get("dataSize", 0) / (1024 * 1024), 2)
        
        # Cloudinary Quota
        cloudinary_credits = "N/A"
        try:
            cli_usage = cloudinary.api.usage()
            used = cli_usage.get('credits', {}).get('usage', 0)
            cloudinary_credits = f"{round(used, 2)}"
        except Exception:
            pass
            
        # 2Factor SMS Balance
        sms_balance = "N/A"
        sms_api_key = os.getenv('SMS_API_KEY', '')
        if sms_api_key:
            try:
                resp = http_requests.get(f"https://2factor.in/API/V1/{sms_api_key}/BAL/SMS", timeout=5)
                if resp.status_code == 200:
                    sms_balance = resp.json().get('Details', 'N/A')
            except Exception:
                pass
                
    except Exception:
        total_voters = 0
        total_generated = 0
        total_generations = 0
        cards_on_cloud = 0
        generated_voters_count = 0
        db_connected = False
        mongodb_size_mb = 0
        cloudinary_credits = "N/A"
        sms_balance = "N/A"

    return {
        'total_voters': total_voters,
        'total_generated': total_generated,
        'total_generations': total_generations,
        'cards_on_cloud': cards_on_cloud,
        'generated_voters_count': generated_voters_count,
        'db_connected': db_connected,
        'mongodb_size_mb': mongodb_size_mb,
        'cloudinary_credits': cloudinary_credits,
        'sms_balance': sms_balance
    }


# ══════════════════════════════════════════════════════════════════
#  PUBLIC USER ROUTES  (/)
# ══════════════════════════════════════════════════════════════════

@app.route('/')
def user_home():
    resp = app.make_response(render_template('user/chatbot.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ── Chatbot API Endpoints ────────────────────────────────────────

@app.route('/api/chat/check-mobile', methods=['POST'])
def chat_check_mobile():
    """Check if mobile number has a previously generated card."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    # Check if this mobile has a linked card in stats
    stat = stats_col.find_one({'auth_mobile': mobile}, {'epic_no': 1, 'card_url': 1})
    if stat and stat.get('card_url'):
        return jsonify({
            'has_card': True,
            'epic_no': stat.get('epic_no', ''),
            'card_url': stat.get('card_url', '')
        })
    return jsonify({'has_card': False})


@app.route('/api/chat/send-otp', methods=['POST'])
def chat_send_otp():
    """Generate and send OTP to mobile number via 2Factor.in voice call."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    if not mobile or len(mobile) != 10:
        return jsonify({'success': False, 'message': 'Invalid mobile number'}), 400

    otp = str(random.randint(100000, 999999))

    # Store OTP in DB
    otp_col.update_one(
        {'mobile': mobile},
        {'$set': {
            'otp': otp,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'verified': False,
        }},
        upsert=True
    )

    # Send OTP via 2Factor.in voice call
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
                logger.info(f"OTP call sent to {mobile}")
        except Exception as e:
            logger.warning(f"OTP send failed for {mobile}: {e}")

    if not otp_sent:
        logger.warning(f"OTP not sent for {mobile}")

    return jsonify({'success': otp_sent})


@app.route('/api/chat/verify-otp', methods=['POST'])
def chat_verify_otp():
    """Verify OTP for mobile number."""
    data = request.get_json()
    mobile = data.get('mobile', '').strip()
    otp = data.get('otp', '').strip()

    if not mobile or not otp:
        return jsonify({'success': False, 'message': 'Mobile and OTP required'}), 400

    doc = otp_col.find_one({'mobile': mobile})
    if not doc or doc.get('otp') != otp:
        return jsonify({'success': False, 'message': 'Invalid OTP'}), 400

    # Check expiry (5 minutes)
    try:
        created = datetime.fromisoformat(doc['created_at'])
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            return jsonify({'success': False, 'message': 'OTP expired. Please request a new one.'}), 400
    except Exception:
        pass

    # Mark OTP as verified (but don't mark mobile as verified yet — that happens after card generation)
    otp_col.update_one({'mobile': mobile}, {'$set': {'verified': True}})

    # Check if this mobile already has a linked card
    stat = stats_col.find_one({'auth_mobile': mobile}, {'epic_no': 1, 'card_url': 1})
    if stat and stat.get('card_url'):
        return jsonify({
            'success': True,
            'has_card': True,
            'epic_no': stat.get('epic_no', ''),
            'card_url': stat.get('card_url', '')
        })

    return jsonify({'success': True, 'has_card': False})


@app.route('/api/chat/validate-epic', methods=['POST'])
def chat_validate_epic():
    """Validate EPIC number and return voter details."""
    data = request.get_json()
    epic_no = data.get('epic_no', '').strip().upper()
    if not epic_no:
        return jsonify({'success': False, 'message': 'Please enter your EPIC Number.'}), 400

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
def chat_generate_card():
    """Upload photo and generate ID card via chatbot."""
    epic_no = request.form.get('epic_no', '').strip().upper()
    mobile_num = request.form.get('mobile', '').strip()

    if not epic_no:
        return jsonify({'success': False, 'message': 'EPIC Number is required.'}), 400

    voter = find_voter_by_epic(epic_no)
    if not voter:
        return jsonify({'success': False, 'message': 'EPIC Number not found.'}), 404

    # Photo is required
    if 'photo' not in request.files or not request.files['photo'].filename:
        return jsonify({'success': False, 'message': 'Photo is required.'}), 400

    file = request.files['photo']
    if not allowed_file(file.filename, ALLOWED_IMG):
        return jsonify({'success': False, 'message': 'Invalid photo format. Use JPG or PNG.'}), 400

    photo_image = None
    photo_url = ''
    try:
        photo_image = Image.open(file.stream).convert('RGB')
        photo_url = upload_photo_to_cloudinary(photo_image, epic_no)
    except Exception as e:
        logger.warning(f"Photo upload error for {epic_no}: {e}")
        return jsonify({'success': False, 'message': 'Could not process the uploaded photo.'}), 500

    try:
        # Generate unique PTC code for this registration
        ptc_code = generate_ptc_code()

        voter['serial_number'] = ptc_code
        voter['verify_url'] = f"{config.BASE_URL}/verify/{epic_no}"
        voter['auth_mobile'] = mobile_num
        template = Image.open(config.TEMPLATE_PATH).convert('RGBA')
        card_image = generate_card(voter, template, photo_image=photo_image)
        card_url = upload_card_to_cloudinary(card_image, epic_no)

        increment_generation_count(epic_no, photo_url=photo_url, card_url=card_url, auth_mobile=mobile_num)

        # Save to Generated Voters DB (new MongoDB)
        save_generated_voter(voter, mobile_num, photo_url, card_url, ptc_code)

        # Now mark mobile as verified (only after successful card generation)
        if mobile_num:
            verified_mobiles_col.update_one(
                {'mobile': mobile_num},
                {'$set': {'mobile': mobile_num, 'epic_no': epic_no, 'verified_at': datetime.now(timezone.utc).isoformat()}},
                upsert=True
            )

        return jsonify({'success': True, 'card_url': card_url, 'epic_no': epic_no, 'ptc_code': ptc_code})
    except Exception as e:
        logger.error(f"Card generation error for {epic_no}: {e}")
        return jsonify({'success': False, 'message': 'Card generation failed. Please try again.'}), 500


@app.route('/card/<epic_no>')
def user_card_page(epic_no):
    """GET page showing generated card — safe to refresh (no re-generation)."""
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
    """Redirect to the Cloudinary-hosted card image."""
    card_url = get_voter_card_url(epic_no)
    if card_url:
        return redirect(card_url)
    flash('Card not found.', 'danger')
    return redirect(url_for('user_home'))


@app.route('/mycard/<epic_no>/download')
def user_download_card(epic_no):
    """Redirect to Cloudinary card with download flag."""
    card_url = get_voter_card_url(epic_no)
    if card_url:
        # Add Cloudinary fl_attachment transformation for download
        if '/upload/' in card_url:
            dl_url = card_url.replace('/upload/', f'/upload/fl_attachment:{epic_no}_VoterID/')
        else:
            dl_url = card_url
        return redirect(dl_url)
    flash('Card not found.', 'danger')
    return redirect(url_for('user_home'))


@app.route('/verify/<epic_no>')
def verify_voter(epic_no):
    """Public verification page — opened when QR code is scanned on mobile."""
    epic_no = epic_no.strip().upper()
    voter = find_voter_by_epic(epic_no)
    if not voter:
        flash('Voter ID not found in database.', 'danger')
        return redirect(url_for('user_home'))

    # Attach stats
    s = stats_col.find_one({'epic_no': epic_no}, {'_id': 0}) or {}
    voter['gen_count'] = s.get('count', 0)
    voter['last_generated'] = s.get('last_generated', '')
    voter['photo_url'] = s.get('photo_url', '')
    voter['card_url'] = s.get('card_url', '')
    voter['auth_mobile'] = s.get('auth_mobile', '')

    # Attach PTC code from generated voters DB
    gen_doc = gen_voters_col.find_one({'epic_no': epic_no}, {'ptc_code': 1})
    voter['ptc_code'] = gen_doc.get('ptc_code', '') if gen_doc else ''

    # Separate core fields from extra fields
    core_keys = {'epic_no', 'name', 'assembly', 'district', 'gen_count',
                 'last_generated', 'photo_url', 'card_url', 'serial_number',
                 'verify_url', 'auth_mobile', 'ptc_code'}
    extra_fields = {k: v for k, v in voter.items() if k not in core_keys and v}

    return render_template('user/verify.html',
                           voter=voter,
                           extra_fields=extra_fields)


# ══════════════════════════════════════════════════════════════════
#  ADMIN BLUEPRINT  (/admin)
# ══════════════════════════════════════════════════════════════════

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ── Admin Authentication ─────────────────────────────────────────
@admin_bp.before_request
def require_admin_login():
    """Guard all /admin routes — redirect to login if not authenticated."""
    if request.endpoint == 'admin.login':
        return  # allow access to the login page itself
    if not session.get('admin_logged_in'):
        flash('Please log in to access the admin panel.', 'warning')
        return redirect(url_for('admin.login', next=request.url))


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session.permanent = True
            flash('Welcome back, Admin!', 'success')
            next_url = request.args.get('next') or url_for('admin.dashboard')
            return redirect(next_url)
        else:
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
def voters_list():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)
    search = request.args.get('search', '').strip()
    filter_assembly = request.args.get('assembly', '').strip()
    filter_district = request.args.get('district', '').strip()

    # Get voters from MongoDB
    voters = load_voters_from_db()
    all_stats = get_all_stats()

    # Collect unique assemblies & districts for filter dropdowns
    assemblies = sorted({v.get('assembly', '') for v in voters if v.get('assembly', '')})
    districts = sorted({v.get('district', '') for v in voters if v.get('district', '')})

    # Attach generation count + photo URL + card URL + auth mobile
    for v in voters:
        epic = v['epic_no']
        s = all_stats.get(epic, {})
        v['gen_count'] = s.get('count', 0)
        v['last_generated'] = s.get('last_generated', '')
        v['photo_url'] = s.get('photo_url', '')
        v['card_url'] = s.get('card_url', '')
        v['auth_mobile'] = s.get('auth_mobile', '')

    # Apply assembly/district filters
    if filter_assembly:
        voters = [v for v in voters if v.get('assembly', '') == filter_assembly]
    if filter_district:
        voters = [v for v in voters if v.get('district', '') == filter_district]

    # Search filter
    if search:
        sl = search.lower()
        voters = [v for v in voters if
                  sl in v.get('epic_no', '').lower() or
                  sl in v.get('name', '').lower() or
                  sl in v.get('assembly', '').lower() or
                  sl in v.get('district', '').lower()]

    total = len(voters)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    voters_page = voters[(page - 1) * per_page: page * per_page]

    return render_template('admin/voters.html',
                           voters=voters_page, page=page,
                           total_pages=total_pages, total=total,
                           per_page=per_page, search=search,
                           assemblies=assemblies, districts=districts,
                           filter_assembly=filter_assembly,
                           filter_district=filter_district)


@admin_bp.route('/import', methods=['GET', 'POST'])
def import_xlsx():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected.', 'danger')
            return redirect(url_for('admin.import_xlsx'))

        file = request.files['file']
        if not file or not file.filename:
            flash('No file selected.', 'danger')
            return redirect(url_for('admin.import_xlsx'))

        if not allowed_file(file.filename, ALLOWED_DATA):
            flash('Only .xlsx, .xls, and .csv files are accepted.', 'danger')
            return redirect(url_for('admin.import_xlsx'))

        ext = file.filename.rsplit('.', 1)[1].lower()

        try:
            if ext == 'csv':
                raw = file.stream.read()
                voter_list = _parse_csv_bytes(raw)
            else:
                # Save XLSX temporarily for openpyxl to read
                os.makedirs(config.DATA_DIR, exist_ok=True)
                file.save(config.VOTERS_XLSX)
                voter_list = _parse_xlsx(config.VOTERS_XLSX)

            if not voter_list:
                flash('No voter records found in the file. Check column headers.', 'warning')
                return redirect(url_for('admin.import_xlsx'))

            # Check import mode
            import_mode = request.form.get('import_mode', 'merge')

            if import_mode == 'replace':
                # Replace all — drop existing and insert fresh
                count = replace_all_voters(voter_list)
                flash(f'Data replaced successfully! {count} unique voter records stored.', 'success')
            else:
                # Merge — upsert (update existing, insert new, no duplicates)
                existing_count = voters_col.count_documents({})
                count = upsert_voters(voter_list)
                new_total = voters_col.count_documents({})
                new_added = new_total - existing_count
                updated = count - new_added if count > new_added else 0
                flash(
                    f'Import merged! {len(voter_list)} records processed — '
                    f'{new_added} new added, {updated} updated, '
                    f'{new_total} total in database.',
                    'success'
                )

        except Exception as e:
            flash(f'Could not process file: {e}', 'danger')
            return redirect(url_for('admin.import_xlsx'))

        return redirect(url_for('admin.dashboard'))

    # GET — show current status
    try:
        voters_count = voters_col.count_documents({})
    except Exception:
        voters_count = 0
    return render_template('admin/import.html', voters_count=voters_count)


@admin_bp.route('/api/stats')
def api_stats():
    return jsonify(get_dashboard_stats())


@admin_bp.route('/voters/<epic_no>')
def voter_detail(epic_no):
    """Show full details for a single voter."""
    voter = find_voter_by_epic(epic_no)
    if not voter:
        flash('Voter not found.', 'danger')
        return redirect(url_for('admin.voters_list'))

    # Attach stats
    s = stats_col.find_one({'epic_no': epic_no}, {'_id': 0}) or {}
    voter['gen_count'] = s.get('count', 0)
    voter['last_generated'] = s.get('last_generated', '')
    voter['photo_url'] = s.get('photo_url', '')
    voter['card_url'] = s.get('card_url', '')
    voter['auth_mobile'] = s.get('auth_mobile', '')

    # Separate core fields from extra fields for display
    core_keys = {'epic_no', 'name', 'assembly', 'district', 'gen_count',
                 'last_generated', 'photo_url', 'card_url', 'auth_mobile'}
    extra_fields = {k: v for k, v in voter.items() if k not in core_keys}

    return render_template('admin/voter_detail.html',
                           voter=voter, extra_fields=extra_fields)

@admin_bp.route('/api/voters')
def api_voters():
    """JSON API: search, filter, paginate voters."""
    search = request.args.get('search', '').strip()
    assembly = request.args.get('assembly', '').strip()
    district = request.args.get('district', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    voters = load_voters_from_db()
    all_stats = get_all_stats()

    for v in voters:
        s = all_stats.get(v['epic_no'], {})
        v['gen_count'] = s.get('count', 0)
        v['last_generated'] = s.get('last_generated', '')
        v['photo_url'] = s.get('photo_url', '')
        v['card_url'] = s.get('card_url', '')
        v['auth_mobile'] = s.get('auth_mobile', '')

    # Apply filters
    if assembly:
        voters = [v for v in voters if v.get('assembly', '') == assembly]
    if district:
        voters = [v for v in voters if v.get('district', '') == district]
    if search:
        sl = search.lower()
        voters = [v for v in voters if
                  sl in v.get('epic_no', '').lower() or
                  sl in v.get('name', '').lower() or
                  sl in v.get('assembly', '').lower() or
                  sl in v.get('district', '').lower()]

    total = len(voters)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    voters_page = voters[(page - 1) * per_page: page * per_page]

    return jsonify({
        'voters': voters_page,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
    })


# ── Generated Voters (from new DB) ──────────────────────────────

@admin_bp.route('/generated-voters')
def generated_voters_list():
    """Show all voters who generated ID cards via the chatbot."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)
    search = request.args.get('search', '').strip()

    voters = list(gen_voters_col.find({}, {'_id': 0}).sort('generated_at', -1))

    # Search filter
    if search:
        sl = search.lower()
        voters = [v for v in voters if
                  sl in v.get('epic_no', '').lower() or
                  sl in v.get('name', '').lower() or
                  sl in v.get('ptc_code', '').lower() or
                  sl in v.get('mobile', '').lower() or
                  sl in v.get('assembly', '').lower() or
                  sl in v.get('district', '').lower()]

    total = len(voters)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    voters_page = voters[(page - 1) * per_page: page * per_page]

    return render_template('admin/generated_voters.html',
                           voters=voters_page, page=page,
                           total_pages=total_pages, total=total,
                           per_page=per_page, search=search)


@admin_bp.route('/api/generated-voters')
def api_generated_voters():
    """JSON API for generated voters list."""
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    per_page = min(max(per_page, 5), 100)

    voters = list(gen_voters_col.find({}, {'_id': 0}).sort('generated_at', -1))

    if search:
        sl = search.lower()
        voters = [v for v in voters if
                  sl in v.get('epic_no', '').lower() or
                  sl in v.get('name', '').lower() or
                  sl in v.get('ptc_code', '').lower() or
                  sl in v.get('mobile', '').lower() or
                  sl in v.get('assembly', '').lower() or
                  sl in v.get('district', '').lower()]

    total = len(voters)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    voters_page = voters[(page - 1) * per_page: page * per_page]

    return jsonify({
        'voters': voters_page,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
    })


# Register admin blueprint
app.register_blueprint(admin_bp)


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
    print("  Database: MongoDB Atlas | Photos: Cloudinary")
    print("=" * 60)

    app.run(host=args.host, port=args.port, debug=args.debug)
