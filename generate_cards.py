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
            f"NAME:{voter.get('name', '')}\n"
            f"ASSEMBLY:{voter.get('assembly_name', '')}\n"
            f"PTC:{voter.get('serial_number', '')}"
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
    bg_color = getattr(config, 'QR_BG_COLOR_UPPER', (229, 232, 237))
    qr_img = qr.make_image(fill_color="black", back_color=bg_color).convert('RGB')
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
    """Crop-to-fill the photo into the box and apply rounded corners."""
    box_w = config.PHOTO_BOX[2] - config.PHOTO_BOX[0]
    box_h = config.PHOTO_BOX[3] - config.PHOTO_BOX[1]
    img_w, img_h = photo.size

    # Crop-to-fill: scale so the smallest side fills the box, then centre-crop
    ratio = max(box_w / img_w, box_h / img_h)
    new_w = int(img_w * ratio)
    new_h = int(img_h * ratio)
    resized = photo.resize((new_w, new_h), Image.LANCZOS)

    # Centre crop
    left = (new_w - box_w) // 2
    top = (new_h - box_h) // 2
    cropped = resized.crop((left, top, left + box_w, top + box_h))

    # Apply rounded corners and black border
    radius = getattr(config, 'PHOTO_BORDER_RADIUS', 0)
    border_w = getattr(config, 'PHOTO_BORDER_WIDTH', 3)
    border_color = getattr(config, 'PHOTO_BORDER_COLOR', (0, 0, 0))

    cropped = cropped.convert('RGBA')
    if radius > 0:
        # Round the corners with alpha mask
        mask = Image.new('L', (box_w, box_h), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([0, 0, box_w, box_h], radius=radius, fill=255)
        cropped.putalpha(mask)

    # Draw black rounded border on top of the photo
    if border_w > 0:
        border_draw = ImageDraw.Draw(cropped)
        border_draw.rounded_rectangle(
            [0, 0, box_w - 1, box_h - 1],
            radius=radius,
            outline=border_color + (255,),
            width=border_w
        )

    return cropped


# ══════════════════════════════════════════════════════════════════
#  CARD GENERATOR
# ══════════════════════════════════════════════════════════════════

def generate_card(voter: dict, template: Image.Image,
                  photo_image: Image.Image = None) -> str:
    """
    Generate a single voter ID card.
    Parameters:
        voter       - dict with keys: epic_no, name, assembly
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
        ('assembly_name', config.ASSEMBLY_XY, config.ASSEMBLY_END_X, config.ASSEMBLY_MAX_WIDTH),
        ('district', config.DISTRICT_XY, config.DISTRICT_END_X, config.DISTRICT_MAX_WIDTH),
    ]
    for field_key, (label_end_x, y), end_x, max_width in fields:
        text = voter.get(field_key, '')
        if not text:
            continue
        
        # SECURITY FIX: Sanitize input to prevent template injection
        text = str(text)
        # Remove control characters and non-printable characters
        text = ''.join(char for char in text if char.isprintable() or char.isspace())
        # Remove any potential template injection characters
        text = text.replace('{', '').replace('}', '').replace('$', '').replace('\\', '')
        # Limit length to prevent buffer overflow
        max_len = {'name': 100, 'epic_no': 20, 'assembly_name': 100, 'district': 100}.get(field_key, 100)
        text = text[:max_len]
        
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
    # Paste with alpha mask if photo has rounded corners (RGBA)
    if photo_resized.mode == 'RGBA':
        card.paste(photo_resized, (config.PHOTO_BOX[0], config.PHOTO_BOX[1]), photo_resized)
    else:
        card.paste(photo_resized, (config.PHOTO_BOX[0], config.PHOTO_BOX[1]))

    # ── 3. QR code ───────────────────────────────────────────────
    serial = voter.get('serial_number', generate_serial_number(voter.get('epic_no', '')))
    voter['serial_number'] = serial

    # Paint over the template's white box with a smooth gradient sampled from
    # left/right neighbours so it blends seamlessly into the card background.
    wb = getattr(config, 'QR_WHITE_BOX', (318, 390, 486, 613))
    for row_y in range(wb[1], wb[3] + 1):
        # Sample the card colour just outside the left and right edges
        lx = wb[0] - 3
        rx = wb[2] + 3
        lr, lg, lb = card.getpixel((lx, row_y))[:3]
        rr, rg, rb = card.getpixel((rx, row_y))[:3]
        # Average left and right to get the fill colour for this row
        fill = ((lr + rr) // 2, (lg + rg) // 2, (lb + rb) // 2)
        draw.line([(wb[0], row_y), (wb[2], row_y)], fill=fill)

    qr_img = generate_qr_code(voter)
    card.paste(qr_img, (config.QR_BOX[0], config.QR_BOX[1]))

    # ── 4. Text below QR ─────────────────────────────────────────
    # Show PTC code below QR (or fallback to serial number)
    ptc_code = voter.get('ptc_code', '')
    vid_font = load_bold_font(config.QR_ID_FONT_SIZE)
    vid_text = ptc_code if ptc_code else serial
    vid_w = get_text_width(vid_text, vid_font)
    vid_x = config.QR_ID_XY[0] - vid_w // 2
    draw.text((vid_x, config.QR_ID_XY[1]), vid_text, fill=config.QR_FONT_COLOR, font=vid_font)

    # ── 5. Return the card image (caller handles upload) ─────────
    return card.convert('RGB')
