"""
Card Generation Engine — We The Leaders v6.0
=============================================
Pixel-perfect replication of Id cards/index.html at 5× scale.

HTML card:  340 × 214 px
Output:    1700 × 1070 px  (5× scale)

Every position maps directly from CSS values × 5.
"""
import hashlib, io, logging, os, sys
from PIL import Image, ImageDraw, ImageFont
import config

try:
    import qrcode
    _qrcode_available = True
except ImportError:
    _qrcode_available = False

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

# ── Scale factor & canvas ────────────────────────────────────────
SCALE  = 5
CARD_W = 340 * SCALE   # 1700
CARD_H = 214 * SCALE   # 1070

# ── Asset folder ─────────────────────────────────────────────────
_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Id cards')

def _asset_path(name):
    return os.path.join(_ASSETS, name)

def _load_rgba(name):
    p = _asset_path(name)
    if not os.path.isfile(p):
        return None
    try:
        return Image.open(p).convert('RGBA')
    except Exception:
        return None

def _paste_rgba(base, overlay, xy, opacity=1.0):
    if overlay.mode != 'RGBA':
        overlay = overlay.convert('RGBA')
    if opacity < 1.0:
        r, g, b, a = overlay.split()
        a = a.point(lambda x: int(x * opacity))
        overlay = Image.merge('RGBA', (r, g, b, a))
    base.paste(overlay, xy, mask=overlay.split()[3])


# ── Font helpers ─────────────────────────────────────────────────
def load_font(size, bold=False):
    paths = (
        getattr(config, 'FONT_BOLD_PATHS', ['C:/Windows/Fonts/arialbd.ttf'])
        if bold else
        getattr(config, 'FONT_PATHS', ['C:/Windows/Fonts/arial.ttf'])
    )
    for p in paths:
        if p and os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def load_bold_font(size):
    return load_font(size, bold=True)

def _tw(text, font):
    d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    bb = d.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]

def _th(text, font):
    d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    bb = d.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]

# ── Compat stubs ─────────────────────────────────────────────────
def get_text_width(text, font):
    return _tw(text, font)

def get_text_height(text, font):
    return _th(text, font)

def load_member_photo(*a, **k):
    return None

def generate_serial_number(epic_no):
    h = hashlib.md5(epic_no.encode()).hexdigest().upper()
    return f"M-2025-{h[:3]}"


# ── Passport photo fit ───────────────────────────────────────────
def _fit_photo(photo, box_w, box_h):
    photo  = photo.convert('RGB')
    iw, ih = photo.size
    scale  = max(box_w / iw, box_h / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img    = photo.resize((nw, nh), Image.LANCZOS)
    left   = (nw - box_w) // 2
    top    = max(0, int((nh - box_h) * 0.20))
    return img.crop((left, top, left + box_w, top + box_h))


# ── QR code generator ─────────────────────────────────────────────
def _make_qr(url: str, size: int) -> Image.Image:
    """Generate a clean QR code image at given pixel size."""
    if not _qrcode_available or not url:
        return None
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
        return img.resize((size, size), Image.LANCZOS)
    except Exception:
        return None


# ── Load static asset (newfavicon etc.) ──────────────────────────
def _load_static_rgba(filename: str):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', filename)
    if not os.path.isfile(p):
        return None
    try:
        return Image.open(p).convert('RGBA')
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  BACK CARD GENERATOR
# ══════════════════════════════════════════════════════════════════

def generate_back_card():
    """
    Render the back side of the membership card.
    Background: we_the_leaders_back.png (full bleed)
    Watermark:  newfavicon.png centred at 30% opacity
    Output:     1700 x 1070 RGB
    """
    W, H = CARD_W, CARD_H

    # Background
    back_path = _asset_path('we_the_leaders_back.png')
    if os.path.isfile(back_path):
        bg = Image.open(back_path).convert('RGB').resize((W, H), Image.LANCZOS)
    else:
        bg = Image.new('RGB', (W, H), (250, 248, 244))

    # Centre watermark: newfavicon.png
    favicon = _load_static_rgba('newfavicon.png')
    if favicon:
        wm_size = int(min(W, H) * 0.45)          # ~480 px
        wm = favicon.resize((wm_size, wm_size), Image.LANCZOS)
        r, g, b, a = wm.split()
        a = a.point(lambda x: int(x * 0.30))     # 30% opacity
        wm = Image.merge('RGBA', (r, g, b, a))
        wx = (W - wm_size) // 2
        wy = (H - wm_size) // 2
        bg.paste(wm, (wx, wy), mask=wm.split()[3])

    return bg.convert('RGB')


def generate_card(voter, template=None, photo_image=None, qr_image=None):
    """
    Render membership ID card — pixel-perfect match of index.html at 5× scale.
    template arg is ignored (kept for backward compat).
    """
    S  = SCALE          # 5
    W  = CARD_W         # 1700
    H  = CARD_H         # 1070

    # ── Canvas: linear-gradient(180deg, #ffffff → #f4f5f7) ───────
    card = Image.new('RGB', (W, H))
    for y in range(H):
        t   = y / (H - 1)
        r   = int(0xFF + t * (0xF4 - 0xFF))   # 255 → 244
        g   = int(0xFF + t * (0xF5 - 0xFF))   # 255 → 245
        b   = int(0xFF + t * (0xF7 - 0xFF))   # 255 → 247
        ImageDraw.Draw(card).line([(0, y), (W - 1, y)], fill=(r, g, b))
    draw = ImageDraw.Draw(card)

    # ── Sanitize ─────────────────────────────────────────────────
    def clean(v, n=120):
        s = str(v or '').strip()
        s = ''.join(c for c in s if c.isprintable())
        return s.replace('{','').replace('}','').replace('$','').replace('\\','')[:n]

    name     = clean(voter.get('name', ''))
    epic_no  = clean(voter.get('epic_no', '')).upper()
    assembly = clean(voter.get('assembly_name','') or voter.get('assembly',''))
    district = clean(voter.get('district','') or voter.get('DISTRICT_NAME',''))
    ptc_code = clean(voter.get('ptc_code',''))
    member_id = ptc_code if ptc_code else generate_serial_number(epic_no)

    # ══════════════════════════════════════════════════════════════
    #  LAYER 1 — leader.png watermark (behind everything)
    #  CSS: right:-45px; bottom:52px; height:130px; opacity:0.1; grayscale
    # ══════════════════════════════════════════════════════════════
    leader = _load_rgba('leader.png')
    if leader:
        lh = 130 * S                                    # 650 px
        lw = int(lh * leader.size[0] / leader.size[1])
        lm = leader.resize((lw, lh), Image.LANCZOS)
        # greyscale
        grey = lm.convert('LA').convert('RGBA')
        lx   = W - lw + 45 * S                         # right: -45px → right edge + 45
        ly   = H - lh - 52 * S                         # bottom: 52px
        _paste_rgba(card, grey, (lx, ly), opacity=0.10)

    draw = ImageDraw.Draw(card)

    # ══════════════════════════════════════════════════════════════
    #  LAYER 2 — charity badge logo top-left
    #  CSS: left:18px; top:15px; height:38px
    # ══════════════════════════════════════════════════════════════
    charity = _load_rgba('charity_logo.png')
    if charity:
        bh = 38 * S                                     # 190 px
        bw = int(bh * charity.size[0] / charity.size[1])
        badge = charity.resize((bw, bh), Image.LANCZOS)
        _paste_rgba(card, badge, (18 * S, 15 * S))

    # ══════════════════════════════════════════════════════════════
    #  LAYER 3 — WTL logo centred in header
    #  CSS: .header { padding:16px 20px 0; justify-content:center }
    #        .logo { height:38px }
    # ══════════════════════════════════════════════════════════════
    wtl = _load_rgba('we_the_leaders_logo.png')
    if wtl:
        lh2 = 38 * S                                    # 190 px
        lw2 = int(lh2 * wtl.size[0] / wtl.size[1])
        logo = wtl.resize((lw2, lh2), Image.LANCZOS)
        lx2  = (W - lw2) // 2
        ly2  = 16 * S                                   # padding-top:16px
        _paste_rgba(card, logo, (lx2, ly2))

    draw = ImageDraw.Draw(card)

    # ══════════════════════════════════════════════════════════════
    #  CONTENT AREA
    #  CSS: .content { padding:10px 20px; gap:18px }
    #  Content starts after header band: 16px padding + 38px logo + ~4px = 58px top
    # ══════════════════════════════════════════════════════════════
    CONTENT_TOP = (16 + 38 + 4) * S    # ~290 px  (58 × 5)
    PAD_L       = 20 * S               # 100 px
    GAP         = 18 * S               # 90 px  (gap between photo and details)

    # ── Photo box ─────────────────────────────────────────────────
    # CSS: width:85px; height:105px; border-radius:8px; border:2px solid #e2e8f0
    PHOTO_W  = 85 * S   # 425
    PHOTO_H  = 105 * S  # 525
    PHOTO_X  = PAD_L
    # Vertically centre in content area
    # content area height ≈ H - CONTENT_TOP - footer(14+some) ~ H - CONTENT_TOP - 40*S
    FOOTER_T = (H - 14 * S)            # footer bottom:14px from card bottom
    AVAIL_H  = FOOTER_T - CONTENT_TOP
    PHOTO_Y  = CONTENT_TOP + (AVAIL_H - PHOTO_H) // 2

    # Draw border + white bg
    BR  = 8 * S                        # border-radius:8px → 40px
    BW  = 2 * S                        # border:2px → 10px
    # white background rounded rect
    draw.rounded_rectangle(
        [PHOTO_X - BW, PHOTO_Y - BW,
         PHOTO_X + PHOTO_W + BW, PHOTO_Y + PHOTO_H + BW],
        radius=BR, fill=(255, 255, 255), outline=(226, 232, 240), width=BW
    )

    # Paste photo
    if photo_image:
        fitted = _fit_photo(photo_image, PHOTO_W, PHOTO_H)
    else:
        # SVG-like placeholder: #f1f5f9 bg, circle head, body path
        fitted = Image.new('RGB', (PHOTO_W, PHOTO_H), (241, 245, 249))
        pd = ImageDraw.Draw(fitted)
        cx  = PHOTO_W // 2
        # circle head — cx=50%, cy=45/120*H≈37.5%, r=22/100*W=22%
        cr  = int(PHOTO_W * 0.22)
        cy  = int(PHOTO_H * 0.375)
        pd.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(203, 213, 225))
        # body path approximation
        pd.polygon([
            (int(PHOTO_W * 0.10), PHOTO_H),
            (int(PHOTO_W * 0.90), PHOTO_H),
            (int(PHOTO_W * 0.78), int(PHOTO_H * 0.60)),
            (int(PHOTO_W * 0.22), int(PHOTO_H * 0.60)),
        ], fill=(203, 213, 225))

    # Clip photo to rounded rect mask
    mask = Image.new('L', (PHOTO_W, PHOTO_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, PHOTO_W - 1, PHOTO_H - 1], radius=BR, fill=255
    )
    card.paste(fitted, (PHOTO_X, PHOTO_Y), mask=mask)

    draw = ImageDraw.Draw(card)

    # ══════════════════════════════════════════════════════════════
    #  DETAILS BLOCK (right of photo)
    # ══════════════════════════════════════════════════════════════
    DET_X = PHOTO_X + PHOTO_W + GAP    # start of details column
    # Reserve space for QR on far right
    # QR CSS: right:20px; bottom:24px; width:45px; height:45px
    QR_W  = 45 * S     # 225
    QR_H  = 45 * S     # 225
    QR_X  = W - 20 * S - QR_W         # right:20px
    QR_Y  = H - 24 * S - QR_H         # bottom:24px
    DET_MAX_X = QR_X - 10 * S
    DET_W = DET_MAX_X - DET_X

    # Vertically centre details in same content zone as photo
    # Details consist of: name + member_id + 3 info rows
    # Let's compute total height first to centre it

    # Font sizes from CSS (in px at 1× then × SCALE):
    # .member  font-size:19px weight:700
    # .member-id-val font-size:13px weight:800
    # .info div font-size:11px; .info span font-size:8px weight:700
    F_NAME   = 19 * S   # 95
    F_MID    = 13 * S   # 65
    F_VAL    = 11 * S   # 55
    F_LBL    = 8  * S   # 40

    f_name  = load_bold_font(F_NAME)
    f_mid   = load_bold_font(F_MID)
    f_val   = load_font(F_VAL, bold=False)
    f_lbl   = load_bold_font(F_LBL)

    # Auto-shrink name if too wide
    while _tw(name, f_name) > DET_W and F_NAME > 12 * S:
        F_NAME -= S
        f_name  = load_bold_font(F_NAME)

    NAME_H   = _th(name, f_name)
    MID_H    = _th(member_id, f_mid)
    # .member margin-bottom:8px; .member-id-val margin-bottom:6px
    # .info div margin-bottom:4px
    MB_NAME  = 8  * S   # 40
    MB_MID   = 6  * S   # 30
    MB_INFO  = 4  * S   # 20
    ROW_H    = _th("Ag", f_val)

    FIELDS = [
        ("EPIC No",  epic_no),
        ("Assembly", assembly),
        ("District", district),
    ]

    # Total block height
    block_h  = (NAME_H + MB_NAME +
                MID_H  + MB_MID  +
                3 * ROW_H + 2 * MB_INFO)

    # Centre block vertically within the photo zone
    DET_Y = CONTENT_TOP + (AVAIL_H - block_h) // 2

    # — Name —
    # color:#0f172a  weight:700  font-family:Outfit (we use bold arial)
    draw.text((DET_X, DET_Y), name,
              font=f_name, fill=(15, 23, 42))
    y = DET_Y + NAME_H + MB_NAME

    # — Member ID —
    # color:#111827  weight:800  font-size:13px
    draw.text((DET_X, y), member_id,
              font=f_mid, fill=(17, 24, 39))
    y += MID_H + MB_MID

    # — Info rows —
    # .info span width:75px (label column)
    SPAN_W   = 75 * S   # 375 px
    COLON_GAP = 0       # colon is part of value text ": value"

    for label, value in FIELDS:
        # value: color:#334155  font-size:11px — auto-shrink if needed
        fv, fv_s = f_val, F_VAL
        val_x    = DET_X + SPAN_W
        max_vw   = DET_MAX_X - val_x
        v_text   = f": {value}"
        while _tw(v_text, fv) > max_vw and fv_s > 6 * S:
            fv_s -= 1
            fv = load_font(fv_s, bold=False)

        # vertically align label (smaller) with value (larger) on same row
        lbl_h   = _th(label, f_lbl)
        val_h   = _th(v_text, fv)
        lbl_off = (val_h - lbl_h) // 2

        # label: color:#64748b  font-size:8px  weight:700  uppercase
        draw.text((DET_X, y + lbl_off), label.upper(),
                  font=f_lbl, fill=(100, 116, 139))
        # value
        draw.text((val_x, y), v_text,
                  font=fv, fill=(51, 65, 85))

        y += ROW_H + MB_INFO

    # ══════════════════════════════════════════════════════════════
    #  QR code box
    #  CSS: right:20px; bottom:24px; width:45px; height:45px
    #       border:1px solid #cbd5e1; border-radius:4px; padding:3px
    # ══════════════════════════════════════════════════════════════
    QR_PAD = 3 * S    # inner padding
    QR_BR  = 4 * S    # border-radius

    draw.rounded_rectangle(
        [QR_X - QR_PAD, QR_Y - QR_PAD,
         QR_X + QR_W + QR_PAD, QR_Y + QR_H + QR_PAD],
        radius=QR_BR,
        fill=(255, 255, 255),
        outline=(203, 213, 225),
        width=1 * S
    )

    # Build QR from verify_url if no qr_image supplied
    if not qr_image:
        verify_url = voter.get('verify_url', '')
        if not verify_url:
            verify_url = f"{getattr(config, 'BASE_URL', 'https://wetheleaders.org')}/verify/{epic_no}"
        qr_image = _make_qr(verify_url, QR_W)

    if qr_image:
        qr_fit = qr_image.convert('RGB').resize((QR_W, QR_H), Image.LANCZOS)
        card.paste(qr_fit, (QR_X, QR_Y))
    else:
        # Fallback checkerboard (qrcode lib missing)
        qr_ph  = Image.new('RGB', (QR_W, QR_H), (255, 255, 255))
        qd     = ImageDraw.Draw(qr_ph)
        step   = QR_W // 7
        for i in range(7):
            for j in range(7):
                if (i + j) % 2 == 0:
                    qd.rectangle(
                        [i * step, j * step,
                         (i + 1) * step - 2, (j + 1) * step - 2],
                        fill=(180, 180, 180)
                    )
        card.paste(qr_ph, (QR_X, QR_Y))
    # QR label below — ".front-qr-label font-size:5px weight:700 color:#64748b"
    F_QRL  = 5 * S     # 25
    f_qrl  = load_bold_font(F_QRL)
    ql     = "SCAN"
    ql_w   = _tw(ql, f_qrl)
    ql_x   = QR_X + (QR_W - ql_w) // 2
    ql_y   = QR_Y + QR_H + QR_PAD + 3 * S
    draw.text((ql_x, ql_y), ql, font=f_qrl, fill=(100, 116, 139))

    # ══════════════════════════════════════════════════════════════
    #  FOOTER
    #  CSS: .footer { bottom:14px; left:20px; right:20px }
    #        .footer-left { font-size:7px weight:700 color:#64748b letter-spacing:0.8px uppercase }
    # ══════════════════════════════════════════════════════════════
    F_FOOT  = 7 * S    # 35
    f_foot  = load_bold_font(F_FOOT)
    foot_y  = H - 14 * S - _th("M", f_foot)
    draw.text((20 * S, foot_y), "MEMBERSHIP ID CARD",
              font=f_foot, fill=(100, 116, 139))

    # ── Outer card border: 1px solid rgba(0,0,0,0.08) ────────────
    draw.rounded_rectangle(
        [0, 0, W - 1, H - 1],
        radius=16 * S,
        outline=(0, 0, 0, 20),
        width=S
    )

    return card.convert('RGB')
