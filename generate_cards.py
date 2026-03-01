"""
Voter ID Card Generator v3.0 — Card Generation Engine
=======================================================
Generates personalised ID cards with QR codes.
Returns PIL Image — caller handles Cloudinary upload.
"""

import hashlib
import logging
import os
import sys

import qrcode
from PIL import Image, ImageDraw, ImageFont

import config

# ── Logging ──────────────────────────────────────────────────────

def setup_logging():
    logger = logging.getLogger('card_generator')
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(ch)
    return logger


logger = setup_logging()


# ══════════════════════════════════════════════════════════════════
#  FONT UTILITIES
# ══════════════════════════════════════════════════════════════════

def load_font(size: int) -> ImageFont.FreeTypeFont:
    paths = getattr(config, 'FONT_PATHS', [config.FONT_FALLBACK])
    for path in paths:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def load_bold_font(size: int) -> ImageFont.FreeTypeFont:
    paths = getattr(config, 'FONT_BOLD_PATHS', [config.FONT_BOLD_FALLBACK, config.FONT_FALLBACK])
    for path in paths:
        if path and os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return load_font(size)


def get_text_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def auto_fit_font(text: str, max_width: int, initial_size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    size = initial_size
    loader = load_bold_font if bold else load_font
    font = loader(size)
    while get_text_width(text, font) > max_width and size > config.FONT_MIN_SIZE:
        size -= 1
        font = loader(size)
    return font


# ══════════════════════════════════════════════════════════════════
#  QR CODE
# ══════════════════════════════════════════════════════════════════

def generate_serial_number(epic_no: str) -> str:
    h = hashlib.md5(epic_no.encode()).hexdigest().upper()
    return f"SN-{h[:1]}{h[2:3]}{h[4:5]}{h[6:7]}{h[8:9]}{h[10:11]}{h[12:13]}"


def generate_qr_code(voter: dict) -> Image.Image:
    verify_url = voter.get('verify_url', '')
    if verify_url:
        qr_data = verify_url
    else:
        qr_data = (
            f"EPIC:{voter.get('epic_no', '')}\n"
            f"NAME:{voter.get('name', '')}\n"
            f"ASSEMBLY:{voter.get('assembly', '')}\n"
            f"DISTRICT:{voter.get('district', '')}\n"
            f"SN:{voter.get('serial_number', '')}"
        )
    qr = qrcode.QRCode(
        version=config.QR_VERSION,
        error_correction=[
            qrcode.constants.ERROR_CORRECT_L,
            qrcode.constants.ERROR_CORRECT_M,
            qrcode.constants.ERROR_CORRECT_Q,
            qrcode.constants.ERROR_CORRECT_H,
        ][config.QR_ERROR_CORRECTION],
        box_size=10,
        border=config.QR_BORDER,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
    box_w = config.QR_BOX[2] - config.QR_BOX[0]
    box_h = config.QR_BOX[3] - config.QR_BOX[1]
    return qr_img.resize((box_w, box_h), Image.LANCZOS)


# ══════════════════════════════════════════════════════════════════
#  PHOTO HANDLING
# ══════════════════════════════════════════════════════════════════

def load_member_photo(photo_path: str = '', epic_no: str = '') -> Image.Image:
    """
    Load photo from a given path, or look up by epic_no in member_photos dir.
    Falls back to a grey placeholder box.
    """
    # Try explicit path
    if photo_path and os.path.isfile(photo_path):
        try:
            return Image.open(photo_path).convert('RGB')
        except Exception:
            pass

    # Try member_photos/<epic_no>.*
    if epic_no:
        for ext in ['.jpg', '.jpeg', '.png', '.bmp']:
            p = os.path.join(config.MEMBER_PHOTOS_DIR, f"{epic_no}{ext}")
            if os.path.isfile(p):
                try:
                    return Image.open(p).convert('RGB')
                except Exception:
                    pass

    # Grey placeholder
    box_w = config.PHOTO_BOX[2] - config.PHOTO_BOX[0]
    box_h = config.PHOTO_BOX[3] - config.PHOTO_BOX[1]
    placeholder = Image.new('RGB', (box_w, box_h), color=(220, 220, 220))
    draw = ImageDraw.Draw(placeholder)
    try:
        font = load_font(14)
        draw.text((box_w // 2 - 30, box_h // 2 - 8), "No Photo", fill=(150, 150, 150), font=font)
    except Exception:
        pass
    return placeholder


def resize_photo_to_box(photo: Image.Image) -> Image.Image:
    box_w = config.PHOTO_BOX[2] - config.PHOTO_BOX[0]
    box_h = config.PHOTO_BOX[3] - config.PHOTO_BOX[1]
    img_w, img_h = photo.size
    ratio = min(box_w / img_w, box_h / img_h)
    new_w = int(img_w * ratio)
    new_h = int(img_h * ratio)
    resized = photo.resize((new_w, new_h), Image.LANCZOS)
    result = Image.new('RGB', (box_w, box_h), color=(240, 240, 240))
    result.paste(resized, ((box_w - new_w) // 2, (box_h - new_h) // 2))
    return result


# ══════════════════════════════════════════════════════════════════
#  CARD GENERATOR
# ══════════════════════════════════════════════════════════════════

def generate_card(voter: dict, template: Image.Image,
                  photo_image: Image.Image = None) -> str:
    """
    Generate a single voter ID card.
    Parameters:
        voter       - dict with keys: epic_no, name, assembly, district
        template    - PIL Image of the template
        photo_image - optional PIL Image for the member photo
    Returns: path to the generated card JPEG.
    """
    card = template.copy()
    draw = ImageDraw.Draw(card)

    # ── 1. Text fields (bold, centred) ───────────────────────────
    fields = [
        ('name', config.NAME_XY, config.NAME_END_X, config.NAME_MAX_WIDTH),
        ('epic_no', config.VOTER_ID_XY, config.VOTER_ID_END_X, config.VOTER_ID_MAX_WIDTH),
        ('assembly', config.ASSEMBLY_XY, config.ASSEMBLY_END_X, config.ASSEMBLY_MAX_WIDTH),
        ('district', config.DISTRICT_XY, config.DISTRICT_END_X, config.DISTRICT_MAX_WIDTH),
    ]
    for field_key, (label_end_x, y), end_x, max_width in fields:
        text = voter.get(field_key, '')
        if not text:
            continue
        text = text.upper()  # ALL CAPS
        font = auto_fit_font(text, max_width, config.FONT_SIZE, bold=True)
        text_w = get_text_width(text, font)
        cx = label_end_x + (end_x - label_end_x - text_w) // 2
        draw.text((cx, y), text, fill=config.FONT_COLOR, font=font)

    # ── 2. Member photo ──────────────────────────────────────────
    if photo_image:
        photo = photo_image
    else:
        photo = load_member_photo(epic_no=voter.get('epic_no', ''))
    photo_resized = resize_photo_to_box(photo)
    card.paste(photo_resized, (config.PHOTO_BOX[0], config.PHOTO_BOX[1]))

    # ── 3. QR code ───────────────────────────────────────────────
    serial = voter.get('serial_number', generate_serial_number(voter.get('epic_no', '')))
    voter['serial_number'] = serial

    qr_img = generate_qr_code(voter)
    card.paste(qr_img, (config.QR_BOX[0], config.QR_BOX[1]))

    # ── 4. Text below QR ─────────────────────────────────────────
    vid_font = load_bold_font(config.QR_ID_FONT_SIZE)
    vid_text = voter.get('epic_no', '')
    vid_w = get_text_width(vid_text, vid_font)
    vid_x = config.QR_ID_XY[0] - vid_w // 2
    draw.text((vid_x, config.QR_ID_XY[1]), vid_text, fill=config.QR_FONT_COLOR, font=vid_font)

    sn_font = load_font(config.QR_SERIAL_FONT_SIZE)
    sn_w = get_text_width(serial, sn_font)
    sn_x = config.QR_SERIAL_XY[0] - sn_w // 2
    draw.text((sn_x, config.QR_SERIAL_XY[1]), serial, fill=config.QR_FONT_COLOR, font=sn_font)

    # ── 5. Return the card image (caller handles upload) ─────────
    return card.convert('RGB')
