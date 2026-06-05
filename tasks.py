"""
Celery Tasks — Async Card Generation (MongoDB edition)
"""
import os, io, base64, uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

from celery import Celery
from PIL import Image
from pymongo import MongoClient

import config
import cloudinary
import cloudinary.uploader
from generate_cards import generate_card, generate_serial_number, setup_logging
from security_fixes import hash_pin

logger = setup_logging()

# ── Celery ────────────────────────────────────────────────────────
celery = Celery(
    'voter_card_tasks',
    broker=os.getenv('CELERY_BROKER_URL', 'memory://'),
    backend=os.getenv('CELERY_RESULT_BACKEND', 'cache+memory://'),
)
celery.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=240,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
)

# ── MongoDB ───────────────────────────────────────────────────────
_mongo_client = None

def _get_db():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=10000)
    return _mongo_client[config.MONGO_DB]

# ── Cloudinary ────────────────────────────────────────────────────
cloudinary.config(
    cloud_name=config.CLOUDINARY_CLOUD_NAME,
    api_key=config.CLOUDINARY_API_KEY,
    api_secret=config.CLOUDINARY_API_SECRET,
    secure=True,
)


def _find_voter_by_epic(epic_no: str) -> dict | None:
    db = _get_db()
    doc = db.voters.find_one({"EPIC_NO": epic_no.upper()})
    if not doc:
        return None
    voter_name = (doc.get("FM_NAME_EN") or "") + " " + (doc.get("LASTNAME_EN") or "")
    voter_name = voter_name.strip() or doc.get("VOTER_NAME", "")
    rel_name   = (doc.get("RLN_FM_NM_EN") or "") + " " + (doc.get("RLN_L_NM_EN") or "")
    rel_name   = rel_name.strip() or doc.get("RELATION_NAME", "")
    return {
        "epic_no":       doc.get("EPIC_NO", ""),
        "name":          voter_name,
        "assembly":      str(doc.get("AC_NO") or doc.get("ASSEMBLY_NO") or ""),
        "assembly_name": doc.get("ASSEMBLY_NAME", ""),
        "district":      doc.get("DISTRICT_NAME") or doc.get("DISTRICT", ""),
        "age":           doc.get("AGE", ""),
        "sex":           doc.get("GENDER", ""),
        "relation_type": doc.get("RLN_TYPE", ""),
        "relation_name": rel_name,
        "part_no":       str(doc.get("PART_NO") or ""),
        "section_no":    str(doc.get("SECTION_NO") or ""),
        "slno_in_part":  str(doc.get("SLNOINPART") or ""),
        "house_no":      doc.get("C_HOUSE_NO") or doc.get("HOUSE_NO", ""),
        "dob":           doc.get("DOB", ""),
        "FM_NAME_EN":    doc.get("FM_NAME_EN", ""),
        "LASTNAME_EN":   doc.get("LASTNAME_EN", ""),
        "AC_NO":         doc.get("AC_NO") or doc.get("ASSEMBLY_NO", ""),
        "ASSEMBLY_NAME": doc.get("ASSEMBLY_NAME", ""),
        "DISTRICT_NAME": doc.get("DISTRICT_NAME") or doc.get("DISTRICT", ""),
        "PART_NO":       doc.get("PART_NO"),
        "SECTION_NO":    doc.get("SECTION_NO"),
        "SLNOINPART":    doc.get("SLNOINPART"),
        "C_HOUSE_NO":    doc.get("C_HOUSE_NO"),
        "GENDER":        doc.get("GENDER"),
        "AGE":           doc.get("AGE"),
        "DOB":           doc.get("DOB"),
        "MOBILE_NO":     doc.get("MOBILE_NO") or doc.get("MOBILE_NUMBER"),
    }


@celery.task(bind=True, name='tasks.generate_card_async')
def generate_card_async(self, epic_no, mobile, photo_base64=None, ptc_code='',
                        referred_by_ptc='', referred_by_referral_id='', secret_pin=''):
    try:
        self.update_state(state='PROCESSING', meta={'status': 'Finding voter data'})

        voter = _find_voter_by_epic(epic_no)
        if not voter:
            return {'success': False, 'message': f'Voter {epic_no} not found', 'epic_no': epic_no}

        self.update_state(state='PROCESSING', meta={'status': 'Processing photo'})

        photo_url   = ''
        photo_image = None

        if photo_base64:
            try:
                data = base64.b64decode(photo_base64.split(',')[1] if ',' in photo_base64 else photo_base64)
                photo_image = Image.open(io.BytesIO(data))
                up = cloudinary.uploader.upload(
                    data, folder='member_photos', public_id=epic_no,
                    overwrite=True, resource_type='image'
                )
                photo_url = up['secure_url']
            except Exception as e:
                logger.error(f"Photo upload failed for {epic_no}: {e}")

        self.update_state(state='PROCESSING', meta={'status': 'Generating card'})

        voter['ptc_code']   = ptc_code
        voter['verify_url'] = f"{config.BASE_URL}/verify/{epic_no}"

        template   = Image.open(config.TEMPLATE_PATH)
        card_image = generate_card(voter, template, photo_image)

        self.update_state(state='PROCESSING', meta={'status': 'Uploading card'})

        buf = io.BytesIO()
        card_image.save(buf, format='JPEG', quality=95)
        up2      = cloudinary.uploader.upload(
            buf.getvalue(), folder='generated_cards', public_id=epic_no,
            overwrite=True, resource_type='image'
        )
        card_url = up2['secure_url']

        self.update_state(state='PROCESSING', meta={'status': 'Saving to database'})

        now    = datetime.now(timezone.utc)
        hashed = hash_pin(secret_pin) if secret_pin else None
        db     = _get_db()

        # Upsert generated_voters
        db.generated_voters.update_one(
            {"EPIC_NO": epic_no},
            {"$set": {
                "EPIC_NO":        epic_no,
                "MOBILE_NO":      mobile,
                "ptc_code":       ptc_code,
                "photo_url":      photo_url,
                "card_url":       card_url,
                "generated_at":   now,
                "secret_pin":     hashed,
                "referred_by_ptc": referred_by_ptc or None,
                "referred_by_referral_id": referred_by_referral_id or None,
                "FM_NAME_EN":     voter.get("FM_NAME_EN", ""),
                "LASTNAME_EN":    voter.get("LASTNAME_EN", ""),
                "ASSEMBLY_NAME":  voter.get("assembly_name", ""),
                "DISTRICT_NAME":  voter.get("district", ""),
                "AC_NO":          voter.get("AC_NO", ""),
            }},
            upsert=True
        )

        # Increment referrer
        if referred_by_ptc:
            db.generated_voters.update_one(
                {"ptc_code": referred_by_ptc},
                {"$inc": {"referred_members_count": 1}}
            )

        # Upsert generation_stats
        db.generation_stats.update_one(
            {"epic_no": epic_no},
            {"$set": {"card_url": card_url, "photo_url": photo_url,
                      "last_generated": now, "auth_mobile": mobile},
             "$inc": {"count": 1},
             "$setOnInsert": {"epic_no": epic_no}},
            upsert=True
        )

        return {
            'success':    True,
            'card_url':   card_url,
            'photo_url':  photo_url,
            'epic_no':    epic_no,
            'voter_name': voter.get('name', ''),
            'message':    'Card generated successfully',
        }

    except Exception as e:
        logger.error(f"Async card generation failed for {epic_no}: {e}")
        return {'success': False, 'message': str(e), 'epic_no': epic_no}
