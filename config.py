"""
Configuration — Voter ID Card Generator v4.0
==============================================
MySQL for all data (voters, generated cards, stats, OTP, etc.).
Cloudinary for user-uploaded photos + generated cards.
Secrets loaded from .env file.
"""
import os
from dotenv import load_dotenv

# Load .env from the project root
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

# ── Paths ─────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, 'template.jpeg')
MEMBER_PHOTOS_DIR = os.path.join(BASE_DIR, 'member_photos')
DATA_DIR = os.path.join(BASE_DIR, 'data')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')

# Excel — temporary local copy used during import parsing
VOTERS_XLSX = os.path.join(DATA_DIR, 'voters.xlsx')

# ── MySQL (shared connection settings) ────────────────────────────
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")

# ── MySQL DB: Voter rolls (READ-ONLY) ────────────────────────────
MYSQL_VOTERS_DB = os.getenv("MYSQL_VOTERS_DB", "mysql_voters")
MYSQL_VOTERS_TABLE = os.getenv("MYSQL_VOTERS_TABLE", "voters")

# ── MySQL DB: Generated data (READ/WRITE) ────────────────────────
MYSQL_DB = os.getenv("MYSQL_DB", "voter_id_generator")

# ── Cloudinary ───────────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
CLOUDINARY_PHOTO_FOLDER = os.getenv("CLOUDINARY_PHOTO_FOLDER", "member_photos")
CLOUDINARY_CARDS_FOLDER = os.getenv("CLOUDINARY_CARDS_FOLDER", "generated_cards")

# ── Admin Login ──────────────────────────────────────────────────
# SECURITY: No default credentials - must be set in .env
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    raise ValueError("ADMIN_USERNAME and ADMIN_PASSWORD must be set in .env file")

# ── YouTube Data API ─────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# ── WhatsApp Channel ─────────────────────────────────────────────
WHATSAPP_CHANNEL_URL = os.getenv("WHATSAPP_CHANNEL_URL", "")


# ── Font Settings ─────────────────────────────────────────────────
FONT_SIZE = 30
FONT_MIN_SIZE = 14
FONT_COLOR = (0, 0, 0)

# Sans-serif font paths — tried in order (Windows → Linux → bundled)
FONT_PATHS = [
    'C:/Windows/Fonts/arial.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
]
FONT_BOLD_PATHS = [
    'C:/Windows/Fonts/arialbd.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
]
# Legacy single-path fallbacks (kept for backwards compat)
FONT_FALLBACK = 'C:/Windows/Fonts/arial.ttf'
FONT_BOLD_FALLBACK = 'C:/Windows/Fonts/arialbd.ttf'

# ── Template Dimensions ──────────────────────────────────────────
TEMPLATE_WIDTH = 1005
TEMPLATE_HEIGHT = 650

# ── Text Field Coordinates (bold, centred) ────────────────────────
NAME_XY = (175, 189)
NAME_END_X = 695
NAME_MAX_WIDTH = 520

VOTER_ID_XY = (200, 243)
VOTER_ID_END_X = 695
VOTER_ID_MAX_WIDTH = 495

ASSEMBLY_XY = (230, 298)
ASSEMBLY_END_X = 695
ASSEMBLY_MAX_WIDTH = 465

DISTRICT_XY = (178, 351)
DISTRICT_END_X = 695
DISTRICT_MAX_WIDTH = 517

# ── Photo Box ────────────────────────────────────────────────────
# Slightly overlaps into the border (2px each side) to eliminate edge gaps
PHOTO_BOX = (60, 397, 249, 604)
PHOTO_BORDER_RADIUS = 24
PHOTO_BORDER_WIDTH = 3
PHOTO_BORDER_COLOR = (0, 0, 0)

# ── QR Code Settings ─────────────────────────────────────────────
QR_BOX = (338, 418, 468, 548)
QR_WHITE_BOX = (318, 390, 486, 613)  # Template's built-in white box around QR area
QR_WHITE_BOX_MID_Y = 580  # Split line between upper & lower fill zones
QR_BG_COLOR_UPPER = (229, 232, 237)  # Matches card background at QR height (blue-grey tint)
QR_BG_COLOR_LOWER = (243, 243, 243)  # Matches card background at bottom zone
QR_ID_XY = (403, 555)
QR_ID_FONT_SIZE = 18
QR_SERIAL_XY = (403, 582)
QR_SERIAL_FONT_SIZE = 16
QR_FONT_COLOR = (0, 0, 0)
QR_VERSION = 1
QR_ERROR_CORRECTION = 0
QR_BORDER = 1

# ── MySQL Column Reference ────────────────────────────────────────
#    The voters table uses these MySQL columns (mapped to internal names
#    by _translate_voter_row() at query time):
#    EPIC_NO → epic_no,  FM_NAME_EN+LASTNAME_EN → name,  AC_NO → assembly,
#    AGE → age,  GENDER → sex,  RLN_TYPE → relation_type,
#    RLN_FM_NM_EN+RLN_L_NM_EN → relation_name,  MOBILE_NO → mobile,
#    PART_NO → part_no,  DOB → dob

# ── Output Settings ──────────────────────────────────────────────
JPEG_QUALITY = 95

# ── App URL (used in QR codes) ───────────────────────────────────
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000")
