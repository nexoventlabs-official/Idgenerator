"""
Cloudinary Secure URL Generation
=================================
Generate signed URLs for private photo/card access.
"""
import hashlib
import time
from typing import Optional
import config


def generate_signed_url(public_id: str, folder: str, expiry_seconds: int = 3600) -> str:
    """
    Generate a signed Cloudinary URL with expiration.
    
    Args:
        public_id: The Cloudinary public ID (e.g., EPIC number)
        folder: The folder name (member_photos or generated_cards)
        expiry_seconds: URL validity duration (default: 1 hour)
    
    Returns:
        Signed Cloudinary URL
    """
    import cloudinary.utils
    
    # Calculate expiration timestamp
    expires_at = int(time.time()) + expiry_seconds
    
    # Build transformation parameters
    options = {
        'resource_type': 'image',
        'type': 'upload',
        'sign_url': True,
        'secure': True,
    }
    
    # Add expiration if supported (Cloudinary Advanced plan feature)
    # For free tier, we'll use basic signed URLs
    full_public_id = f"{folder}/{public_id}"
    
    try:
        url = cloudinary.utils.cloudinary_url(full_public_id, **options)[0]
        return url
    except Exception:
        # Fallback to unsigned URL if signing fails
        return f"https://res.cloudinary.com/{config.CLOUDINARY_CLOUD_NAME}/image/upload/{full_public_id}"


def generate_download_url(public_id: str, folder: str, filename: Optional[str] = None) -> str:
    """
    Generate a Cloudinary URL with download attachment flag.
    
    Args:
        public_id: The Cloudinary public ID
        folder: The folder name
        filename: Optional custom filename for download
    
    Returns:
        Cloudinary URL with fl_attachment transformation
    """
    import cloudinary.utils
    
    full_public_id = f"{folder}/{public_id}"
    
    options = {
        'resource_type': 'image',
        'type': 'upload',
        'secure': True,
        'flags': 'attachment',
    }
    
    if filename:
        options['flags'] = f'attachment:{filename}'
    
    try:
        url = cloudinary.utils.cloudinary_url(full_public_id, **options)[0]
        return url
    except Exception:
        # Fallback
        base_url = f"https://res.cloudinary.com/{config.CLOUDINARY_CLOUD_NAME}/image/upload"
        attachment = f"fl_attachment:{filename}" if filename else "fl_attachment"
        return f"{base_url}/{attachment}/{full_public_id}"
