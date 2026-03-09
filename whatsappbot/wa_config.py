"""
WhatsApp Bot Configuration
===========================
Meta WhatsApp Business API credentials loaded from .env
"""
import os

# Meta WhatsApp Business API
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_BUSINESS_ID = os.getenv("WHATSAPP_BUSINESS_ID", "")
WHATSAPP_APP_ID = os.getenv("WHATSAPP_APP_ID", "")
WHATSAPP_WABA_ID = os.getenv("WHATSAPP_WABA_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "voter_id_whatsapp_verify_2026")

# API base URL
WHATSAPP_API_URL = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
WHATSAPP_MEDIA_URL = "https://graph.facebook.com/v21.0"
