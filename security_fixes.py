"""
Security Enhancement Module
============================
Provides password hashing, rate limiting, and input validation utilities.
"""
import hashlib
import secrets
import time
from functools import wraps
from flask import request, jsonify
import re
from typing import Optional

# ══════════════════════════════════════════════════════════════════
#  PASSWORD HASHING (PIN Security)
# ══════════════════════════════════════════════════════════════════

def hash_pin(pin: str) -> str:
    """Hash a 4-digit PIN using PBKDF2-SHA256 (secure, no external deps)."""
    salt = secrets.token_hex(16)
    pin_hash = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt.encode(), 100000)
    return f"{salt}${pin_hash.hex()}"


def verify_pin(pin: str, hashed: str) -> bool:
    """Verify a PIN against its hash."""
    try:
        salt, pin_hash = hashed.split('$')
        new_hash = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt.encode(), 100000)
        return secrets.compare_digest(new_hash.hex(), pin_hash)
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════
#  RATE LIMITING (In-Memory Store)
# ══════════════════════════════════════════════════════════════════

class RateLimiter:
    """Simple in-memory rate limiter with sliding window."""
    
    def __init__(self):
        self.requests = {}  # {key: [(timestamp, count), ...]}
    
    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, Optional[int]]:
        """
        Check if request is allowed.
        Returns: (allowed: bool, retry_after: Optional[int])
        """
        now = time.time()
        
        # Clean old entries
        if key in self.requests:
            self.requests[key] = [(ts, cnt) for ts, cnt in self.requests[key] 
                                  if now - ts < window_seconds]
        
        # Count requests in window
        if key not in self.requests:
            self.requests[key] = []
        
        total = sum(cnt for _, cnt in self.requests[key])
        
        if total >= max_requests:
            # Calculate retry_after
            oldest = min(ts for ts, _ in self.requests[key])
            retry_after = int(window_seconds - (now - oldest)) + 1
            return False, retry_after
        
        # Allow request
        self.requests[key].append((now, 1))
        return True, None
    
    def cleanup(self, max_age_seconds: int = 3600):
        """Remove entries older than max_age_seconds."""
        now = time.time()
        for key in list(self.requests.keys()):
            self.requests[key] = [(ts, cnt) for ts, cnt in self.requests[key] 
                                  if now - ts < max_age_seconds]
            if not self.requests[key]:
                del self.requests[key]


# Global rate limiter instance
rate_limiter = RateLimiter()


def rate_limit(max_requests: int, window_seconds: int, key_func=None):
    """
    Rate limiting decorator.
    
    Args:
        max_requests: Maximum requests allowed in window
        window_seconds: Time window in seconds
        key_func: Function to extract rate limit key from request (default: IP address)
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Determine rate limit key
            if key_func:
                key = key_func()
            else:
                key = request.remote_addr or 'unknown'
            
            allowed, retry_after = rate_limiter.is_allowed(
                f"{f.__name__}:{key}", max_requests, window_seconds
            )
            
            if not allowed:
                return jsonify({
                    'success': False,
                    'message': f'Rate limit exceeded. Try again in {retry_after} seconds.'
                }), 429
            
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ══════════════════════════════════════════════════════════════════
#  INPUT VALIDATION
# ══════════════════════════════════════════════════════════════════

def validate_mobile(mobile: str) -> tuple[bool, str]:
    """Validate Indian mobile number."""
    mobile = mobile.strip()
    if not mobile:
        return False, "Mobile number is required"
    if not re.match(r'^[6-9]\d{9}$', mobile):
        return False, "Invalid mobile number. Must be 10 digits starting with 6-9"
    return True, mobile


def validate_epic(epic: str) -> tuple[bool, str]:
    """Validate EPIC number format."""
    epic = epic.strip().upper()
    if not epic:
        return False, "EPIC number is required"
    # EPIC format: 3 letters + 7 digits (e.g., ABC1234567)
    if not re.match(r'^[A-Z]{3}\d{7}$', epic):
        # Allow flexible format but sanitize
        if len(epic) < 3 or len(epic) > 20:
            return False, "Invalid EPIC number format"
    return True, epic


def validate_pin(pin: str) -> tuple[bool, str]:
    """Validate 4-digit PIN."""
    pin = pin.strip()
    if not pin:
        return False, "PIN is required"
    if not re.match(r'^\d{4}$', pin):
        return False, "PIN must be exactly 4 digits"
    return True, pin


def validate_otp(otp: str) -> tuple[bool, str]:
    """Validate 6-digit OTP."""
    otp = otp.strip()
    if not otp:
        return False, "OTP is required"
    if not re.match(r'^\d{6}$', otp):
        return False, "OTP must be exactly 6 digits"
    return True, otp


def sanitize_search(search: str, max_length: int = 100) -> str:
    """Sanitize search input to prevent ReDoS and injection."""
    if not search:
        return ""
    # Limit length
    search = search[:max_length]
    # Remove special regex characters that could cause ReDoS
    # Keep only alphanumeric, spaces, and basic punctuation
    search = re.sub(r'[^\w\s\-.,@]', '', search)
    return search.strip()


def validate_file_upload(file, allowed_extensions: set, max_size_mb: int = 10) -> tuple[bool, str]:
    """
    Validate uploaded file.
    
    Args:
        file: Werkzeug FileStorage object
        allowed_extensions: Set of allowed extensions (e.g., {'jpg', 'png'})
        max_size_mb: Maximum file size in MB
    
    Returns:
        (is_valid, error_message)
    """
    if not file or not file.filename:
        return False, "No file provided"
    
    # Check extension
    if '.' not in file.filename:
        return False, "File must have an extension"
    
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in allowed_extensions:
        return False, f"Invalid file type. Allowed: {', '.join(allowed_extensions)}"
    
    # Check file size (read first chunk to verify it's not empty)
    file.seek(0, 2)  # Seek to end
    size = file.tell()
    file.seek(0)  # Reset to beginning
    
    if size == 0:
        return False, "File is empty"
    
    if size > max_size_mb * 1024 * 1024:
        return False, f"File too large. Maximum size: {max_size_mb}MB"
    
    return True, ""


# ══════════════════════════════════════════════════════════════════
#  ADMIN AUTHENTICATION HELPERS
# ══════════════════════════════════════════════════════════════════

class LoginAttemptTracker:
    """Track failed login attempts to prevent brute force."""
    
    def __init__(self):
        self.attempts = {}  # {ip: [(timestamp, username), ...]}
    
    def record_attempt(self, ip: str, username: str, success: bool):
        """Record a login attempt."""
        now = time.time()
        
        if ip not in self.attempts:
            self.attempts[ip] = []
        
        # Clean old attempts (older than 1 hour)
        self.attempts[ip] = [(ts, user) for ts, user in self.attempts[ip] 
                             if now - ts < 3600]
        
        if not success:
            self.attempts[ip].append((now, username))
    
    def is_locked(self, ip: str, max_attempts: int = 5, lockout_minutes: int = 15) -> tuple[bool, Optional[int]]:
        """
        Check if IP is locked out.
        Returns: (is_locked, retry_after_seconds)
        """
        if ip not in self.attempts:
            return False, None
        
        now = time.time()
        lockout_seconds = lockout_minutes * 60
        
        # Count recent failed attempts
        recent = [(ts, user) for ts, user in self.attempts[ip] 
                  if now - ts < lockout_seconds]
        
        if len(recent) >= max_attempts:
            oldest = min(ts for ts, _ in recent)
            retry_after = int(lockout_seconds - (now - oldest)) + 1
            return True, retry_after
        
        return False, None
    
    def reset(self, ip: str):
        """Reset attempts for an IP (after successful login)."""
        if ip in self.attempts:
            del self.attempts[ip]


# Global login attempt tracker
login_tracker = LoginAttemptTracker()
