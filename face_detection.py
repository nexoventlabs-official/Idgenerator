"""
Face Detection Module for Photo Validation
===========================================
Validates uploaded photos to ensure they contain a clear human face.
Uses OpenCV with Haar Cascade for fast, accurate face detection.
"""

import cv2
import numpy as np
from PIL import Image
import io
import os

# Initialize face detector (Haar Cascade - lightweight and fast)
CASCADE_PATH = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
face_cascade = cv2.CascadeClassifier(CASCADE_PATH)

# Backup: eye cascade for additional validation
eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')


def detect_face_in_image(image: Image.Image) -> tuple[bool, str, dict]:
    """
    Detect if image contains a clear human face.
    
    Args:
        image: PIL Image object
    
    Returns:
        tuple: (is_valid, message, details)
            - is_valid: True if face detected, False otherwise
            - message: User-friendly message
            - details: Dict with detection info (face_count, confidence, etc.)
    """
    try:
        # Convert PIL Image to OpenCV format
        img_array = np.array(image.convert('RGB'))
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        
        # Detect faces with multiple scale factors for better accuracy
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(80, 80),  # Minimum face size (80x80 pixels)
            flags=cv2.CASCADE_SCALE_IMAGE
        )
        
        face_count = len(faces)
        
        # No face detected
        if face_count == 0:
            return False, "No face detected. Please upload a clear photo with your face visible.", {
                'face_count': 0,
                'reason': 'no_face'
            }
        
        # Multiple faces detected
        if face_count > 1:
            return False, f"Multiple faces detected ({face_count}). Please upload a photo with only your face.", {
                'face_count': face_count,
                'reason': 'multiple_faces'
            }
        
        # Single face detected - validate quality
        x, y, w, h = faces[0]
        face_roi = gray[y:y+h, x:x+w]
        
        # Check face size (should be at least 15% of image)
        img_area = gray.shape[0] * gray.shape[1]
        face_area = w * h
        face_percentage = (face_area / img_area) * 100
        
        if face_percentage < 5:
            return False, "Face is too small. Please upload a closer photo with your face clearly visible.", {
                'face_count': 1,
                'face_percentage': face_percentage,
                'reason': 'face_too_small'
            }
        
        # Check if face is too large (likely too close)
        if face_percentage > 80:
            return False, "Face is too close. Please take the photo from a normal distance.", {
                'face_count': 1,
                'face_percentage': face_percentage,
                'reason': 'face_too_large'
            }
        
        # Additional validation: Check for eyes (confirms it's a real face)
        eyes = eye_cascade.detectMultiScale(face_roi, scaleFactor=1.1, minNeighbors=3)
        
        if len(eyes) < 1:
            return False, "Face not clear. Please upload a photo with your face clearly visible and well-lit.", {
                'face_count': 1,
                'eyes_detected': len(eyes),
                'reason': 'face_not_clear'
            }
        
        # Check image brightness (too dark or too bright)
        brightness = np.mean(face_roi)
        if brightness < 40:
            return False, "Photo is too dark. Please upload a well-lit photo.", {
                'face_count': 1,
                'brightness': brightness,
                'reason': 'too_dark'
            }
        
        if brightness > 220:
            return False, "Photo is too bright. Please upload a photo with better lighting.", {
                'face_count': 1,
                'brightness': brightness,
                'reason': 'too_bright'
            }
        
        # All checks passed
        return True, "Face detected successfully!", {
            'face_count': 1,
            'face_percentage': face_percentage,
            'eyes_detected': len(eyes),
            'brightness': brightness,
            'face_position': {'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)},
            'quality': 'good'
        }
        
    except Exception as e:
        return False, f"Error processing photo: {str(e)}", {
            'error': str(e),
            'reason': 'processing_error'
        }


def validate_photo_for_id_card(file_stream) -> tuple[bool, str, Image.Image | None]:
    """
    Validate uploaded photo file for ID card generation.
    
    Args:
        file_stream: File stream from request.files
    
    Returns:
        tuple: (is_valid, message, image)
            - is_valid: True if photo is valid
            - message: User-friendly message
            - image: PIL Image object if valid, None otherwise
    """
    try:
        # Open image
        image = Image.open(file_stream).convert('RGB')
        
        # Check image dimensions
        width, height = image.size
        
        if width < 200 or height < 200:
            return False, "Photo resolution is too low. Please upload a higher quality photo (minimum 200x200 pixels).", None
        
        if width > 5000 or height > 5000:
            return False, "Photo resolution is too high. Please upload a smaller photo (maximum 5000x5000 pixels).", None
        
        # Detect face
        is_valid, message, details = detect_face_in_image(image)
        
        if is_valid:
            return True, message, image
        else:
            return False, message, None
            
    except Exception as e:
        return False, f"Invalid photo file. Please upload a valid image (JPG, PNG, or JPEG).", None


def get_face_detection_stats(image: Image.Image) -> dict:
    """
    Get detailed face detection statistics for debugging/logging.
    
    Args:
        image: PIL Image object
    
    Returns:
        dict: Detection statistics
    """
    is_valid, message, details = detect_face_in_image(image)
    return {
        'is_valid': is_valid,
        'message': message,
        **details
    }
