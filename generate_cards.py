"""
Card Generation Engine — MAKKAL MEDAI v7.0
==========================================
Pixel-perfect replication of Id cards/index.html at 5× scale.

HTML card:  340 × 214 px
Output:    1700 × 1070 px  (5× scale)

Every position maps directly from CSS values × 5.
"""
import hashlib, io, logging, math, os, sys
from PIL import Image, ImageDraw, ImageFont
import config

try:
    import qrcode
    _qrcode_available = True
except ImportError:
    _qrcode_available = False

# ── SVG backend detection ────────────────────────────────────────
try:
    import cairosvg as _cairosvg
    _svg_backend = 'cairo'
except ImportError:
    _cairosvg = None
    try:
        from svglib.svglib import svg2rlg as _svg2rlg
        from reportlab.graphics import renderPM as _renderPM
        _svg_backend = 'svglib'
    except ImportError:
        _svg2rlg = None
        _renderPM = None
        _svg_backend = 'pil'

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
    """Generate a clean QR code image at given pixel size (black on white)."""
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


# ── SVG background loader ─────────────────────────────────────────
def _load_svg_background(svg_path: str, width: int, height: int) -> Image.Image:
    """
    Load diamond_bg.svg and render it to a PIL Image at given dimensions.
    Tries cairosvg → svglib+reportlab → PIL radial gradient fallback.
    """
    if _svg_backend == 'cairo' and _cairosvg is not None and os.path.isfile(svg_path):
        try:
            png_bytes = _cairosvg.svg2png(
                url=svg_path,
                output_width=width,
                output_height=height,
            )
            return Image.open(io.BytesIO(png_bytes)).convert('RGBA')
        except Exception as e:
            logger.debug(f"cairosvg failed: {e}")

    if _svg_backend == 'svglib' and _svg2rlg is not None and os.path.isfile(svg_path):
        try:
            drawing = _svg2rlg(svg_path)
            if drawing:
                png_bytes = _renderPM.drawToString(drawing, fmt='PNG')
                img = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
                return img.resize((width, height), Image.LANCZOS)
        except Exception as e:
            logger.debug(f"svglib failed: {e}")

    # ── PIL fallback: deep-red radial gradient (centre-right bright) ──
    logger.debug("SVG fallback: painting PIL radial gradient")
    img = Image.new('RGBA', (width, height))
    pixels = img.load()
    # CSS: radial-gradient(ellipse at 70% 50%, #ff1a1a 0%, #400000 100%)
    cx = int(width * 0.70)
    cy = int(height * 0.50)
    max_dist = math.hypot(max(cx, width - cx), max(cy, height - cy))
    for y in range(height):
        for x in range(width):
            dist = math.hypot(x - cx, y - cy)
            t = min(dist / max_dist, 1.0)
            # bright red → dark maroon
            r = int(0xff + t * (0x40 - 0xff))   # 255 → 64
            g = int(0x1a + t * (0x00 - 0x1a))   # 26  → 0
            b = int(0x1a + t * (0x00 - 0x1a))   # 26  → 0
            pixels[x, y] = (r, g, b, 255)

    # Add low-poly triangle effect via semi-transparent darker overlay triangles
    draw = ImageDraw.Draw(img, 'RGBA')
    import random
    rng = random.Random(42)
    step = 80
    for gx in range(0, width + step, step):
        for gy in range(0, height + step, step):
            jitter = step // 3
            pts = [
                (gx + rng.randint(-jitter, jitter),
                 gy + rng.randint(-jitter, jitter)),
                (gx + step + rng.randint(-jitter, jitter),
                 gy + rng.randint(-jitter, jitter)),
                (gx + rng.randint(-jitter, jitter),
                 gy + step + rng.randint(-jitter, jitter)),
            ]
            alpha = rng.randint(8, 28)
            shade = rng.choice([0, 255])
            draw.polygon(pts, fill=(shade, shade, shade, alpha))
    return img


# ══════════════════════════════════════════════════════════════════
#  BACK CARD GENERATOR
# ══════════════════════════════════════════════════════════════════

def generate_back_card(voter=None):
    """
    Render the back side of the membership card.

    Design matches Id cards/index.html .back:
      - Cream gradient background (135deg #fdfbf7 → #faf6eb)
      - leader.png watermark centred at opacity 0.06
      - Left column: QR code (transparent bg) + "SCAN TO VERIFY" label
      - Right column: Terms & Conditions (4 items)
    Output: 1700 × 1070 RGB
    """
    S = SCALE
    W, H = CARD_W, CARD_H

    # ── Background: cream gradient 135° ──────────────────────────
    card = Image.new('RGBA', (W, H))
    draw = ImageDraw.Draw(card)
    # 135° gradient: top-left = #fdfbf7, bottom-right = #faf6eb
    c0 = (0xfd, 0xfb, 0xf7)   # #fdfbf7
    c1 = (0xfa, 0xf6, 0xeb)   # #faf6eb
    for y in range(H):
        for x in range(W):
            t = (x + y) / (W + H - 2)
            r = int(c0[0] + t * (c1[0] - c0[0]))
            g = int(c0[1] + t * (c1[1] - c0[1]))
            b = int(c0[2] + t * (c1[2] - c0[2]))
            draw.point((x, y), fill=(r, g, b, 255))

    # ── Leader.png watermark: centred, width 120px×5=600px, opacity 0.06 ──
    leader = _load_rgba('leader.png')
    if leader:
        wm_w = 120 * S                                  # 600 px
        wm_h = int(wm_w * leader.size[1] / leader.size[0])
        wm   = leader.resize((wm_w, wm_h), Image.LANCZOS)
        wx   = (W - wm_w) // 2
        wy   = (H - wm_h) // 2
        _paste_rgba(card, wm, (wx, wy), opacity=0.06)

    card = card.convert('RGB')
    draw = ImageDraw.Draw(card)

    # ── Layout: padding:16px×5=80px, gap:16px×5=80px ─────────────
    PAD   = 16 * S   # 80
    GAP   = 16 * S   # 80

    # Left column width: 85px×5=425px
    LEFT_W = 85 * S  # 425

    left_x  = PAD
    right_x = left_x + LEFT_W + GAP
    right_w = W - right_x - PAD

    # ── LEFT COLUMN: QR code ──────────────────────────────────────
    # back-qr-box: 80px×5=400px, transparent background
    QR_SIZE = 80 * S   # 400 px
    qr_x    = left_x + (LEFT_W - QR_SIZE) // 2
    qr_y    = PAD + (H - 2 * PAD - QR_SIZE - 35 * S) // 2  # roughly centred

    verify_url = ''
    if voter:
        verify_url = voter.get('verify_url', '')
        if not verify_url:
            epic_no = str(voter.get('epic_no', '')).strip().upper()
            verify_url = f"{getattr(config, 'BASE_URL', 'http://localhost:5000')}/verify/{epic_no}"

    qr_img = _make_qr(verify_url, QR_SIZE) if verify_url else None

    if qr_img:
        # Make white pixels transparent (transparent bg as per CSS)
        qr_rgba = qr_img.convert('RGBA')
        pix = qr_rgba.load()
        for py in range(qr_rgba.height):
            for px_ in range(qr_rgba.width):
                rv, gv, bv, av = pix[px_, py]
                if rv > 200 and gv > 200 and bv > 200:
                    pix[px_, py] = (255, 255, 255, 0)
                else:
                    pix[px_, py] = (20, 20, 20, 255)
        card_rgba = card.convert('RGBA')
        card_rgba.paste(qr_rgba, (qr_x, qr_y), mask=qr_rgba.split()[3])
        card = card_rgba.convert('RGB')
        draw = ImageDraw.Draw(card)
    else:
        # Placeholder grid
        draw.rectangle([qr_x, qr_y, qr_x + QR_SIZE, qr_y + QR_SIZE],
                       fill=(200, 200, 200))

    # "SCAN TO VERIFY" label — 7px×5=35px bold dark #1e293b
    F_SCAN = 7 * S   # 35 px
    f_scan = load_bold_font(F_SCAN)
    lbl    = "SCAN TO VERIFY"
    lbl_w  = _tw(lbl, f_scan)
    lbl_x  = left_x + (LEFT_W - lbl_w) // 2
    lbl_y  = qr_y + QR_SIZE + 8 * S     # margin-top: 8px×5=40px (scaled 8)
    draw.text((lbl_x, lbl_y), lbl, font=f_scan, fill=(0x1e, 0x29, 0x3b))

    # ── RIGHT COLUMN: Terms & Conditions ─────────────────────────
    # Title: 10.5px×5=52px bold, color #450a0a, uppercase, letter-spacing 1px
    # border-bottom 1.5px×5=7px solid rgba(220,38,38,0.25), pb 3px×5=15px, mb 8px×5=40px
    F_TITLE = int(10.5 * S)  # 52 px
    f_title = load_bold_font(F_TITLE)

    terms_top = PAD + int(H * 0.05)   # a little below top padding to vertically centre block
    ty = terms_top

    title_text = "TERMS & CONDITIONS"
    draw.text((right_x, ty), title_text, font=f_title, fill=(0x45, 0x0a, 0x0a))
    title_h = _th(title_text, f_title)
    ty += title_h + 15   # padding-bottom 3px×5=15px

    # border-bottom line: 7px thick, rgba(220,38,38,0.25)
    bline_color = (220, 38, 38, 64)   # 25% opacity → ~64/255
    card_rgba = card.convert('RGBA')
    bdraw = ImageDraw.Draw(card_rgba)
    bdraw.rectangle([right_x, ty, right_x + right_w, ty + 7],
                    fill=bline_color)
    card = card_rgba.convert('RGB')
    draw = ImageDraw.Draw(card)
    ty += 7 + 40   # 7px line + margin-bottom 8px×5=40px

    # Terms list: 7.5px×5=37px bold, color #1e293b, line-height 1.5
    # padding-left 12px×5=60px, margin-bottom 4px×5=20px per item
    F_TERM   = int(7.5 * S)   # 37 px
    f_term   = load_bold_font(F_TERM)
    TERM_LH  = int(F_TERM * 1.5)   # line-height 1.5
    TERM_MB  = 4 * S               # 20 px margin-bottom
    TERM_PL  = 12 * S              # 60 px padding-left (for number indent)
    TERM_W   = right_w - TERM_PL   # wrapping width

    TERMS = [
        "This card is non-transferable and remains the property of the organization.",
        "It must be presented upon request during official events and audits.",
        "If found, please return to the head office or contact info@makkalmedai.org.",
        "Subject to the rules and regulations of MAKKAL MEDAI.",
    ]

    def wrap_text(text, font, max_w):
        """Word-wrap text to fit within max_w pixels."""
        words  = text.split()
        lines  = []
        line   = ''
        for w in words:
            test = (line + ' ' + w).strip()
            if _tw(test, font) <= max_w:
                line = test
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)
        return lines

    for i, term in enumerate(TERMS):
        num_text = f"{i + 1}."
        num_w    = _tw(num_text, f_term)
        # number
        draw.text((right_x, ty), num_text, font=f_term, fill=(0x1e, 0x29, 0x3b))
        # wrapped term text
        lines = wrap_text(term, f_term, TERM_W)
        for li, line in enumerate(lines):
            draw.text((right_x + TERM_PL, ty + li * TERM_LH),
                      line, font=f_term, fill=(0x1e, 0x29, 0x3b))
        block_h = len(lines) * TERM_LH
        ty += block_h + TERM_MB

    return card.convert('RGB')


# ══════════════════════════════════════════════════════════════════
#  FRONT CARD GENERATOR
# ══════════════════════════════════════════════════════════════════

def generate_card(voter, template=None, photo_image=None, qr_image=None):
    """
    Render membership ID card front — pixel-perfect match of index.html at 5× scale.
    template arg is ignored (kept for backward compat).

    Design:
      - diamond_bg.svg full-bleed background (deep red low-poly)
      - leader.png watermark (white silhouette, right edge, opacity 0.08)
      - leader.png badge logo top-left
      - Header: "MAKKAL MEDAI" + "Membership Card"
      - Photo box + member details (name, EPIC NO, ASSEMBLY, DISTRICT)
      - Footer left: PTC code / member ID
      - QR bottom-right in white rounded box
    """
    S  = SCALE          # 5
    W  = CARD_W         # 1700
    H  = CARD_H         # 1070

    # ── Canvas: SVG background ────────────────────────────────────
    svg_path = _asset_path('diamond_bg.svg')
    bg_img   = _load_svg_background(svg_path, W, H)
    card     = bg_img.convert('RGBA')

    # ══════════════════════════════════════════════════════════════
    #  LAYER 1 — leader.png watermark (white silhouette, right edge)
    #  CSS: right:-45px; bottom:52px; height:130px; opacity:0.08;
    #       filter:brightness(0) invert(1)
    # ══════════════════════════════════════════════════════════════
    leader = _load_rgba('leader.png')
    if leader:
        lh = 130 * S                                    # 650 px
        lw = int(lh * leader.size[0] / leader.size[1])
        lm = leader.resize((lw, lh), Image.LANCZOS)
        # brightness(0) invert(1) → white silhouette: force all pixels white
        r_ch, g_ch, b_ch, a_ch = lm.split()
        white_r = a_ch.point(lambda v: 255)
        white_g = a_ch.point(lambda v: 255)
        white_b = a_ch.point(lambda v: 255)
        wm = Image.merge('RGBA', (white_r, white_g, white_b, a_ch))
        lx = W - lw + 45 * S                           # right: -45px
        ly = H - lh - 52 * S                           # bottom: 52px
        _paste_rgba(card, wm, (lx, ly), opacity=0.08)

    draw = ImageDraw.Draw(card)

    # ══════════════════════════════════════════════════════════════
    #  LAYER 2 — leader.png badge logo top-left
    #  CSS: left:18px; top:15px; height:38px
    # ══════════════════════════════════════════════════════════════
    if leader:
        bh = 38 * S                                     # 190 px
        bw = int(bh * leader.size[0] / leader.size[1])
        badge = leader.resize((bw, bh), Image.LANCZOS)
        _paste_rgba(card, badge, (18 * S, 15 * S))

    draw = ImageDraw.Draw(card)

    # ══════════════════════════════════════════════════════════════
    #  LAYER 3 — Header: "MAKKAL MEDAI" + "Membership Card"
    #  CSS: .header { padding:16px 20px 0; justify-content:center }
    #       .card-title { font-size:20px; font-weight:800; color:#fff; letter-spacing:2px }
    #       .card-subtitle { font-size:7.5px; color:rgba(255,255,255,0.9); letter-spacing:2px }
    # ══════════════════════════════════════════════════════════════
    F_TITLE_HDR = 20 * S    # 100 px
    F_SUB_HDR   = int(7.5 * S)  # 37 px
    f_title_hdr = load_bold_font(F_TITLE_HDR)
    f_sub_hdr   = load_bold_font(F_SUB_HDR)

    title_text = "MAKKAL MEDAI"
    sub_text   = "Membership Card"

    title_w    = _tw(title_text, f_title_hdr)
    sub_w      = _tw(sub_text, f_sub_hdr)
    title_h    = _th(title_text, f_title_hdr)
    sub_h      = _th(sub_text, f_sub_hdr)

    # centred horizontally; top padding: 16px×5=80px
    hdr_pad_top = 16 * S
    title_x = (W - title_w) // 2
    title_y = hdr_pad_top
    sub_x   = (W - sub_w) // 2
    sub_y   = title_y + title_h + 4 * S   # margin-top:4px

    # text-shadow: 0 2px 4px rgba(0,0,0,0.25) → offset (0, 2px×5=10)
    shadow_off = 2 * S
    draw.text((title_x, title_y + shadow_off), title_text,
              font=f_title_hdr, fill=(0, 0, 0, 64))
    draw.text((title_x, title_y), title_text,
              font=f_title_hdr, fill=(255, 255, 255))

    draw.text((sub_x, sub_y + shadow_off), sub_text,
              font=f_sub_hdr, fill=(0, 0, 0, 50))
    draw.text((sub_x, sub_y), sub_text,
              font=f_sub_hdr, fill=(255, 255, 255, 230))   # 90% opacity

    # ── Sanitize voter fields ─────────────────────────────────────
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
    #  CONTENT AREA
    #  CSS: .content { padding:10px 20px; gap:18px }
    #  Header occupies: 16px top + title + 4px + subtitle + ~2px ≈ 32px
    # ══════════════════════════════════════════════════════════════
    CONTENT_TOP = (16 + int(20 + 4 + 7.5 + 4)) * S   # ≈ 51.5 * 5 ≈ 258
    PAD_L       = 20 * S    # 100 px
    GAP         = 18 * S    # 90 px

    # ── Photo box ─────────────────────────────────────────────────
    # CSS: width:85px; height:105px; border-radius:8px; border:2.5px solid rgba(255,255,255,0.8)
    PHOTO_W  = 85 * S    # 425
    PHOTO_H  = 105 * S   # 525
    PHOTO_X  = PAD_L
    FOOTER_T = H - 14 * S  # footer bottom:14px from card bottom
    AVAIL_H  = FOOTER_T - CONTENT_TOP
    PHOTO_Y  = CONTENT_TOP + (AVAIL_H - PHOTO_H) // 2

    BR  = 8 * S            # border-radius:8px → 40px
    BW  = int(2.5 * S)     # border:2.5px → 12px

    # Draw white rounded rect behind photo (border)
    draw.rounded_rectangle(
        [PHOTO_X - BW, PHOTO_Y - BW,
         PHOTO_X + PHOTO_W + BW, PHOTO_Y + PHOTO_H + BW],
        radius=BR + BW,
        fill=(255, 255, 255, 204),   # rgba(255,255,255,0.8)
    )

    # Paste photo
    if photo_image:
        fitted = _fit_photo(photo_image, PHOTO_W, PHOTO_H)
    else:
        # Placeholder
        fitted = Image.new('RGB', (PHOTO_W, PHOTO_H), (241, 245, 249))
        pd = ImageDraw.Draw(fitted)
        cx = PHOTO_W // 2
        cr = int(PHOTO_W * 0.22)
        cy = int(PHOTO_H * 0.375)
        pd.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(203, 213, 225))
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
    card_rgb_temp = card.convert('RGB')
    card_rgb_temp.paste(fitted, (PHOTO_X, PHOTO_Y), mask=mask)
    card = card_rgb_temp.convert('RGBA')
    draw = ImageDraw.Draw(card)

    # ══════════════════════════════════════════════════════════════
    #  QR CODE — bottom-right, white rounded box
    #  CSS: right:20px; bottom:24px; width:45px; height:45px
    #       background:#fff; border:1px solid #cbd5e1; border-radius:4px; padding:3px
    # ══════════════════════════════════════════════════════════════
    QR_INNER   = 45 * S   # 225 px (QR image area including padding)
    QR_BORDER  = 1 * S    # 5 px
    QR_PAD     = 3 * S    # 15 px
    QR_OUTER   = QR_INNER + QR_BORDER * 2   # total outer box
    QR_BOX_R   = 4 * S    # border-radius:4px → 20px

    QR_X = W - 20 * S - QR_OUTER   # right: 20px from edge
    QR_Y = H - 24 * S - QR_OUTER   # bottom: 24px from edge

    # Draw white box with border
    draw.rounded_rectangle(
        [QR_X, QR_Y, QR_X + QR_OUTER, QR_Y + QR_OUTER],
        radius=QR_BOX_R,
        fill=(255, 255, 255),
        outline=(0xcb, 0xd5, 0xe1),
        width=QR_BORDER,
    )

    # Build QR if not provided
    if not qr_image:
        verify_url = voter.get('verify_url', '')
        if not verify_url:
            verify_url = f"{getattr(config, 'BASE_URL', 'http://localhost:5000')}/verify/{epic_no}"
        qr_image = _make_qr(verify_url, QR_INNER - QR_PAD * 2)

    if qr_image:
        # Keep white background for QR (new design has white QR box)
        qr_sized = qr_image.convert('RGB').resize(
            (QR_INNER - QR_PAD * 2, QR_INNER - QR_PAD * 2), Image.LANCZOS
        )
        qr_paste_x = QR_X + QR_BORDER + QR_PAD
        qr_paste_y = QR_Y + QR_BORDER + QR_PAD
        card_temp = card.convert('RGB')
        card_temp.paste(qr_sized, (qr_paste_x, qr_paste_y))
        card = card_temp.convert('RGBA')
        draw = ImageDraw.Draw(card)
    else:
        draw.rectangle(
            [QR_X + QR_BORDER + QR_PAD, QR_Y + QR_BORDER + QR_PAD,
             QR_X + QR_OUTER - QR_BORDER - QR_PAD,
             QR_Y + QR_OUTER - QR_BORDER - QR_PAD],
            fill=(220, 220, 220)
        )

    # ══════════════════════════════════════════════════════════════
    #  DETAILS BLOCK (right of photo)
    #  .member { font-size:19.5px; font-weight:800; color:#fff }
    #  .info span { width:80px; font-size:7.5px; color:rgba(255,255,255,0.8) }
    #  .info div { font-size:10.5px; font-weight:700; color:#fff }
    # ══════════════════════════════════════════════════════════════
    DET_X     = PHOTO_X + PHOTO_W + GAP
    DET_MAX_X = QR_X - 15 * S
    DET_W     = DET_MAX_X - DET_X

    F_NAME  = int(19.5 * S)    # 97 px
    F_LBL   = int(7.5 * S)     # 37 px
    F_VAL   = int(10.5 * S)    # 52 px

    f_name = load_bold_font(F_NAME)
    f_lbl  = load_bold_font(F_LBL)
    f_val  = load_bold_font(F_VAL)

    # Shrink name to fit
    fn_size = F_NAME
    while _tw(name, f_name) > DET_W and fn_size > 10 * S:
        fn_size -= S
        f_name   = load_bold_font(fn_size)

    # Label column width: CSS span width:80px×5=400px
    LBL_COL_W = 80 * S   # 400 px  (fixed, as per CSS)
    COLON_GAP = _tw(" : ", f_lbl)

    FIELDS = [
        ("EPIC NO",  epic_no),
        ("ASSEMBLY", assembly),
        ("DISTRICT", district),
    ]

    ROW_H   = _th("Mg", f_val)
    ROW_GAP = 4 * S   # margin-bottom:4px×5=20px
    MB_NAME = 6 * S   # margin-bottom:6px×5=30px

    NAME_H  = _th(name, f_name)
    N_ROWS  = len(FIELDS)
    block_h = NAME_H + MB_NAME + N_ROWS * ROW_H + (N_ROWS - 1) * ROW_GAP

    # Vertical placement with QR overflow guard (as spec'd)
    MAX_BLOCK_BOTTOM = QR_Y - 20 * S
    ideal_y = PHOTO_Y + int(AVAIL_H * 0.62)
    if ideal_y + block_h > MAX_BLOCK_BOTTOM:
        DET_Y = MAX_BLOCK_BOTTOM - block_h
    else:
        DET_Y = ideal_y

    y = DET_Y

    # — Name — (WHITE, text-shadow)
    shadow = 2 * S
    draw.text((DET_X, y + shadow), name, font=f_name, fill=(0, 0, 0, 50))
    draw.text((DET_X, y), name, font=f_name, fill=(255, 255, 255))
    y += NAME_H + MB_NAME

    # — Field rows —
    for i, (label, value) in enumerate(FIELDS):
        row_y  = y + i * (ROW_H + ROW_GAP)
        lbl_h  = _th(label, f_lbl)
        val_h  = _th(value, f_val)
        lbl_off = (val_h - lbl_h) // 2

        # Label: rgba(255,255,255,0.8) = 204 alpha
        draw.text((DET_X, row_y + lbl_off), label,
                  font=f_lbl, fill=(255, 255, 255, 204))

        # Colon
        colon_x = DET_X + LBL_COL_W
        draw.text((colon_x, row_y + lbl_off), ":",
                  font=f_lbl, fill=(255, 255, 255, 204))

        # Value: WHITE bold
        val_x = colon_x + _tw(":", f_lbl) + 4 * S
        fv, fvs = f_val, F_VAL
        max_val_w = DET_MAX_X - val_x
        while _tw(value, fv) > max_val_w and fvs > 5 * S:
            fvs -= 1
            fv   = load_bold_font(fvs)
        draw.text((val_x, row_y + shadow), value, font=fv, fill=(0, 0, 0, 40))
        draw.text((val_x, row_y), value, font=fv, fill=(255, 255, 255))

    # ══════════════════════════════════════════════════════════════
    #  FOOTER
    #  CSS: .footer { position:absolute; bottom:14px; left:20px }
    #       .footer-left { font-size:11px; font-weight:800; color:#fff; letter-spacing:0.8px }
    # ══════════════════════════════════════════════════════════════
    F_FOOT = 11 * S   # 55 px
    f_foot = load_bold_font(F_FOOT)

    foot_text = member_id.upper() if member_id else "MEMBERSHIP ID CARD"
    foot_h    = _th(foot_text, f_foot)
    foot_y    = H - 14 * S - foot_h

    draw.text((20 * S, foot_y + shadow), foot_text,
              font=f_foot, fill=(0, 0, 0, 50))
    draw.text((20 * S, foot_y), foot_text,
              font=f_foot, fill=(255, 255, 255))

    return card.convert('RGB')
