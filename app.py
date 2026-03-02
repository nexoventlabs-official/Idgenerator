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

# ── MongoDB Setup ────────────────────────────────────────────────
mongo_client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)
db = mongo_client[config.MONGO_DB_NAME]
voters_col = db[config.MONGO_VOTERS_COLLECTION]
stats_col = db[config.MONGO_STATS_COLLECTION]
photos_col = db['photos']
videos_col = db['videos']
announcements_col = db['announcements']

# Ensure indexes (graceful — don't crash if Atlas is unreachable)
try:
    voters_col.create_index('epic_no', unique=True)
    stats_col.create_index('epic_no', unique=True)
    logger.info("MongoDB connected & indexes ensured.")
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

def increment_generation_count(epic_no: str, photo_url: str = '', card_url: str = ''):
    """Increment generation count; optionally update photo_url and card_url."""
    update = {
        '$inc': {'count': 1},
        '$set': {'last_generated': datetime.now(timezone.utc).isoformat()},
        '$setOnInsert': {'epic_no': epic_no},
    }
    if photo_url:
        update['$set']['photo_url'] = photo_url
    if card_url:
        update['$set']['card_url'] = card_url
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
    """Return {epic_no: {count, last_generated, photo_url, card_url}} dict."""
    result = {}
    for doc in stats_col.find({}, {'_id': 0}):
        result[doc['epic_no']] = {
            'count': doc.get('count', 0),
            'last_generated': doc.get('last_generated', ''),
            'photo_url': doc.get('photo_url', ''),
            'card_url': doc.get('card_url', ''),
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
    except Exception:
        total_voters = 0
        total_generated = 0
        total_generations = 0
        cards_on_cloud = 0
        db_connected = False

    return {
        'total_voters': total_voters,
        'total_generated': total_generated,
        'total_generations': total_generations,
        'cards_on_cloud': cards_on_cloud,
        'db_connected': db_connected,
    }


# ══════════════════════════════════════════════════════════════════
#  PUBLIC USER ROUTES  (/)
# ══════════════════════════════════════════════════════════════════

@app.route('/')
def user_home():
    # Fetch latest content for home page sections
    latest_photos = list(photos_col.find({}, {'_id': 0}).sort('created_at', -1).limit(6))
    latest_videos = list(videos_col.find({}, {'_id': 0}).sort('created_at', -1).limit(3))
    latest_announcements = list(announcements_col.find({}, {'_id': 0}).sort('created_at', -1).limit(3))
    member_count = stats_col.count_documents({})
    return render_template('user/home.html',
                           latest_photos=latest_photos,
                           latest_videos=latest_videos,
                           latest_announcements=latest_announcements,
                           member_count=member_count)


@app.route('/photos')
def user_photos():
    """Public photos gallery page."""
    photos = list(photos_col.find({}, {'_id': 0}).sort('created_at', -1))
    return render_template('user/photos.html', photos=photos)


@app.route('/videos')
def user_videos():
    """Public videos page."""
    videos = list(videos_col.find({}, {'_id': 0}).sort('created_at', -1))
    return render_template('user/videos.html', videos=videos)


@app.route('/announcements')
def user_announcements():
    """Public announcements page."""
    announcements = list(announcements_col.find({}, {'_id': 0}).sort('created_at', -1))
    return render_template('user/announcements.html', announcements=announcements)


@app.route('/registration')
def user_registration():
    """Registration page (placeholder)."""
    return render_template('user/registration.html')


@app.route('/generate-id')
def user_generate_page():
    """Dedicated page for generating ID card."""
    return render_template('user/generate.html')


@app.route('/lookup-voter', methods=['POST'])
def lookup_voter():
    """AJAX endpoint: return voter details for confirmation before generating."""
    epic_no = request.form.get('epic_no', '').strip().upper()
    if not epic_no:
        return jsonify({'success': False, 'message': 'Please enter your Epic Number.'}), 400
    voter = find_voter_by_epic(epic_no)
    if not voter:
        return jsonify({'success': False, 'message': 'Epic Number not found. Please check and try again.'}), 404
    # Return only safe display fields
    display_fields = {}
    for key, val in voter.items():
        if key.startswith('_'):
            continue
        display_fields[key] = str(val) if val else ''
    return jsonify({'success': True, 'voter': display_fields})


@app.route('/generate', methods=['POST'])
def user_generate():
    """User enters Epic Number + photo → generate card."""
    epic_no = request.form.get('epic_no', '').strip().upper()

    if not epic_no:
        flash('Please enter your Epic Number.', 'danger')
        return redirect(url_for('user_generate_page'))

    # Photo is required
    photo_image = None
    photo_url = ''
    if 'photo' not in request.files or not request.files['photo'].filename:
        flash('Please upload your photo. It is required.', 'danger')
        return redirect(url_for('user_generate_page'))

    file = request.files['photo']
    if not allowed_file(file.filename, ALLOWED_IMG):
        flash('Invalid photo format. Please upload a JPG or PNG image.', 'danger')
        return redirect(url_for('user_generate_page'))

    # Look up in MongoDB
    voter = find_voter_by_epic(epic_no)
    if not voter:
        flash('Epic Number not found. Please check and try again.', 'danger')
        return redirect(url_for('user_generate_page'))

    # Process photo upload
    try:
        photo_image = Image.open(file.stream).convert('RGB')
        # Upload to Cloudinary
        photo_url = upload_photo_to_cloudinary(photo_image, epic_no)
    except Exception as e:
        logger.warning(f"Photo upload error for {epic_no}: {e}")
        flash('Could not process the uploaded photo. Please try again.', 'danger')
        return redirect(url_for('user_generate_page'))

    # Generate card
    try:
        voter['serial_number'] = generate_serial_number(epic_no)
        voter['verify_url'] = f"{config.BASE_URL}/verify/{epic_no}"
        template = Image.open(config.TEMPLATE_PATH).convert('RGBA')
        card_image = generate_card(voter, template, photo_image=photo_image)

        # Upload generated card to Cloudinary (replaces old card)
        card_url = upload_card_to_cloudinary(card_image, epic_no)

        # Track generation + photo URL + card URL
        increment_generation_count(epic_no, photo_url=photo_url, card_url=card_url)

        # PRG: Redirect to GET /card/<epic_no> to prevent re-generation on refresh
        return redirect(url_for('user_card_page', epic_no=epic_no))

    except Exception as e:
        logger.error(f"Card generation error for {epic_no}: {e}")
        flash('Something went wrong generating the card. Please try again.', 'danger')
        return redirect(url_for('user_generate_page'))


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

    # Separate core fields from extra fields
    core_keys = {'epic_no', 'name', 'assembly', 'district', 'gen_count',
                 'last_generated', 'photo_url', 'card_url', 'serial_number', 'verify_url'}
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

    # Attach generation count + photo URL + card URL
    for v in voters:
        epic = v['epic_no']
        s = all_stats.get(epic, {})
        v['gen_count'] = s.get('count', 0)
        v['last_generated'] = s.get('last_generated', '')
        v['photo_url'] = s.get('photo_url', '')
        v['card_url'] = s.get('card_url', '')

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

    # Separate core fields from extra fields for display
    core_keys = {'epic_no', 'name', 'assembly', 'district', 'gen_count', 'last_generated', 'photo_url', 'card_url'}
    extra_fields = {k: v for k, v in voter.items() if k not in core_keys}

    return render_template('admin/voter_detail.html',
                           voter=voter, extra_fields=extra_fields)

# ── Admin: Photos Management ─────────────────────────────────────

@admin_bp.route('/photos')
def admin_photos():
    items = list(photos_col.find({}, {'_id': 0}).sort('created_at', -1))
    return render_template('admin/photos.html', items=items)


@admin_bp.route('/photos/add', methods=['POST'])
def admin_photos_add():
    title = request.form.get('title', '').strip()
    url = request.form.get('url', '').strip()
    if not url:
        flash('Image URL is required.', 'danger')
        return redirect(url_for('admin.admin_photos'))
    photos_col.insert_one({
        'title': title,
        'url': url,
        'created_at': datetime.now(timezone.utc).isoformat(),
    })
    flash('Photo added successfully!', 'success')
    return redirect(url_for('admin.admin_photos'))


@admin_bp.route('/photos/delete', methods=['POST'])
def admin_photos_delete():
    url = request.form.get('url', '').strip()
    if url:
        photos_col.delete_one({'url': url})
        flash('Photo deleted.', 'success')
    return redirect(url_for('admin.admin_photos'))


# ── Admin: Videos Management ─────────────────────────────────────

@admin_bp.route('/videos')
def admin_videos():
    items = list(videos_col.find({}, {'_id': 0}).sort('created_at', -1))
    return render_template('admin/videos.html', items=items)


def _extract_youtube_id(url):
    """Extract YouTube video ID from various URL formats."""
    yt_id = ''
    if 'youtube.com/watch' in url and 'v=' in url:
        yt_id = url.split('v=')[1].split('&')[0]
    elif 'youtu.be/' in url:
        yt_id = url.split('youtu.be/')[1].split('?')[0]
    elif 'youtube.com/embed/' in url:
        yt_id = url.split('youtube.com/embed/')[1].split('?')[0]
    return yt_id


def _fetch_youtube_details(yt_id):
    """Fetch title & description from YouTube.
    Uses oEmbed (no key needed) for title, Data API v3 for description."""
    title, description = None, None

    # 1) oEmbed — always works, no API key needed, returns title
    try:
        oembed = http_requests.get(
            'https://www.youtube.com/oembed',
            params={'url': f'https://www.youtube.com/watch?v={yt_id}', 'format': 'json'},
            timeout=8,
        )
        if oembed.status_code == 200:
            title = oembed.json().get('title', '')
    except Exception:
        pass

    # 2) Data API v3 — for description (requires API key)
    api_key = config.YOUTUBE_API_KEY
    if api_key and yt_id:
        try:
            resp = http_requests.get(
                'https://www.googleapis.com/youtube/v3/videos',
                params={'id': yt_id, 'part': 'snippet', 'key': api_key},
                timeout=8,
            )
            data = resp.json()
            if data.get('items'):
                snippet = data['items'][0]['snippet']
                if not title:
                    title = snippet.get('title', '')
                description = snippet.get('description', '')
        except Exception:
            pass

    return title, description


@admin_bp.route('/videos/add', methods=['POST'])
def admin_videos_add():
    title = request.form.get('title', '').strip()
    url = request.form.get('url', '').strip()
    if not url:
        flash('Video URL is required.', 'danger')
        return redirect(url_for('admin.admin_videos'))

    description = ''
    yt_id = _extract_youtube_id(url)
    if yt_id:
        yt_title, yt_desc = _fetch_youtube_details(yt_id)
        if yt_title and not title:
            title = yt_title
        if yt_desc:
            description = yt_desc

    videos_col.insert_one({
        'title': title,
        'description': description,
        'url': url,
        'created_at': datetime.now(timezone.utc).isoformat(),
    })
    flash('Video added successfully!', 'success')
    return redirect(url_for('admin.admin_videos'))


@admin_bp.route('/videos/delete', methods=['POST'])
def admin_videos_delete():
    url = request.form.get('url', '').strip()
    if url:
        videos_col.delete_one({'url': url})
        flash('Video deleted.', 'success')
    return redirect(url_for('admin.admin_videos'))

@admin_bp.route('/videos/refresh', methods=['POST'])
def admin_videos_refresh():
    """Re-fetch YouTube title & description for all videos."""
    updated = 0
    for video in videos_col.find():
        yt_id = _extract_youtube_id(video.get('url', ''))
        if yt_id:
            yt_title, yt_desc = _fetch_youtube_details(yt_id)
            update_fields = {}
            if yt_title:
                update_fields['title'] = yt_title
            if yt_desc:
                update_fields['description'] = yt_desc
            if update_fields:
                videos_col.update_one({'_id': video['_id']}, {'$set': update_fields})
                updated += 1
    flash(f'Refreshed {updated} video(s) with YouTube details.', 'success')
    return redirect(url_for('admin.admin_videos'))

# ── Admin: Announcements Management ──────────────────────────────

@admin_bp.route('/announcements')
def admin_announcements():
    items = list(announcements_col.find({}, {'_id': 0}).sort('created_at', -1))
    return render_template('admin/announcements.html', items=items)


@admin_bp.route('/announcements/add', methods=['POST'])
def admin_announcements_add():
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    image_url = request.form.get('image_url', '').strip()
    if not title:
        flash('Title is required.', 'danger')
        return redirect(url_for('admin.admin_announcements'))
    announcements_col.insert_one({
        'title': title,
        'content': content,
        'image_url': image_url,
        'created_at': datetime.now(timezone.utc).isoformat(),
    })
    flash('Announcement added successfully!', 'success')
    return redirect(url_for('admin.admin_announcements'))


@admin_bp.route('/announcements/delete', methods=['POST'])
def admin_announcements_delete():
    title = request.form.get('title', '').strip()
    created_at = request.form.get('created_at', '').strip()
    if title and created_at:
        announcements_col.delete_one({'title': title, 'created_at': created_at})
        flash('Announcement deleted.', 'success')
    return redirect(url_for('admin.admin_announcements'))

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
