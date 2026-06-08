"""
Dummy card preview — new ATM-ratio design (1700 × 1071)
Run: python generate_dummy_card.py
Opens: dummy_card_output.jpeg
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image, ImageDraw
import config
from generate_cards import generate_card, CARD_W, CARD_H

OUTPUT = "dummy_card_output.jpeg"

VOTER = {
    "name":          "RAJESH KUMAR",
    "epic_no":       "KFD3622586",
    "assembly_name": "EGMORE",
    "district":      "CHENNAI",
    "ptc_code":      "PTC-A1B2C3",
    "verify_url":    "http://localhost:5000/verify/KFD3622586",
}

# ── Realistic placeholder passport photo ─────────────────────────
def make_placeholder_photo(w, h):
    img = Image.new('RGB', (w, h), (241, 245, 249))
    d   = ImageDraw.Draw(img)
    cx  = w // 2
    # head
    r = int(w * 0.28)
    cy_head = int(h * 0.33)
    d.ellipse([cx - r, cy_head - r, cx + r, cy_head + r],
              fill=(220, 190, 160))
    # neck
    nw = int(w * 0.14)
    d.rectangle([cx - nw, cy_head + r - 4, cx + nw, int(h * 0.60)],
                fill=(220, 190, 160))
    # shirt
    d.polygon([
        (int(w * 0.05), h),
        (int(w * 0.95), h),
        (int(w * 0.82), int(h * 0.60)),
        (int(w * 0.18), int(h * 0.60)),
    ], fill=(100, 130, 200))
    return img

# template arg is ignored in new design — pass None
template      = None
sample_photo  = make_placeholder_photo(425, 525)   # proportional to PHOTO box

card = generate_card(VOTER, template, sample_photo)
card.save(OUTPUT, quality=95)
print(f"Saved: {OUTPUT}  ({CARD_W}x{CARD_H})")
print("Open dummy_card_output.jpeg to preview.")
