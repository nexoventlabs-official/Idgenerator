"""
Card Generation Engine — We The Leaders / Makkal Medai
======================================================
Pixel-perfect replication of Id cards/index.html at 5× scale.

HTML card:  340 × 214 px
Output:    1700 × 1070 px  (5× scale)

Every position maps directly from CSS values × 5.
"""
import hashlib
import io
import logging
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
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
def _download_font(url, dest_path):
    try:
        logger.info(f"Downloading font from {url} to {dest_path}...")
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            with open(dest_path, 'wb') as f:
                f.write(response.read())
        logger.info(f"Successfully downloaded font: {dest_path}")
        return True
    except Exception as e:
        logger.warning(f"Failed to download font: {e}")
        return False

def load_font(size, bold=False, font_family='Montserrat'):
    # Montserrat is the premium professional font family used for everything
    font_name = 'Montserrat-ExtraBold.ttf' if bold else 'Montserrat-Bold.ttf'
    font_url = (
        'https://cdn.jsdelivr.net/gh/JulietaUla/Montserrat@master/fonts/ttf/Montserrat-ExtraBold.ttf' if bold else
        'https://cdn.jsdelivr.net/gh/JulietaUla/Montserrat@master/fonts/ttf/Montserrat-Bold.ttf'
    )

    local_path = _asset_path(font_name)
    
    # Auto-download font if missing
    if not os.path.isfile(local_path):
        _download_font(font_url, local_path)
        
    if os.path.isfile(local_path):
        try:
            return ImageFont.truetype(local_path, size)
        except Exception as e:
            logger.warning(f"Error loading truetype font {local_path}: {e}")
            
    # Fallback to system fonts or config
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

# ── Spaced Text Drawing Helpers ────────────────────────────────────
def get_text_width_with_spacing(text, font, spacing_px):
    if not text:
        return 0
    w = sum(_tw(char, font) for char in text)
    w += spacing_px * (len(text) - 1)
    return w

def draw_text_with_spacing(draw, xy, text, font, fill, spacing_px, align='left'):
    x, y = xy
    if align == 'center':
        total_w = get_text_width_with_spacing(text, font, spacing_px)
        x = x - total_w // 2
    elif align == 'right':
        total_w = get_text_width_with_spacing(text, font, spacing_px)
        x = x - total_w
        
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += _tw(char, font) + spacing_px

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
    img    = photo.resize((nw, nh), Image.Resampling.LANCZOS)
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
        return img.resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        return None


# ── SVG Background Renderer ───────────────────────────────────────
def render_svg_bg(W, H, scale):
    svg_path = _asset_path('diamond_bg.svg')
    if not os.path.isfile(svg_path):
        # Fallback: create base red gradient
        bg = Image.new('RGBA', (W, H), (179, 0, 0, 255))
        return bg

    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        
        # Build radial gradient at 1x scale and resize to preserve performance
        w_small, h_small = 340, 214
        bg_small = Image.new('RGBA', (w_small, h_small))
        draw_small = ImageDraw.Draw(bg_small)
        
        cx_s, cy_s, r_s = int(w_small * 0.60), int(h_small * 0.30), int(w_small * 0.70)
        max_d = int((w_small**2 + h_small**2)**0.5)
        for d in range(max_d, -1, -1):
            t = d / r_s
            if t > 1.0:
                t = 1.0
            if t <= 0.5:
                factor = t / 0.5
                color = (
                    int(255 + factor * (179 - 255)),
                    int(26 + factor * (0 - 26)),
                    int(26 + factor * (0 - 26)),
                    255
                )
            else:
                factor = (t - 0.5) / 0.5
                color = (
                    int(179 + factor * (64 - 179)),
                    int(0 + factor * (0 - 0)),
                    int(0 + factor * (0 - 0)),
                    255
                )
            draw_small.ellipse([cx_s - d, cy_s - d, cx_s + d, cy_s + d], fill=color)
        
        bg = bg_small.resize((W, H), Image.Resampling.LANCZOS)
        
        # Parse all polygon tags
        polys = []
        for child in root.iter():
            if child.tag.endswith('polygon'):
                polys.append(child)
                
        for poly in polys:
            attrib = poly.attrib
            pts_str = attrib.get('points', '')
            fill_hex = attrib.get('fill', '#ffffff')
            opacity = float(attrib.get('opacity', '1.0'))
            
            # Parse points
            pts = []
            for pt in pts_str.strip().split():
                coords = pt.split(',')
                if len(coords) == 2:
                    pts.append((float(coords[0]) * scale, float(coords[1]) * scale))
            
            if not pts:
                continue
                
            fill_hex = fill_hex.lstrip('#')
            fill_rgb = tuple(int(fill_hex[i:i+2], 16) for i in (0, 2, 4))
            
            # Draw using bounding-box crop overlay to optimize speed
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            min_x, max_x = int(min(xs)), int(max(xs))
            min_y, max_y = int(min(ys)), int(max(ys))
            
            min_x, min_y = max(0, min_x - 1), max(0, min_y - 1)
            max_x, max_y = min(W, max_x + 1), min(H, max_y + 1)
            box_w = max_x - min_x
            box_h = max_y - min_y
            if box_w <= 0 or box_h <= 0:
                continue
                
            crop_overlay = Image.new('RGBA', (box_w, box_h), (0, 0, 0, 0))
            draw_crop = ImageDraw.Draw(crop_overlay)
            crop_pts = [(x - min_x, y - min_y) for x, y in pts]
            
            alpha = int(opacity * 255)
            draw_crop.polygon(crop_pts, fill=fill_rgb + (alpha,), outline=fill_rgb + (alpha,))
            bg.paste(crop_overlay, (min_x, min_y), mask=crop_overlay)
            
        return bg
    except Exception as e:
        logger.error(f"Error rendering SVG background: {e}")
        return Image.new('RGBA', (W, H), (179, 0, 0, 255))


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

def generate_back_card(voter=None, qr_image=None):
    """
    Render the back side of the membership card.
    Background: 135deg linear-gradient (#fdfbf7 to #faf6eb)
    Watermark:  leader.png centered at 6% opacity, size 600x600 px
    QR block:   QR code at 400x400 px, positioned at (112, 297) px with 'SCAN TO VERIFY' label
    Terms block:Terms list aligned right starting at X=605 px, vertically centered.
    Output:     1700 x 1070 RGB
    """
    S = SCALE
    W, H = CARD_W, CARD_H

    # 1. Background linear gradient
    bg = Image.new('RGBA', (W, H))
    draw = ImageDraw.Draw(bg)
    
    for d in range(W + H):
        t = d / (W + H)
        r = int(253 + t * (250 - 253))
        g = int(251 + t * (246 - 251))
        b = int(247 + t * (235 - 247))
        x_start = max(0, d - H)
        x_end = min(W - 1, d)
        y_start = d - x_start
        y_end = d - x_end
        draw.line([(x_start, y_start), (x_end, y_end)], fill=(r, g, b, 255))

    # 2. Centered watermark (leader.png)
    leader = _load_rgba('leader.png')
    if leader:
        wm_w = 120 * S  # 600 px
        wm_h = int(wm_w * leader.size[1] / leader.size[0])
        wm_img = leader.resize((wm_w, wm_h), Image.Resampling.LANCZOS)
        
        wx = (W - wm_w) // 2
        wy = (H - wm_h) // 2
        _paste_rgba(bg, wm_img, (wx, wy), opacity=0.06)

    # 3. QR code & label (Left section bounds: X=100 to 525)
    QR_W = 80 * S   # 400 px
    QR_H = QR_W
    QR_X = 100 + (85 * S - QR_W) // 2  # 112 px
    QR_Y = 80 + (910 - 475) // 2       # 297 px

    if not qr_image:
        epic_no = voter.get('epic_no', 'ABC1234567') if voter else 'ABC1234567'
        verify_url = voter.get('verify_url', '') if voter else ''
        if not verify_url:
            verify_url = f"{getattr(config, 'BASE_URL', 'https://wetheleaders.org')}/verify/{epic_no}"
        qr_image = _make_qr(verify_url, QR_W)

    if qr_image:
        # Make white pixels transparent and paste QR code
        qr_rgba = qr_image.convert('RGBA')
        pixels = qr_rgba.load()
        for py in range(qr_rgba.height):
            for px in range(qr_rgba.width):
                r, g, b, a = pixels[px, py]
                if r > 200 and g > 200 and b > 200:
                    pixels[px, py] = (255, 255, 255, 0)
                else:
                    pixels[px, py] = (30, 41, 59, 255)
        bg.paste(qr_rgba, (QR_X, QR_Y), mask=qr_rgba.split()[3])

    # Draw QR Label: "Scan to Verify" with 4px letter spacing
    f_qr_lbl = load_font(int(7 * S), bold=True, font_family='Plus Jakarta Sans')
    qr_lbl = "SCAN TO VERIFY"
    qr_lbl_w = get_text_width_with_spacing(qr_lbl, f_qr_lbl, int(0.8 * S))
    qr_lbl_x = 100 + (85 * S - qr_lbl_w) // 2
    qr_lbl_y = QR_Y + QR_H + 8 * S
    draw_text_with_spacing(draw, (qr_lbl_x, qr_lbl_y), qr_lbl, font=f_qr_lbl, fill=(30, 41, 59, 255), spacing_px=int(0.8 * S))

    # 4. Terms & Conditions (Right section: X=605 to 1600)
    RIGHT_X = 100 + 85 * S + 16 * S  # 605 px
    RIGHT_W = W - 100 - RIGHT_X      # 995 px
    
    f_title = load_font(int(10.5 * S), bold=True, font_family='Outfit')
    f_list = load_font(int(7.5 * S), bold=True, font_family='Plus Jakarta Sans')
    
    terms_title = "TERMS & CONDITIONS"
    title_h = _th(terms_title, f_title)
    
    items = [
        "This card is non-transferable and remains the property of the organization.",
        "It must be presented upon request during official events and audits.",
        "If found, please return to the head office or contact info@makkalmedai.org.",
        "Subject to the rules and regulations of MAKKAL MEDAI."
    ]
    
    # Wrap text and calculate right block heights dynamically
    list_line_height = int(7.5 * S * 1.5)  # 57 px
    list_item_margin = 4 * S               # 20 px
    list_padding_left = 12 * S             # 60 px
    
    wrapped_items = []
    total_list_h = 0
    
    for item in items:
        max_text_w = RIGHT_W - list_padding_left
        words = item.split(' ')
        lines = []
        curr_line = []
        for word in words:
            test_line = ' '.join(curr_line + [word])
            if get_text_width_with_spacing(test_line, f_list, 0) <= max_text_w:
                curr_line.append(word)
            else:
                lines.append(' '.join(curr_line))
                curr_line = [word]
        if curr_line:
            lines.append(' '.join(curr_line))
            
        item_h = len(lines) * list_line_height
        wrapped_items.append((lines, item_h))
        total_list_h += item_h + list_item_margin
        
    if wrapped_items:
        total_list_h -= list_item_margin
        
    title_section_h = title_h + 40 + 8 + 15 + 12
    total_right_h = title_section_h + total_list_h
    
    right_y = 80 + (910 - total_right_h) // 2

    # Draw Title with letter spacing
    draw_text_with_spacing(draw, (RIGHT_X, right_y), terms_title, font=f_title, fill=(69, 10, 10, 255), spacing_px=int(1.0 * S))
    
    # Draw underline border-bottom: 1.5px solid rgba(220, 38, 38, 0.25)
    line_y = right_y + title_h + 15
    draw.line([(RIGHT_X, line_y), (W - 100, line_y)], fill=(220, 38, 38, 64), width=8)
    
    # Draw list items
    curr_y = right_y + title_section_h
    for i, (lines, item_h) in enumerate(wrapped_items):
        # Draw list number
        num_str = f"{i+1}."
        draw.text((RIGHT_X, curr_y), num_str, font=f_list, fill=(30, 41, 59, 255))
        
        # Draw wrapped lines
        text_x = RIGHT_X + list_padding_left
        line_y_offset = curr_y
        for line in lines:
            draw.text((text_x, line_y_offset), line, font=f_list, fill=(30, 41, 59, 255))
            line_y_offset += list_line_height
            
        curr_y += item_h + list_item_margin

    return bg.convert('RGB')


# ══════════════════════════════════════════════════════════════════
#  FRONT CARD GENERATOR
# ══════════════════════════════════════════════════════════════════

def generate_card(voter, template=None, photo_image=None, qr_image=None):
    """
    Render membership ID card — pixel-perfect match of index.html at 5× scale.
    """
    S  = SCALE
    W  = CARD_W
    H  = CARD_H

    # Render SVG background
    card_rgba = render_svg_bg(W, H, S)

    # Apply 60% black overlay on background to highlight text
    black_overlay = Image.new('RGBA', card_rgba.size, (0, 0, 0, int(0.60 * 255)))
    card_rgba = Image.alpha_composite(card_rgba, black_overlay)

    draw = ImageDraw.Draw(card_rgba)

    # Sanitize voter inputs
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

    # Title Casing to match mockups
    name_display = name.title()
    assembly_display = assembly.title()
    district_display = district.title()

    # 1. Front Leader Image (colored, bottom-right corner)
    # CSS: right: 0px; bottom: 0px; height: 115px; opacity: 1.0
    leader = _load_rgba('leader.png')
    if leader:
        lh = 115 * S
        lw = int(lh * leader.size[0] / leader.size[1])
        lm = leader.resize((lw, lh), Image.Resampling.LANCZOS)
        lx = W - lw
        ly = H - lh
        _paste_rgba(card_rgba, lm, (lx, ly), opacity=1.0)

    # 3. Centered Header
    # CSS: padding-top: 16px + margin-top: 2px = 18px Y. Outfit bold.
    # Calibrated font size for true visual alignment ( आउटफिट Bold, size 16 -> 80px )
    f_title = load_font(16 * S, bold=True, font_family='Outfit')
    f_sub = load_font(int(6.5 * S), bold=True, font_family='Outfit')

    title_text = "MAKKAL MEDAI"
    ascent, descent = f_title.getmetrics()
    title_h = ascent + descent
    title_y = 18 * S
    draw_text_with_spacing(draw, (W // 2, title_y), title_text, font=f_title, fill=(255, 255, 255), spacing_px=int(2.0 * S), align='center')

    sub_text = "MEMBERSHIP CARD"  # Mockup has uppercase Subtitle
    sub_y = title_y + title_h + 3 * S
    draw_text_with_spacing(draw, (W // 2, sub_y), sub_text, font=f_sub, fill=(255, 255, 255, 230), spacing_px=int(2.0 * S), align='center')

    # 4. Photo Frame (No border and no drop-shadow)
    # CSS: X=20px (100), Y=56px (280), width=72px (360), height=90px (450)
    PHOTO_W = 72 * S
    PHOTO_H = 90 * S
    PHOTO_X = 20 * S
    PHOTO_Y = 56 * S
    BR = 8 * S

    photo_box = Image.new('RGBA', (PHOTO_W, PHOTO_H), (255, 255, 255, 0))
    if photo_image:
        fitted = _fit_photo(photo_image, PHOTO_W, PHOTO_H)
        photo_box.paste(fitted.convert('RGBA'), (0, 0))
    else:
        # Fallback placeholder SVG
        photo_box_draw = ImageDraw.Draw(photo_box)
        cx = PHOTO_W // 2
        cr = int(PHOTO_W * 0.22)
        cy = int(PHOTO_H * 0.375)
        photo_box_draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(203, 213, 225, 255))
        photo_box_draw.polygon([
            (int(PHOTO_W * 0.10), PHOTO_H),
            (int(PHOTO_W * 0.90), PHOTO_H),
            (int(PHOTO_W * 0.78), int(PHOTO_H * 0.60)),
            (int(PHOTO_W * 0.22), int(PHOTO_H * 0.60)),
        ], fill=(203, 213, 225, 255))

    # Paste photo inside rounded corner mask
    mask = Image.new('L', (PHOTO_W, PHOTO_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, PHOTO_W - 1, PHOTO_H - 1], radius=BR, fill=255)
    card_rgba.paste(photo_box, (PHOTO_X, PHOTO_Y), mask=mask)

    # 5. Details Section (Right of photo: gap 18px (90 px) -> X=630)
    # Calibrated details sizes (Outfit Bold 15 -> 75px, Plus Jakarta Sans Bold 9 -> 45px)
    DET_X = PHOTO_X + PHOTO_W + 18 * S
    DET_MAX_X = W - 20 * S
    DET_W = DET_MAX_X - DET_X

    f_member = load_font(int(15 * S), bold=True, font_family='Outfit')
    f_val = load_font(int(9 * S), bold=True, font_family='Plus Jakarta Sans')
    f_lbl = load_font(int(7 * S), bold=True, font_family='Outfit')

    # Auto-shrink member name to fit bounds
    while get_text_width_with_spacing(name_display, f_member, int(0.2 * S)) > DET_W and f_member.size > 8 * S:
        f_member = load_font(f_member.size - 2, bold=True, font_family='Outfit')

    ascent, descent = f_member.getmetrics()
    name_h = ascent + descent
    row_h = f_val.getmetrics()[0] + f_val.getmetrics()[1]
    
    # Vertically align top of name to start near the top inside photo box
    det_y = PHOTO_Y + 4 * S

    # Draw name
    draw_text_with_spacing(draw, (DET_X, det_y), name_display, font=f_member, fill=(255, 255, 255, 255), spacing_px=int(0.2 * S))
    
    # Draw list fields (Spelled EXACTLY like mockup with "EPIC NO")
    fields = [
        ("EPIC NO", epic_no),
        ("ASSEMBLY", assembly_display),
        ("DISTRICT", district_display)
    ]
    
    span_width = 80 * S
    colon_w = _tw(": ", f_val)
    
    y = det_y + name_h + 12 * S
    for label, val in fields:
        # Draw labels (Outfit bold with 1px letter spacing, white 80% opacity)
        draw_text_with_spacing(draw, (DET_X, y), label, font=f_lbl, fill=(255, 255, 255, 204), spacing_px=int(1.0 * S))
        
        # Draw colons
        draw.text((DET_X + span_width, y), ": ", font=f_val, fill=(255, 255, 255, 255))
        
        # Draw values (Plus Jakarta Sans Bold)
        val_x = DET_X + span_width + colon_w
        fv = f_val
        while get_text_width_with_spacing(val, fv, 0) > (DET_MAX_X - val_x) and fv.size > 6 * S:
            fv = load_font(fv.size - 2, bold=True, font_family='Plus Jakarta Sans')
        draw.text((val_x, y), val, font=fv, fill=(255, 255, 255, 255))
        
        y += row_h + 4 * S

    # 6. Footer Member ID (Left aligned: X=20px (100), bottom margin: 14px (70))
    # Calibrated size (Plus Jakarta Sans Bold 10 -> 50px) with 0.8px letter spacing
    f_foot = load_font(10 * S, bold=True, font_family='Plus Jakarta Sans')
    foot_text = member_id
    foot_h = f_foot.getmetrics()[0] + f_foot.getmetrics()[1]
    foot_x = 20 * S
    foot_y = H - 14 * S - foot_h
    draw_text_with_spacing(draw, (foot_x, foot_y), foot_text, font=f_foot, fill=(255, 255, 255, 255), spacing_px=int(0.8 * S))

    return card_rgba.convert('RGB')
