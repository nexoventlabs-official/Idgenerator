"""
Card Generation Engine — We The Leaders v5.0
=============================================
Layout: label+value rows on LEFT, passport photo bottom-RIGHT (no QR).
Template: newtemplate.jpeg (1536x1024)
"""
import hashlib, logging, os, sys
from PIL import Image, ImageDraw, ImageFont, ImageOps
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


# ── Font utilities ────────────────────────────────────────────────
def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = (
        getattr(config, 'FONT_BOLD_PATHS', ['C:/Windows/Fonts/arialbd.ttf'])
        if bold else
        getattr(config, 'FONT_PATHS', ['C:/Windows/Fonts/arial.ttf'])
    )
    for path in paths:
        if path and os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def load_bold_font(size: int) -> ImageFont.FreeTypeFont:
    return load_font(size, bold=True)


def get_text_width(text: str, font) -> int:
    draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def get_text_height(text: str, font) -> int:
    draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


def load_member_photo(*args, **kwargs):
    """Stub kept for API compatibility."""
    return None


def generate_serial_number(epic_no: str) -> str:
    h = hashlib.md5(epic_no.encode()).hexdigest().upper()
    return f"SN-{h[:1]}{h[2:3]}{h[4:5]}{h[6:7]}{h[8:9]}{h[10:11]}{h[12:13]}"


# ── Passport photo helper ─────────────────────────────────────────
def _fit_passport_photo(photo: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """
    Crop & resize photo to exactly box_w x box_h (passport style: face centred).
    """
    photo = photo.convert('RGB')
    img_w, img_h = photo.size

    # Scale so the smallest dimension fills the box
    scale = max(box_w / img_w, box_h / img_h)
    new_w = int(img_w * scale)
    new_h = int(img_h * scale)
    resized = photo.resize((new_w, new_h), Image.LANCZOS)

    # Centre-crop
    left = (new_w - box_w) // 2
    top  = max(0, int((new_h - box_h) * 0.25))   # bias towards top (face)
    cropped = resized.crop((left, top, left + box_w, top + box_h))
    return cropped


# ══════════════════════════════════════════════════════════════════
#  MAIN CARD GENERATOR
# ══════════════════════════════════════════════════════════════════

def generate_card(voter: dict,
                  template: Image.Image,
                  photo_image: Image.Image = None) -> Image.Image:
    """
    Generate a membership ID card.

    Args:
        voter        – dict: epic_no, name, assembly_name, district, ptc_code, verify_url
        template     – PIL Image of newtemplate.jpeg
        photo_image  – PIL Image (passport photo, optional)

    Returns: PIL RGB Image
    """
    card = template.copy().convert("RGB")
    W, H = card.size
    draw = ImageDraw.Draw(card)

    # ── Sanitize ──────────────────────────────────────────────────
    def clean(val, maxlen=120):
        s = str(val or '').strip()
        s = ''.join(c for c in s if c.isprintable())
        s = s.replace('{','').replace('}','').replace('$','').replace('\\','')
        return s[:maxlen]

    name     = clean(voter.get('name', '')).upper()
    epic_no  = clean(voter.get('epic_no', '')).upper()
    assembly = clean(voter.get('assembly_name','') or voter.get('assembly','')).upper()
    district = clean(voter.get('district','') or voter.get('DISTRICT_NAME','')).upper()
    ptc_code = clean(voter.get('ptc_code', ''))

    # ── Layout zones ──────────────────────────────────────────────
    CONTENT_TOP = int(H * 0.40)
    CONTENT_BOT = int(H * 0.83)

    # ── Fonts ─────────────────────────────────────────────────────
    F_LBL = int(H * 0.030)
    F_VAL = int(H * 0.042)
    F_PTC = int(H * 0.022)

    f_lbl = load_font(F_LBL, bold=False)
    f_val = load_font(F_VAL, bold=True)
    f_ptc = load_font(F_PTC, bold=True)

    LABEL_CLR = (90,  90,  90)
    VALUE_CLR = (10,  10,  10)

    # ── Text fields ───────────────────────────────────────────────
    FIELDS = [
        ("Name",     name),
        ("EPIC No",  epic_no),
        ("Assembly", assembly),
        ("District", district),
    ]

    FIELD_X   = int(W * 0.05)
    COLON_GAP = 12
    max_lbl_w = max(get_text_width(lbl, f_lbl) for lbl, _ in FIELDS)
    COLON_X   = FIELD_X + max_lbl_w + COLON_GAP
    VALUE_X   = COLON_X + get_text_width(" : ", f_lbl) + 4
    MAX_VAL_W = int(W * 0.34)

    row_content_h = get_text_height("Ag", f_val)
    ROW_GAP       = int(H * 0.055)
    ROW_H         = row_content_h + ROW_GAP
    block_top     = CONTENT_TOP + int((CONTENT_BOT - CONTENT_TOP) * 0.08)

    for i, (label, value) in enumerate(FIELDS):
        y = block_top + i * ROW_H

        lbl_y = y + (row_content_h - get_text_height(label, f_lbl)) // 2
        draw.text((FIELD_X, lbl_y), label, font=f_lbl, fill=LABEL_CLR)

        col_y = y + (row_content_h - get_text_height(":", f_lbl)) // 2
        draw.text((COLON_X, col_y), ":", font=f_lbl, fill=LABEL_CLR)

        fv, size = f_val, F_VAL
        while get_text_width(value, fv) > MAX_VAL_W and size > int(H * 0.020):
            size -= 1
            fv = load_font(size, bold=True)
        draw.text((VALUE_X, y), value, font=fv, fill=VALUE_CLR)

    # ── Passport photo — bottom right (where QR was) ──────────────
    # Passport ratio 35mm × 45mm  →  7 : 9
    PHOTO_W = int(H * 0.21)              # same width as old QR
    PHOTO_H = int(PHOTO_W * 9 / 7)      # passport height ratio
    PHOTO_X = W  - PHOTO_W - int(W * 0.022)
    PHOTO_Y = H  - PHOTO_H - int(H * 0.012)

    if photo_image:
        # Fit user photo
        fitted = _fit_passport_photo(photo_image, PHOTO_W, PHOTO_H)
    else:
        # Grey placeholder
        fitted = Image.new('RGB', (PHOTO_W, PHOTO_H), (210, 210, 210))
        pd = ImageDraw.Draw(fitted)
        fp = load_font(max(12, int(PHOTO_W * 0.10)), bold=False)
        ph_text = "PHOTO"
        pw = get_text_width(ph_text, fp)
        pd.text(((PHOTO_W - pw) // 2, PHOTO_H // 2 - 10), ph_text,
                font=fp, fill=(130, 130, 130))

    # White border around photo
    PAD = 6
    draw.rectangle(
        [PHOTO_X - PAD, PHOTO_Y - PAD,
         PHOTO_X + PHOTO_W + PAD, PHOTO_Y + PHOTO_H + PAD],
        fill=(255, 255, 255)
    )
    # Thin dark border
    draw.rectangle(
        [PHOTO_X - PAD, PHOTO_Y - PAD,
         PHOTO_X + PHOTO_W + PAD, PHOTO_Y + PHOTO_H + PAD],
        outline=(80, 80, 80), width=2
    )
    card.paste(fitted, (PHOTO_X, PHOTO_Y))

    # PTC code below photo (centered)
    if ptc_code:
        ptc_w  = get_text_width(ptc_code, f_ptc)
        ptc_cx = PHOTO_X + PHOTO_W // 2
        ptc_y  = PHOTO_Y - get_text_height(ptc_code, f_ptc) - 8
        draw.text((ptc_cx - ptc_w // 2, ptc_y),
                  ptc_code, font=f_ptc, fill=(15, 15, 15))

    return card.convert('RGB')
