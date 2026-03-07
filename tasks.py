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
from pymongo import MongoClient

logger = setup_logging()

# Initialize MongoDB connections for Celery workers
mongo_client = MongoClient(
    config.MONGO_URI,
    serverSelectionTimeoutMS=5000,
    maxPoolSize=50,
    minPoolSize=5,
)
db = mongo_client[config.MONGO_DB_NAME]
voters_col = db[config.MONGO_VOTERS_COLLECTION]

gen_mongo_client = MongoClient(
    config.GEN_MONGO_URI,
    serverSelectionTimeoutMS=5000,
    maxPoolSize=50,
    minPoolSize=5,
)
gen_db = gen_mongo_client[config.GEN_MONGO_DB_NAME]
gen_voters_col = gen_db[config.GEN_MONGO_COLLECTION]
stats_col = gen_db[config.MONGO_STATS_COLLECTION]

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
        
        # Find voter in database
        voter = voters_col.find_one({'epic_no': epic_no})
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
        
        # Save to generated voters collection
        from security_fixes import hash_pin
        
        doc = {
            'ptc_code': ptc_code,
            'epic_no': epic_no,
            'name': voter.get('name', ''),
            'assembly': voter.get('assembly', ''),
            'district': voter.get('district', ''),
            'mobile': mobile,
            'photo_url': photo_url,
            'card_url': card_url,
            'generated_at': datetime.now(timezone.utc).isoformat(),
        }
        
        if secret_pin:
            doc['secret_pin'] = hash_pin(secret_pin)
        if referred_by_ptc:
            doc['referred_by_ptc'] = referred_by_ptc
        if referred_by_referral_id:
            doc['referred_by_referral_id'] = referred_by_referral_id
        
        # Atomic upsert
        import pymongo.errors
        try:
            gen_voters_col.update_one(
                {'epic_no': epic_no, 'mobile': mobile},
                {'$set': doc, '$setOnInsert': {'created_at': datetime.now(timezone.utc).isoformat()}},
                upsert=True
            )
        except pymongo.errors.DuplicateKeyError:
            gen_voters_col.update_one(
                {'epic_no': epic_no, 'mobile': mobile},
                {'$set': doc}
            )
        
        # Increment referrer count
        if referred_by_ptc:
            gen_voters_col.update_one(
                {'ptc_code': referred_by_ptc},
                {'$inc': {'referred_members_count': 1}}
            )
        
        # Update stats
        stats_col.update_one(
            {'epic_no': epic_no},
            {
                '$set': {
                    'card_url': card_url,
                    'photo_url': photo_url,
                    'last_generated': datetime.now(timezone.utc).isoformat(),
                    'auth_mobile': mobile,
                },
                '$inc': {'count': 1}
            },
            upsert=True
        )
        
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
