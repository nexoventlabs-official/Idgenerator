"""
WhatsApp Bot Flask Routes
==========================
Webhook endpoint for Meta WhatsApp Business Cloud API.
- GET  /webhook  → Verification (Meta challenge)
- POST /webhook  → Incoming messages
"""
import logging
from flask import Blueprint, request, jsonify
from . import wa_config
from .handler import handle_message

logger = logging.getLogger('whatsapp_bot')

whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')


@whatsapp_bp.route('/webhook', methods=['GET'])
def verify_webhook():
    """Meta webhook verification (challenge-response)."""
    mode = request.args.get('hub.mode', '')
    token = request.args.get('hub.verify_token', '')
    challenge = request.args.get('hub.challenge', '')

    if mode == 'subscribe' and token == wa_config.WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp webhook verified successfully")
        return challenge, 200

    logger.warning(f"WhatsApp webhook verification failed: mode={mode}")
    return 'Forbidden', 403


@whatsapp_bp.route('/webhook', methods=['POST'])
def receive_message():
    """Process incoming WhatsApp messages."""
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "no payload"}), 400

    try:
        entries = payload.get('entry', [])
        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                value = change.get('value', {})

                # Skip status updates (sent, delivered, read)
                if 'statuses' in value:
                    continue

                messages = value.get('messages', [])
                for msg in messages:
                    sender = msg.get('from', '')  # E.164 format e.g. "919876543210"
                    msg_id = msg.get('id', '')

                    if not sender:
                        continue

                    logger.info(f"WhatsApp msg from {sender[:4]}****: type={msg.get('type')}")

                    # Mark as read
                    from .api import mark_read
                    mark_read(msg_id)

                    # Process message
                    try:
                        handle_message(sender, msg)
                    except Exception as he:
                        logger.error(f"Handler error for {sender[:4]}****: {he}", exc_info=True)

    except Exception as e:
        logger.error(f"WhatsApp webhook processing error: {e}", exc_info=True)

    # Always return 200 to avoid Meta retries
    return jsonify({"status": "ok"}), 200
