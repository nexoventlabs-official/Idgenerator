"""
Dummy card preview — uses newtemplate.jpeg
Photo in passport-size position (bottom right), no QR.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image, ImageDraw, ImageFont
import config
from generate_cards import generate_card

OUTPUT = "dummy_card_output.jpeg"

VOTER = {
    "name":          "RAJESH KUMAR",
    "epic_no":       "KFD3622586",
    "assembly_name": "EGMORE",
    "district":      "CHENNAI",
    "ptc_code":      "PTC-A1B2C3D",
    "verify_url":    "http://localhost:5000/verify/KFD3622586",
}

# Create a realistic placeholder photo (flesh-toned rectangle)
def make_placeholder_photo(w, h):
    img = Image.new('RGB', (w, h), (210, 180, 140))   # skin tone bg
    d = ImageDraw.Draw(img)
    # head circle
    cx, cy, r = w//2, int(h*0.35), int(w*0.28)
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(230, 200, 160))
    # body
    d.rectangle([cx - int(w*0.35), int(h*0.62), cx + int(w*0.35), h],
                fill=(255, 255, 255))
    return img

template = Image.open(config.TEMPLATE_PATH)
W, H = template.size
PHOTO_W = int(H * 0.21)
PHOTO_H = int(PHOTO_W * 9 / 7)
sample_photo = make_placeholder_photo(PHOTO_W, PHOTO_H)

card = generate_card(VOTER, template, sample_photo)
card.save(OUTPUT, quality=95)
print(f"Saved: {OUTPUT}  ({W}x{H})")
print("Open dummy_card_output.jpeg to preview.")
