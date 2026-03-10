"""
Celery Tasks for Async Card Generation
========================================
Background job processing for card generation to prevent blocking workers.
"""

import os
from celery import Celery
from datetime import datetime, timezone
from PIL import Image
import io
import base64

# Initialize Celery
celery = Celery(
    'voter_card_tasks',
    broker=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('REDIS_URL', 'redis://localhost:6379/0')
)

# Celery configuration
celery.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max
    task_soft_time_limit=240,  # 4 minutes soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
)

# Import after Celery initialization to avoid circular imports
import config
from generate_cards import generate_card, generate_serial_number, setup_logging
import cloudinary
import cloudinary.uploader
import pymysql
from dbutils.pooled_db import PooledDB

logger = setup_logging()

# Initialize MySQL connection pool for Celery workers (voter data - read-only)
mysql_voters_pool = PooledDB(
    creator=pymysql,
    maxconnections=5,
    mincached=1,
    maxcached=3,
    blocking=True,
    host=config.MYSQL_HOST,
    port=config.MYSQL_PORT,
    user=config.MYSQL_USER,
    passwd=config.MYSQL_PASSWORD,
    db=config.MYSQL_VOTERS_DB,
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor,
    autocommit=True,
)
VOTERS_TABLE = config.MYSQL_VOTERS_TABLE

# Load assembly table mapping for multi-table voter queries
_ASSEMBLY_TABLES_TASKS = []
try:
    _conn = mysql_voters_pool.connection()
    with _conn.cursor() as _cur:
        _cur.execute("SELECT table_name, assembly_name, district_name FROM tbl_assembly_consitituency ORDER BY assembly_name")
        _ASSEMBLY_TABLES_TASKS = _cur.fetchall()
    _conn.close()
except Exception:
    pass

# Initialize MySQL connection pool for Celery workers (generated data - read/write)
mysql_pool = PooledDB(
    creator=pymysql,
    maxconnections=10,
    mincached=2,
    maxcached=5,
    blocking=True,
    host=config.MYSQL_HOST,
    port=config.MYSQL_PORT,
    user=config.MYSQL_USER,
    passwd=config.MYSQL_PASSWORD,
    db=config.MYSQL_DB,
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor,
    autocommit=True,
)


def _translate_voter_row(row: dict) -> dict | None:
    """Map MySQL column names to internal field names."""
    if not row:
        return None
    return {
        'epic_no': row.get('EPIC_NO') or '',
        'name': f"{row.get('FM_NAME_EN') or ''} {row.get('LASTNAME_EN') or ''}".strip(),
        'assembly': str(row.get('AC_NO') or ''),
        'age': row.get('AGE') or '',
        'sex': row.get('GENDER') or '',
        'relation_type': row.get('RLN_TYPE') or '',
        'relation_name': f"{row.get('RLN_FM_NM_EN') or ''} {row.get('RLN_L_NM_EN') or ''}".strip(),
        'mobile': row.get('MOBILE_NO') or '',
        'part_no': str(row.get('PART_NO') or ''),
        'dob': row.get('DOB') or '',
        'id': row.get('id', ''),
    }


def _find_voter_by_epic(epic_no: str):
    """Find a voter in MySQL by EPIC number (searches across all voter tables)."""
    conn = mysql_voters_pool.connection()
    try:
        with conn.cursor() as cur:
            for tbl in _ASSEMBLY_TABLES_TASKS:
                table_name = tbl['table_name']
                cur.execute(f"SELECT * FROM `{table_name}` WHERE `EPIC_NO` = %s LIMIT 1", (epic_no,))
                row = cur.fetchone()
                if row:
                    return _translate_voter_row(row)
        return None
    finally:
        conn.close()


def _get_mysql():
    """Get a connection from the MySQL pool."""
    return mysql_pool.connection()


# Initialize Cloudinary
cloudinary.config(
    cloud_name=config.CLOUDINARY_CLOUD_NAME,
    api_key=config.CLOUDINARY_API_KEY,
    api_secret=config.CLOUDINARY_API_SECRET,
    secure=True
)


@celery.task(bind=True, name='tasks.generate_card_async')
def generate_card_async(self, epic_no, mobile, photo_base64=None, ptc_code='', 
                       referred_by_ptc='', referred_by_referral_id='', secret_pin=''):
    """
    Generate voter ID card asynchronously.
    
    Args:
        epic_no: Voter EPIC number
        mobile: Mobile number
        photo_base64: Base64 encoded photo (optional)
        ptc_code: PTC code for this voter
        referred_by_ptc: Referrer's PTC code (optional)
        referred_by_referral_id: Referral ID (optional)
        secret_pin: Secret PIN (optional)
    
    Returns:
        dict: {
            'success': bool,
            'card_url': str,
            'photo_url': str,
            'epic_no': str,
            'message': str
        }
    """
    try:
        # Update task state
        self.update_state(state='PROCESSING', meta={'status': 'Finding voter data'})
        
        # Find voter in MySQL
        voter = _find_voter_by_epic(epic_no)
        if not voter:
            return {
                'success': False,
                'message': f'Voter with EPIC {epic_no} not found',
                'epic_no': epic_no
            }
        
        # Update task state
        self.update_state(state='PROCESSING', meta={'status': 'Processing photo'})
        
        # Handle photo upload
        photo_url = ''
        photo_image = None
        
        if photo_base64:
            try:
                # Decode base64 photo
                photo_data = base64.b64decode(photo_base64.split(',')[1] if ',' in photo_base64 else photo_base64)
                photo_image = Image.open(io.BytesIO(photo_data))
                
                # Upload photo to Cloudinary
                upload_result = cloudinary.uploader.upload(
                    photo_data,
                    folder='member_photos',
                    public_id=epic_no,
                    overwrite=True,
                    resource_type='image'
                )
                photo_url = upload_result['secure_url']
                logger.info(f"Photo uploaded for {epic_no}: {photo_url}")
                
            except Exception as e:
                logger.error(f"Photo upload failed for {epic_no}: {e}")
                # Continue without photo
        
        # Update task state
        self.update_state(state='PROCESSING', meta={'status': 'Generating card'})
        
        # Set verify URL and PTC code on voter for QR code generation
        voter['ptc_code'] = ptc_code
        voter['verify_url'] = f"{config.BASE_URL}/verify/{epic_no}"
        
        # Load template
        template_path = os.path.join(config.BASE_DIR, 'template.jpeg')
        template = Image.open(template_path)
        
        # Generate card
        card_image = generate_card(voter, template, photo_image)
        
        # Update task state
        self.update_state(state='PROCESSING', meta={'status': 'Uploading card'})
        
        # Convert card to bytes
        card_buffer = io.BytesIO()
        card_image.save(card_buffer, format='JPEG', quality=95)
        card_buffer.seek(0)
        
        # Upload card to Cloudinary
        card_upload = cloudinary.uploader.upload(
            card_buffer.getvalue(),
            folder='generated_cards',
            public_id=epic_no,
            overwrite=True,
            resource_type='image'
        )
        card_url = card_upload['secure_url']
        logger.info(f"Card generated for {epic_no}: {card_url}")
        
        # Update task state
        self.update_state(state='PROCESSING', meta={'status': 'Updating database'})
        
        # Save to generated voters
        from security_fixes import hash_pin
        
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        hashed = hash_pin(secret_pin) if secret_pin else None
        
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                # Upsert generated voter (all voter columns + generation columns)
                cur.execute(
                    "INSERT INTO generated_voters ("
                    "AC_NO, ASSEMBLY_NAME, PART_NO, SECTION_NO, SLNOINPART, C_HOUSE_NO, C_HOUSE_NO_V1, "
                    "FM_NAME_EN, LASTNAME_EN, FM_NAME_V1, LASTNAME_V1, "
                    "RLN_TYPE, RLN_FM_NM_EN, RLN_L_NM_EN, RLN_FM_NM_V1, RLN_L_NM_V1, "
                    "EPIC_NO, GENDER, AGE, DOB, MOBILE_NO, ORG_LIST_NO, DISTRICT_NAME, "
                    "ptc_code, photo_url, card_url, generated_at, secret_pin, "
                    "referred_by_ptc, referred_by_referral_id, created_at"
                    ") VALUES ("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s,%s,%s"
                    ") ON DUPLICATE KEY UPDATE "
                    "AC_NO=VALUES(AC_NO), ASSEMBLY_NAME=VALUES(ASSEMBLY_NAME), "
                    "PART_NO=VALUES(PART_NO), SECTION_NO=VALUES(SECTION_NO), "
                    "SLNOINPART=VALUES(SLNOINPART), C_HOUSE_NO=VALUES(C_HOUSE_NO), "
                    "C_HOUSE_NO_V1=VALUES(C_HOUSE_NO_V1), FM_NAME_EN=VALUES(FM_NAME_EN), "
                    "LASTNAME_EN=VALUES(LASTNAME_EN), FM_NAME_V1=VALUES(FM_NAME_V1), "
                    "LASTNAME_V1=VALUES(LASTNAME_V1), RLN_TYPE=VALUES(RLN_TYPE), "
                    "RLN_FM_NM_EN=VALUES(RLN_FM_NM_EN), RLN_L_NM_EN=VALUES(RLN_L_NM_EN), "
                    "RLN_FM_NM_V1=VALUES(RLN_FM_NM_V1), RLN_L_NM_V1=VALUES(RLN_L_NM_V1), "
                    "GENDER=VALUES(GENDER), AGE=VALUES(AGE), DOB=VALUES(DOB), "
                    "ORG_LIST_NO=VALUES(ORG_LIST_NO), DISTRICT_NAME=VALUES(DISTRICT_NAME), "
                    "ptc_code=VALUES(ptc_code), photo_url=VALUES(photo_url), card_url=VALUES(card_url), "
                    "generated_at=VALUES(generated_at), "
                    "secret_pin=COALESCE(VALUES(secret_pin), secret_pin), "
                    "referred_by_ptc=VALUES(referred_by_ptc), "
                    "referred_by_referral_id=VALUES(referred_by_referral_id)",
                    (
                        voter.get('AC_NO', voter.get('assembly')),
                        voter.get('ASSEMBLY_NAME', voter.get('assembly_name')),
                        voter.get('PART_NO', voter.get('part_no')),
                        voter.get('SECTION_NO'), voter.get('SLNOINPART'),
                        voter.get('C_HOUSE_NO'), voter.get('C_HOUSE_NO_V1'),
                        voter.get('FM_NAME_EN', voter.get('name', '').split(' ')[0] if voter.get('name') else ''),
                        voter.get('LASTNAME_EN', ' '.join(voter.get('name', '').split(' ')[1:]) if voter.get('name') else ''),
                        voter.get('FM_NAME_V1'), voter.get('LASTNAME_V1'),
                        voter.get('RLN_TYPE', voter.get('relation_type')),
                        voter.get('RLN_FM_NM_EN'), voter.get('RLN_L_NM_EN'),
                        voter.get('RLN_FM_NM_V1'), voter.get('RLN_L_NM_V1'),
                        epic_no,
                        voter.get('GENDER', voter.get('sex')),
                        voter.get('AGE', voter.get('age')),
                        voter.get('DOB', voter.get('dob')),
                        mobile,
                        voter.get('ORG_LIST_NO'),
                        voter.get('DISTRICT_NAME'),
                        ptc_code, photo_url, card_url, now, hashed,
                        referred_by_ptc or None, referred_by_referral_id or None, now
                    )
                )
                
                # Increment referrer count
                if referred_by_ptc:
                    cur.execute(
                        "UPDATE generated_voters SET referred_members_count = referred_members_count + 1 "
                        "WHERE ptc_code = %s", (referred_by_ptc,)
                    )
                
                # Update stats
                cur.execute(
                    "INSERT INTO generation_stats (epic_no, card_url, photo_url, last_generated, auth_mobile, count) "
                    "VALUES (%s, %s, %s, %s, %s, 1) "
                    "ON DUPLICATE KEY UPDATE card_url=VALUES(card_url), photo_url=VALUES(photo_url), "
                    "last_generated=VALUES(last_generated), auth_mobile=VALUES(auth_mobile), count=count+1",
                    (epic_no, card_url, photo_url, now, mobile)
                )
        finally:
            conn.close()
        
        logger.info(f"Card generation completed for {epic_no}")
        
        return {
            'success': True,
            'card_url': card_url,
            'photo_url': photo_url,
            'epic_no': epic_no,
            'voter_name': voter.get('name', ''),
            'message': 'Card generated successfully'
        }
        
    except Exception as e:
        logger.error(f"Card generation failed for {epic_no}: {e}")
        return {
            'success': False,
            'message': f'Card generation failed: {str(e)}',
            'epic_no': epic_no
        }
