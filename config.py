"""
Configuration — Voter ID Card Generator v4.0
==============================================
MongoDB for voter data + generation stats.
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

# ── MongoDB ──────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "voter_id_generator")
MONGO_VOTERS_COLLECTION = os.getenv("MONGO_VOTERS_COLLECTION", "voters")
MONGO_STATS_COLLECTION = os.getenv("MONGO_STATS_COLLECTION", "generation_stats")

# ── Cloudinary ───────────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
CLOUDINARY_PHOTO_FOLDER = os.getenv("CLOUDINARY_PHOTO_FOLDER", "member_photos")
CLOUDINARY_CARDS_FOLDER = os.getenv("CLOUDINARY_CARDS_FOLDER", "generated_cards")

# ── Admin Login ──────────────────────────────────────────────────
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# ── YouTube Data API ─────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")


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
PHOTO_BOX = (63, 402, 246, 600)

# ── QR Code Settings ─────────────────────────────────────────────
QR_BOX = (338, 418, 468, 548)
QR_ID_XY = (403, 555)
QR_ID_FONT_SIZE = 18
QR_SERIAL_XY = (403, 582)
QR_SERIAL_FONT_SIZE = 16
QR_FONT_COLOR = (0, 0, 0)
QR_VERSION = 1
QR_ERROR_CORRECTION = 0
QR_BORDER = 1

# ── XLSX Column Mapping ──────────────────────────────────────────
#    Maps internal field names → Excel column headers (case-insensitive match).
#    Admin XLSX must have at least: Epic Number, Name columns.
XLSX_COLUMNS = {
    'epic_no': ['Epic Number', 'Epic No', 'EPIC', 'epicNumber', 'Voter ID', 'voter_id'],
    'name': ['Name', 'Voter Name', 'Full Name', 'applicantFirstName', 'Applicant Name'],
    'assembly': ['Assembly', 'Constituency', 'AC Name', 'asmblyName', 'Assembly Name'],
    'district': ['District', 'Dist', 'districtValue', 'District Value'],
}

# ── Output Settings ──────────────────────────────────────────────
JPEG_QUALITY = 95

# ── App URL (used in QR codes) ───────────────────────────────────
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000")
