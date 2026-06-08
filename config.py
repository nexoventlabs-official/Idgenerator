"""
Configuration — We The Leaders v5.0
======================================
MongoDB Atlas for all data (voters, generated cards, OTP, etc.)
Cloudinary for user-uploaded photos + generated cards.
Secrets loaded from .env file.
"""
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

# ── Paths ─────────────────────────────────────────────────────────
BASE_DIR           = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH      = os.path.join(BASE_DIR, 'newtemplate.jpeg')
MEMBER_PHOTOS_DIR  = os.path.join(BASE_DIR, 'member_photos')
DATA_DIR           = os.path.join(BASE_DIR, 'data')
UPLOADS_DIR        = os.path.join(BASE_DIR, 'uploads')

# ── MongoDB ───────────────────────────────────────────────────────
MONGO_URI   = os.getenv("MONGO_URI", "")
MONGO_DB    = os.getenv("MONGO_DB", "wetheleaders")

# ── Cloudinary ────────────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME    = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY       = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET    = os.getenv("CLOUDINARY_API_SECRET", "")
CLOUDINARY_PHOTO_FOLDER  = os.getenv("CLOUDINARY_PHOTO_FOLDER", "member_photos")
CLOUDINARY_CARDS_FOLDER  = os.getenv("CLOUDINARY_CARDS_FOLDER", "generated_cards")

# ── Admin Login ───────────────────────────────────────────────────
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    raise ValueError("ADMIN_USERNAME and ADMIN_PASSWORD must be set in .env")

# ── SMS OTP ───────────────────────────────────────────────────────
SMS_API_KEY = os.getenv("SMS_API_KEY", "")

# ── WhatsApp ──────────────────────────────────────────────────────
WHATSAPP_CHANNEL_URL = os.getenv("WHATSAPP_CHANNEL_URL", "")

# ── App URL ───────────────────────────────────────────────────────
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000")

# ── Font Settings ─────────────────────────────────────────────────
FONT_SIZE     = 30
FONT_MIN_SIZE = 14
FONT_COLOR    = (0, 0, 0)

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
FONT_FALLBACK      = 'C:/Windows/Fonts/arial.ttf'
FONT_BOLD_FALLBACK = 'C:/Windows/Fonts/arialbd.ttf'

# ── Template Dimensions (new ATM/PAN card ratio) ─────────────────
TEMPLATE_WIDTH  = 1700
TEMPLATE_HEIGHT = 1071

# ── Layout (% based — computed dynamically in generate_cards.py) ──
# All coordinates are derived from template size at runtime.
# These are kept for reference only.
CONTENT_TOP_PCT = 0.40   # below orange bar
CONTENT_BOT_PCT = 0.83   # above footer wave
FIELD_X_PCT     = 0.05
COLON_GAP       = 12
ROW_GAP_PCT     = 0.055
QR_SIZE_PCT     = 0.21
QR_X_PCT        = 0.022   # margin from right
QR_Y_PCT        = 0.012   # margin from bottom

# ── Output ────────────────────────────────────────────────────────
JPEG_QUALITY = 95
