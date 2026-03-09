"""
WhatsApp Bot Conversation Handler
===================================
Manages conversation state and processes incoming messages
to replicate the website chatbot flow on WhatsApp.

Flow for NEW users:
  1. User sends Hi → Reply with their number + Confirm button
  2. Confirm clicked → Send OTP via SMS
  3. User enters OTP → Verify
  4. Ask EPIC number
  5. User enters EPIC → Validate & show voter details + Confirm button
  6. Confirm clicked → Ask for photo
  7. User sends photo → Face detection + Generate card
  8. Show card image → Ask to set PIN
  9. User enters PIN → Ask to confirm PIN
  10. User enters PIN again → Save PIN, done

Flow for RETURNING users (number already registered):
  1. User sends Hi → Detected existing account → Ask PIN
  2. User enters PIN → Show ID card + Main Menu list

Main Menu (list message – mirrors website sidebar):
  - View ID Card
  - View Profile
  - Booth Info
  - Referral Link
  - My Members
  - Volunteer Request
  - Booth Agent Request
"""
import io
import logging
import re
import secrets
from datetime import datetime, timezone

from PIL import Image

from . import api

logger = logging.getLogger('whatsapp_bot')

# ──────────────────────────────────────────────────────────────────
#  CONVERSATION STATES
# ──────────────────────────────────────────────────────────────────
STATE_IDLE = "idle"
STATE_AWAITING_CONFIRM = "awaiting_confirm"
STATE_AWAITING_OTP = "awaiting_otp"
STATE_AWAITING_EPIC = "awaiting_epic"
STATE_AWAITING_EPIC_CONFIRM = "awaiting_epic_confirm"
STATE_AWAITING_PHOTO = "awaiting_photo"
STATE_AWAITING_SET_PIN = "awaiting_set_pin"
STATE_AWAITING_CONFIRM_PIN = "awaiting_confirm_pin"
STATE_AWAITING_PIN_LOGIN = "awaiting_pin_login"
STATE_AWAITING_FORGOT_OTP = "awaiting_forgot_otp"
STATE_AWAITING_RESET_PIN = "awaiting_reset_pin"
STATE_AWAITING_RESET_CONFIRM_PIN = "awaiting_reset_confirm_pin"
STATE_AWAITING_PHOTO_MODE = "awaiting_photo_mode"
STATE_CONFIRM_VOLUNTEER = "confirm_volunteer"
STATE_CONFIRM_BOOTH_AGENT = "confirm_booth_agent"
STATE_MENU = "menu"


def _get_db_collections():
    """Lazy import to avoid circular imports – returns the DB collections from app.py."""
    from app import (
        voters_col, gen_voters_col, stats_col, otp_col,
        verified_mobiles_col, volunteer_requests_col,
        booth_agent_requests_col, find_voter_by_epic,
        generate_ptc_code, get_or_create_referral,
    )
    return {
        'voters_col': voters_col,
        'gen_voters_col': gen_voters_col,
        'stats_col': stats_col,
        'otp_col': otp_col,
        'verified_mobiles_col': verified_mobiles_col,
        'volunteer_requests_col': volunteer_requests_col,
        'booth_agent_requests_col': booth_agent_requests_col,
        'find_voter_by_epic': find_voter_by_epic,
        'generate_ptc_code': generate_ptc_code,
        'get_or_create_referral': get_or_create_referral,
    }


# ──────────────────────────────────────────────────────────────────
#  IN-MEMORY SESSION STORE (keyed by WhatsApp phone number)
# ──────────────────────────────────────────────────────────────────
# For production scale, replace with Redis. Adequate for single-instance.
_sessions: dict[str, dict] = {}


def _get_session(phone: str) -> dict:
    if phone not in _sessions:
        _sessions[phone] = {"state": STATE_IDLE}
    return _sessions[phone]


def _clear_session(phone: str):
    _sessions.pop(phone, None)


# ──────────────────────────────────────────────────────────────────
#  MAIN ENTRY – called from routes.py for every incoming message
# ──────────────────────────────────────────────────────────────────

def handle_message(phone: str, message: dict):
    """Route an incoming WhatsApp message to the correct handler.

    Parameters
    ----------
    phone : str
        Sender's WhatsApp number in E.164 format (e.g. "919876543210")
    message : dict
        The message object from the webhook payload.
    """
    msg_type = message.get("type", "")
    sess = _get_session(phone)
    state = sess.get("state", STATE_IDLE)

    # Extract text / button id / list row id
    text = ""
    button_id = ""
    list_row_id = ""

    if msg_type == "text":
        text = message.get("text", {}).get("body", "").strip()
    elif msg_type == "interactive":
        interactive = message.get("interactive", {})
        itype = interactive.get("type", "")
        if itype == "button_reply":
            button_id = interactive.get("button_reply", {}).get("id", "")
        elif itype == "list_reply":
            list_row_id = interactive.get("list_reply", {}).get("id", "")
    elif msg_type == "image":
        # Photo upload
        pass  # handled below based on state

    # ── Universal reset: "hi", "hello", "start", "menu" restarts ──
    if text.lower() in ("hi", "hello", "start", "hey", "menu", "0"):
        # If user is already authenticated in this session, skip PIN and show menu
        if sess.get('authenticated'):
            sess['state'] = STATE_MENU
            return _send_main_menu(phone, "What would you like to do?")

        # Protect mid-flow states – remind user what's needed instead of resetting
        _mid_flow_reminders = {
            STATE_AWAITING_OTP: "⏳ You have an OTP pending.\n\nPlease enter the *6-digit OTP* sent to your mobile.\n\n_Type only numbers (e.g. 123456)_\n\nOr send *restart* to start over.",
            STATE_AWAITING_EPIC: "⏳ Please enter your *EPIC number* (Voter ID number).\n\n_Format: 3 letters + 7 digits (e.g. ABC1234567)_\n\nOr send *restart* to start over.",
            STATE_AWAITING_EPIC_CONFIRM: "⏳ Please confirm your voter details using the buttons above.\n\nOr send *restart* to start over.",
            STATE_AWAITING_PHOTO_MODE: "⏳ Please select how you'd like to send your photo using the buttons above.\n\nOr send *restart* to start over.",
            STATE_AWAITING_PHOTO: "⏳ Please send your *photo* for the ID card.\n\nOr send *restart* to start over.",
            STATE_AWAITING_SET_PIN: "⏳ Please set a *4-digit PIN* to secure your ID card.\n\n_Type only numbers (e.g. 1234)_\n\nOr send *restart* to start over.",
            STATE_AWAITING_CONFIRM_PIN: "⏳ Please *re-enter the same PIN* to confirm.\n\n_Type your PIN again_\n\nOr send *restart* to start over.",
            STATE_AWAITING_FORGOT_OTP: "⏳ You requested a PIN reset. Please enter the *6-digit OTP* sent to your mobile.\n\n_Type only numbers (e.g. 123456)_\n\nOr send *restart* to start over.",
            STATE_AWAITING_RESET_PIN: "⏳ Please enter a new *4-digit PIN*.\n\n_Type only numbers (e.g. 1234)_\n\nOr send *restart* to start over.",
            STATE_AWAITING_RESET_CONFIRM_PIN: "⏳ Please *re-enter the new PIN* to confirm.\n\n_Type your PIN again_\n\nOr send *restart* to start over.",
        }
        reminder = _mid_flow_reminders.get(state)
        if reminder:
            api.send_text(phone, reminder)
            return

        _clear_session(phone)
        return _handle_greeting(phone)

    # ── Force restart: "restart" always clears and starts fresh ──
    if text.lower() == "restart":
        _clear_session(phone)
        return _handle_greeting(phone)

    # ── Route by state ──
    if state == STATE_AWAITING_CONFIRM:
        return _handle_confirm(phone, button_id, text)
    elif state == STATE_AWAITING_OTP:
        return _handle_otp(phone, text)
    elif state == STATE_AWAITING_EPIC:
        return _handle_epic(phone, text)
    elif state == STATE_AWAITING_EPIC_CONFIRM:
        return _handle_epic_confirm(phone, button_id, text)
    elif state == STATE_AWAITING_PHOTO_MODE:
        return _handle_photo_mode(phone, button_id, text, message, msg_type)
    elif state == STATE_AWAITING_PHOTO:
        return _handle_photo(phone, message, msg_type)
    elif state == STATE_AWAITING_SET_PIN:
        return _handle_set_pin(phone, text)
    elif state == STATE_AWAITING_CONFIRM_PIN:
        return _handle_confirm_pin(phone, text)
    elif state == STATE_AWAITING_PIN_LOGIN:
        return _handle_pin_login(phone, text, button_id)
    elif state == STATE_AWAITING_FORGOT_OTP:
        return _handle_forgot_otp(phone, text)
    elif state == STATE_AWAITING_RESET_PIN:
        return _handle_reset_pin(phone, text)
    elif state == STATE_AWAITING_RESET_CONFIRM_PIN:
        return _handle_reset_confirm_pin(phone, text)
    elif state == STATE_MENU:
        return _handle_menu_selection(phone, list_row_id, button_id, text)
    elif state == STATE_CONFIRM_VOLUNTEER:
        return _handle_confirm_volunteer(phone, button_id, text)
    elif state == STATE_CONFIRM_BOOTH_AGENT:
        return _handle_confirm_booth_agent(phone, button_id, text)
    else:
        # Unknown state – treat as greeting
        _clear_session(phone)
        return _handle_greeting(phone)


# ══════════════════════════════════════════════════════════════════
#  STEP 1: GREETING
# ══════════════════════════════════════════════════════════════════

def _handle_greeting(phone: str):
    """User says Hi – check if returning or new."""
    db = _get_db_collections()
    # Extract 10-digit mobile (Indian format: 91XXXXXXXXXX)
    mobile_10 = phone[-10:] if len(phone) >= 10 else phone

    # Check if this number already has a card
    stat = db['stats_col'].find_one({'auth_mobile': mobile_10}, {'epic_no': 1, 'card_url': 1, 'secret_pin': 1})

    if stat and stat.get('card_url'):
        # RETURNING USER
        sess = _get_session(phone)
        sess['mobile'] = mobile_10
        sess['epic_no'] = stat.get('epic_no', '')

        if stat.get('secret_pin'):
            # Has PIN – ask for it
            sess['state'] = STATE_AWAITING_PIN_LOGIN
            api.send_buttons(
                phone,
                f"🙏 Welcome back!\n\nYour number: *{mobile_10}*\n\nEnter your *4-digit PIN* to view your ID card.\n\n_Type only numbers (e.g. 1234)_",
                [
                    {"id": "forgot_pin", "title": "Forgot PIN"},
                ],
                header="Voter ID Card",
                footer="Enter your PIN or tap Forgot PIN",
            )
        else:
            # No PIN yet – go straight to menu
            sess['authenticated'] = True
            sess['state'] = STATE_MENU
            _send_main_menu(phone, "Welcome back! Your ID card is ready.")
        return

    # NEW USER – show number and ask to confirm
    sess = _get_session(phone)
    sess['mobile'] = mobile_10
    sess['state'] = STATE_AWAITING_CONFIRM

    api.send_buttons(
        phone,
        f"🙏 Welcome to *Voter ID Card Generator*!\n\nYour WhatsApp number: *{mobile_10}*\n\nTap Confirm to start generating your ID card.",
        [
            {"id": "confirm_number", "title": "✅ Confirm"},
        ],
        header="Voter ID Card",
        footer="Tap Confirm to proceed",
    )


# ══════════════════════════════════════════════════════════════════
#  STEP 2: CONFIRM NUMBER → SEND OTP
# ══════════════════════════════════════════════════════════════════

def _handle_confirm(phone: str, button_id: str, text: str):
    """User tapped Confirm or typed something."""
    sess = _get_session(phone)
    mobile = sess.get('mobile', phone[-10:])

    if button_id != "confirm_number" and text.lower() not in ("confirm", "yes"):
        api.send_text(phone, "Please tap the *Confirm* button to proceed.")
        return

    # WhatsApp number is already verified — skip OTP, go to EPIC entry
    db = _get_db_collections()

    # Mark mobile as verified
    db['verified_mobiles_col'].update_one(
        {'mobile': mobile},
        {'$set': {
            'mobile': mobile,
            'verified_at': datetime.now(timezone.utc).isoformat(),
            'verified_at_dt': datetime.now(timezone.utc),
            'source': 'whatsapp',
        }},
        upsert=True,
    )

    # Check if already has a card (edge case: registered on website)
    stat = db['stats_col'].find_one({'auth_mobile': mobile}, {'epic_no': 1, 'card_url': 1})
    if stat and stat.get('card_url'):
        sess['epic_no'] = stat.get('epic_no', '')
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "Your ID card is already generated!")
        return

    # New registration – ask for EPIC
    sess['state'] = STATE_AWAITING_EPIC
    api.send_text(phone, "📋 Please enter your *EPIC Number* (Voter ID number).\n\nFormat: *3 letters + 7 digits*\nExample: *ABC1234567*\n\n_First type 3 letters, then 7 numbers_")


def _send_otp(phone: str, mobile: str):
    """Generate and send OTP, update session."""
    import os
    import requests as http_requests

    db = _get_db_collections()
    sess = _get_session(phone)

    otp = str(secrets.randbelow(900000) + 100000)

    db['otp_col'].update_one(
        {'mobile': mobile},
        {'$set': {
            'otp': otp,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'created_at_dt': datetime.now(timezone.utc),
            'verified': False,
        }},
        upsert=True,
    )

    # Send via 2Factor.in
    otp_sent = False
    sms_api_key = os.getenv('SMS_API_KEY', '')
    if sms_api_key:
        try:
            resp = http_requests.get(
                f'https://2factor.in/API/V1/{sms_api_key}/SMS/{mobile}/{otp}',
                timeout=15,
            )
            if resp.status_code == 200 and resp.json().get('Status') == 'Success':
                otp_sent = True
        except Exception as e:
            logger.warning(f"OTP send failed for {mobile[:2]}****{mobile[-2:]}: {e}")

    if otp_sent:
        sess['state'] = STATE_AWAITING_OTP
        api.send_text(
            phone,
            f"📱 OTP sent to *{mobile}* via SMS.\n\nPlease enter the *6-digit OTP* code:\n\n_Type only numbers (e.g. 123456)_",
        )
    else:
        api.send_text(
            phone,
            "❌ Failed to send OTP. Please try again later.",
        )
        _clear_session(phone)


# ══════════════════════════════════════════════════════════════════
#  STEP 3: VERIFY OTP
# ══════════════════════════════════════════════════════════════════

def _handle_otp(phone: str, text: str):
    """User enters OTP."""
    sess = _get_session(phone)
    mobile = sess.get('mobile', phone[-10:])
    db = _get_db_collections()

    otp = text.strip()
    if not re.match(r'^\d{6}$', otp):
        api.send_text(phone, "Please enter a valid *6-digit OTP*.\n\n_Type only numbers (e.g. 123456)_")
        return

    doc = db['otp_col'].find_one({'mobile': mobile})
    if not doc or doc.get('otp') != otp:
        api.send_text(phone, "❌ Invalid OTP. Please try again.\n\n_Type the 6-digit number from your SMS_")
        return

    # Check expiry
    try:
        created = datetime.fromisoformat(doc['created_at'])
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            api.send_text(phone, "⏰ OTP expired. Send *Hi* to start again.")
            _clear_session(phone)
            return
    except Exception:
        pass

    # Mark verified
    db['otp_col'].update_one({'mobile': mobile}, {'$set': {'verified': True}})

    api.send_text(phone, "✅ OTP verified successfully!")

    # Check if already has a card (edge case: registered on website)
    stat = db['stats_col'].find_one({'auth_mobile': mobile}, {'epic_no': 1, 'card_url': 1})
    if stat and stat.get('card_url'):
        sess['epic_no'] = stat.get('epic_no', '')
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "Your ID card is already generated!")
        return

    # New registration – ask for EPIC
    sess['state'] = STATE_AWAITING_EPIC
    api.send_text(phone, "📋 Please enter your *EPIC Number* (Voter ID number).\n\nFormat: *3 letters + 7 digits*\nExample: *ABC1234567*\n\n_First type 3 letters, then 7 numbers_")


# ══════════════════════════════════════════════════════════════════
#  STEP 4: VALIDATE EPIC
# ══════════════════════════════════════════════════════════════════

def _handle_epic(phone: str, text: str):
    """User enters EPIC number."""
    sess = _get_session(phone)
    db = _get_db_collections()

    epic_no = text.strip().upper()
    if len(epic_no) < 3 or len(epic_no) > 20:
        api.send_text(phone, "❌ Invalid EPIC format.\n\nFormat: *3 letters + 7 digits*\nExample: *ABC1234567*\n\n_First type 3 letters, then 7 numbers_")
        return

    voter = db['find_voter_by_epic'](epic_no)
    if not voter:
        api.send_text(phone, "❌ EPIC Number not found. Please check and try again.")
        return

    # Store voter details
    sess['epic_no'] = epic_no
    sess['voter'] = voter
    sess['state'] = STATE_AWAITING_EPIC_CONFIRM

    # Build detail text
    details = (
        f"📋 *Voter Details Found:*\n\n"
        f"*Name:* {voter.get('name', '-')}\n"
        f"*EPIC No:* {epic_no}\n"
        f"*Assembly:* {voter.get('assembly', '-')}\n"
        f"*District:* {voter.get('district', '-')}\n"
    )
    if voter.get('age'):
        details += f"*Age:* {voter.get('age')}\n"
    if voter.get('sex'):
        details += f"*Gender:* {voter.get('sex')}\n"
    if voter.get('relation_name'):
        details += f"*{voter.get('relation_type', 'Relation')}:* {voter.get('relation_name')}\n"

    details += "\nIs this correct?"

    api.send_buttons(
        phone,
        details,
        [
            {"id": "epic_confirm", "title": "✅ Confirm"},
            {"id": "epic_retry", "title": "🔄 Re-enter"},
        ],
        footer="Tap Confirm if details are correct",
    )


# ══════════════════════════════════════════════════════════════════
#  STEP 5: CONFIRM EPIC → ASK PIN
# ══════════════════════════════════════════════════════════════════

def _handle_epic_confirm(phone: str, button_id: str, text: str):
    """User confirms or re-enters EPIC."""
    sess = _get_session(phone)

    if button_id == "epic_retry" or text.lower() in ("retry", "re-enter", "change"):
        sess['state'] = STATE_AWAITING_EPIC
        api.send_text(phone, "📋 Please enter your *EPIC Number* again.\n\nFormat: *3 letters + 7 digits*\nExample: *ABC1234567*")
        return

    if button_id != "epic_confirm" and text.lower() not in ("confirm", "yes", "correct"):
        api.send_text(phone, "Please tap *Confirm* or *Re-enter* button.")
        return

    # Ask to set PIN first, before photo
    sess['state'] = STATE_AWAITING_SET_PIN
    api.send_text(
        phone,
        "🔐 Set a *4-digit PIN* to secure your ID card.\n\n"
        "You will need this PIN to view your card in the future.\n\n"
        "Enter a *4-digit numeric PIN*:\n_Type only numbers (e.g. 1234)_",
    )


# ══════════════════════════════════════════════════════════════════
#  STEP 6: RECEIVE PHOTO → GENERATE CARD
# ══════════════════════════════════════════════════════════════════

def _handle_photo_mode(phone: str, button_id: str, text: str, message: dict, msg_type: str):
    """User chose camera/upload or directly sent a photo."""
    sess = _get_session(phone)

    # If user directly sends a photo in this state, process it
    if msg_type == "image":
        sess['state'] = STATE_AWAITING_PHOTO
        return _handle_photo(phone, message, msg_type)

    if button_id == "photo_camera":
        sess['state'] = STATE_AWAITING_PHOTO
        api.send_text(
            phone,
            "📷 *Take a Photo*\n\n"
            "Tap the *📎 attachment icon* (bottom left) → select *Camera* 📷\n\n"
            "Take a clear photo of your face and send it.",
        )
    elif button_id == "photo_upload":
        sess['state'] = STATE_AWAITING_PHOTO
        api.send_text(
            phone,
            "🖼️ *Upload Photo*\n\n"
            "Tap the *📎 attachment icon* (bottom left) → select *Gallery* 🖼️\n\n"
            "Choose a clear passport-size photo and send it.",
        )
    else:
        api.send_buttons(
            phone,
            "Please choose how to send your photo:",
            [
                {"id": "photo_camera", "title": "📷 Open Camera"},
                {"id": "photo_upload", "title": "🖼️ Upload Photo"},
            ],
            footer="Select an option below",
        )


def _handle_photo(phone: str, message: dict, msg_type: str):
    """User sends a photo."""
    sess = _get_session(phone)
    db = _get_db_collections()

    if msg_type != "image":
        api.send_buttons(
            phone,
            "📸 Please send a *photo* (image), not text.\n\nChoose how to send:",
            [
                {"id": "photo_camera", "title": "📷 Open Camera"},
                {"id": "photo_upload", "title": "🖼️ Upload Photo"},
            ],
            footer="Tap an option or send a photo",
        )
        sess['state'] = STATE_AWAITING_PHOTO_MODE
        return

    image_data = message.get("image", {})
    media_id = image_data.get("id", "")
    if not media_id:
        api.send_text(phone, "❌ Could not receive the image. Please try again.")
        return

    api.send_text(phone, "⏳ Processing your photo... Please wait.")

    # Download media
    photo_bytes = api.download_media(media_id)
    if not photo_bytes:
        api.send_text(phone, "❌ Failed to download your photo. Please send it again.")
        return

    # Face detection
    from face_detection import validate_photo_for_id_card
    photo_stream = io.BytesIO(photo_bytes)
    is_valid, face_msg, photo_image = validate_photo_for_id_card(photo_stream)

    if not is_valid:
        api.send_text(
            phone,
            f"❌ Photo validation failed: {face_msg}\n\n"
            "Please send another photo with a clear, single face.",
        )
        return

    # Generate card
    epic_no = sess.get('epic_no', '')
    voter = sess.get('voter', {})
    mobile = sess.get('mobile', phone[-10:])

    try:
        result = _generate_and_upload_card(voter, epic_no, mobile, photo_image, db)
    except Exception as e:
        logger.error(f"Card generation failed for {epic_no}: {e}")
        api.send_text(phone, "❌ Card generation failed. Please try again later.")
        return

    sess['card_url'] = result['card_url']
    sess['photo_url'] = result['photo_url']
    sess['ptc_code'] = result['ptc_code']

    # Save hashed PIN (set earlier in the flow)
    hashed_pin = sess.get('hashed_pin', '')
    if hashed_pin:
        db['stats_col'].update_one({'auth_mobile': mobile}, {'$set': {'secret_pin': hashed_pin}})
        db['gen_voters_col'].update_one({'mobile': mobile}, {'$set': {'secret_pin': hashed_pin}})
        sess.pop('hashed_pin', None)

    # Show the generated card
    api.send_image(
        phone,
        result['card_url'],
        caption=f"🎉 Your ID card has been generated!\n\n*Name:* {voter.get('name', '')}\n*EPIC:* {epic_no}",
    )

    # Registration complete → menu
    sess['authenticated'] = True
    sess['state'] = STATE_MENU
    _send_main_menu(phone, "Your registration is complete! 🎉")


def _generate_and_upload_card(voter: dict, epic_no: str, mobile: str,
                               photo_image: Image.Image, db: dict) -> dict:
    """Generate ID card image, upload to Cloudinary, save to DB.
    Returns dict with card_url, photo_url, ptc_code.
    """
    import cloudinary
    import cloudinary.uploader
    import config
    from generate_cards import generate_card

    # Upload photo to Cloudinary
    photo_buf = io.BytesIO()
    photo_image.save(photo_buf, format='JPEG', quality=95)
    photo_buf.seek(0)

    photo_upload = cloudinary.uploader.upload(
        photo_buf.getvalue(),
        folder='member_photos',
        public_id=epic_no,
        overwrite=True,
        resource_type='image',
    )
    photo_url = photo_upload['secure_url']

    # Generate PTC code
    ptc_code = db['generate_ptc_code']()
    voter['ptc_code'] = ptc_code
    voter['verify_url'] = f"{config.BASE_URL}/verify/{epic_no}"

    # Generate card
    template = Image.open(config.TEMPLATE_PATH)
    card_image = generate_card(voter, template, photo_image)

    card_buf = io.BytesIO()
    card_image.save(card_buf, format='JPEG', quality=95)
    card_buf.seek(0)

    card_upload = cloudinary.uploader.upload(
        card_buf.getvalue(),
        folder='generated_cards',
        public_id=epic_no,
        overwrite=True,
        resource_type='image',
    )
    card_url = card_upload['secure_url']

    # Save to DB
    import pymongo.errors
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
        'source': 'whatsapp',
    }

    try:
        db['gen_voters_col'].update_one(
            {'epic_no': epic_no, 'mobile': mobile},
            {'$set': doc, '$setOnInsert': {'created_at': datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except pymongo.errors.DuplicateKeyError:
        db['gen_voters_col'].update_one(
            {'epic_no': epic_no, 'mobile': mobile},
            {'$set': doc},
        )

    # Update stats
    db['stats_col'].update_one(
        {'epic_no': epic_no},
        {
            '$set': {
                'card_url': card_url,
                'photo_url': photo_url,
                'last_generated': datetime.now(timezone.utc).isoformat(),
                'auth_mobile': mobile,
            },
            '$inc': {'count': 1},
        },
        upsert=True,
    )

    # Mark mobile as verified
    db['verified_mobiles_col'].update_one(
        {'mobile': mobile},
        {'$set': {
            'mobile': mobile,
            'epic_no': epic_no,
            'verified_at': datetime.now(timezone.utc).isoformat(),
            'verified_at_dt': datetime.now(timezone.utc),
        }},
        upsert=True,
    )

    return {'card_url': card_url, 'photo_url': photo_url, 'ptc_code': ptc_code}


# ══════════════════════════════════════════════════════════════════
#  STEP 7: SET PIN
# ══════════════════════════════════════════════════════════════════

def _handle_set_pin(phone: str, text: str):
    """User enters a 4-digit PIN."""
    sess = _get_session(phone)

    pin = text.strip()
    if not re.match(r'^\d{4}$', pin):
        api.send_text(phone, "❌ PIN must be exactly *4 digits*.\n\n_Type only numbers (e.g. 1234)_")
        return

    sess['pin'] = pin
    sess['state'] = STATE_AWAITING_CONFIRM_PIN
    api.send_text(phone, "🔐 Please *re-enter the same 4-digit PIN* to confirm:\n\n_Type your PIN again_")


def _handle_confirm_pin(phone: str, text: str):
    """User confirms the PIN."""
    sess = _get_session(phone)
    db = _get_db_collections()

    pin = text.strip()
    if pin != sess.get('pin', ''):
        api.send_text(phone, "❌ PINs don't match.\n\nEnter a *4-digit numeric PIN* again:\n_Type only numbers (e.g. 1234)_")
        sess['state'] = STATE_AWAITING_SET_PIN
        sess.pop('pin', None)
        return

    mobile = sess.get('mobile', phone[-10:])

    # Hash and save PIN
    from security_fixes import hash_pin
    hashed = hash_pin(pin)

    db['stats_col'].update_one({'auth_mobile': mobile}, {'$set': {'secret_pin': hashed}})
    db['gen_voters_col'].update_one({'mobile': mobile}, {'$set': {'secret_pin': hashed}})

    sess.pop('pin', None)
    logger.info(f"PIN set via WhatsApp for {mobile[:2]}****{mobile[-2:]}")

    # If new registration (no card yet), ask for photo next
    if not sess.get('card_url'):
        from security_fixes import hash_pin
        hashed = hash_pin(pin)
        sess['hashed_pin'] = hashed
        sess.pop('pin', None)
        logger.info(f"PIN set via WhatsApp for {mobile[:2]}****{mobile[-2:]}")

        api.send_text(phone, "✅ PIN set successfully!")

        # Ask for photo with camera/upload options
        sess['state'] = STATE_AWAITING_PHOTO_MODE
        api.send_buttons(
            phone,
            "📸 Now send your *passport-size photo*.\n\n"
            "Requirements:\n"
            "• Clear face visible\n"
            "• Only one person in photo\n"
            "• Good lighting\n"
            "• No sunglasses or mask\n\n"
            "Choose how to send your photo:",
            [
                {"id": "photo_camera", "title": "📷 Open Camera"},
                {"id": "photo_upload", "title": "🖼️ Upload Photo"},
            ],
            header="Upload Photo",
            footer="Select an option below",
        )
        return

    api.send_text(phone, "✅ PIN set successfully! Your ID card is secured.")

    # Show main menu
    sess['authenticated'] = True
    sess['state'] = STATE_MENU
    _send_main_menu(phone, "Your registration is complete! 🎉")


# ══════════════════════════════════════════════════════════════════
#  RETURNING USER: PIN LOGIN
# ══════════════════════════════════════════════════════════════════

def _handle_pin_login(phone: str, text: str, button_id: str):
    """Returning user enters PIN or taps Forgot PIN."""
    sess = _get_session(phone)
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    # Forgot PIN flow
    if button_id == "forgot_pin" or text.lower() in ("forgot", "forgot pin", "reset"):
        _send_otp(phone, mobile)
        sess['state'] = STATE_AWAITING_FORGOT_OTP
        return

    pin = text.strip()
    if not re.match(r'^\d{4}$', pin):
        api.send_text(phone, "Please enter a valid *4-digit PIN*.\n\n_Type only numbers (e.g. 1234)_")
        return

    stat = db['stats_col'].find_one({'auth_mobile': mobile}, {'secret_pin': 1, 'epic_no': 1, 'card_url': 1})
    if not stat or not stat.get('secret_pin'):
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "Welcome back!")
        return

    from security_fixes import verify_pin
    if not verify_pin(pin, stat['secret_pin']):
        api.send_buttons(
            phone,
            "❌ Invalid PIN. Please try again.",
            [{"id": "forgot_pin", "title": "Forgot PIN"}],
            footer="Enter correct PIN or tap Forgot PIN",
        )
        return

    # PIN correct → show card & menu
    sess['authenticated'] = True
    sess['epic_no'] = stat.get('epic_no', '')
    card_url = stat.get('card_url', '')
    if card_url:
        gen_doc = db['gen_voters_col'].find_one({'mobile': mobile}, {'name': 1})
        name = gen_doc.get('name', '') if gen_doc else ''
        api.send_image(
            phone,
            card_url,
            caption=f"🪪 Your ID Card\n*Name:* {name}\n*EPIC:* {sess.get('epic_no', '')}",
        )

    sess['state'] = STATE_MENU
    _send_main_menu(phone, "What would you like to do?")


# ══════════════════════════════════════════════════════════════════
#  FORGOT PIN → OTP → RESET PIN
# ══════════════════════════════════════════════════════════════════

def _handle_forgot_otp(phone: str, text: str):
    """User enters OTP for PIN reset."""
    sess = _get_session(phone)
    mobile = sess.get('mobile', phone[-10:])
    db = _get_db_collections()

    otp = text.strip()
    if not re.match(r'^\d{6}$', otp):
        api.send_text(phone, "Please enter a valid *6-digit OTP*.\n\n_Type only numbers (e.g. 123456)_")
        return

    doc = db['otp_col'].find_one({'mobile': mobile})
    if not doc or doc.get('otp') != otp:
        api.send_text(phone, "❌ Invalid OTP. Please try again.\n\n_Type the 6-digit number from your SMS_")
        return

    try:
        created = datetime.fromisoformat(doc['created_at'])
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            api.send_text(phone, "⏰ OTP expired. Send *Hi* to start again.")
            _clear_session(phone)
            return
    except Exception:
        pass

    sess['state'] = STATE_AWAITING_RESET_PIN
    api.send_text(phone, "✅ OTP verified!\n\nEnter a new *4-digit numeric PIN*:\n_Type only numbers (e.g. 1234)_")


def _handle_reset_pin(phone: str, text: str):
    """User enters new PIN during reset."""
    sess = _get_session(phone)
    pin = text.strip()
    if not re.match(r'^\d{4}$', pin):
        api.send_text(phone, "❌ PIN must be exactly *4 digits*.\n\n_Type only numbers (e.g. 1234)_")
        return

    sess['reset_pin'] = pin
    sess['state'] = STATE_AWAITING_RESET_CONFIRM_PIN
    api.send_text(phone, "🔐 Please *re-enter the new 4-digit PIN* to confirm:\n\n_Type your PIN again_")


def _handle_reset_confirm_pin(phone: str, text: str):
    """User confirms new PIN during reset."""
    sess = _get_session(phone)
    db = _get_db_collections()

    pin = text.strip()
    if pin != sess.get('reset_pin', ''):
        api.send_text(phone, "❌ PINs don't match.\n\nEnter a new *4-digit numeric PIN*:\n_Type only numbers (e.g. 1234)_")
        sess['state'] = STATE_AWAITING_RESET_PIN
        sess.pop('reset_pin', None)
        return

    mobile = sess.get('mobile', phone[-10:])

    from security_fixes import hash_pin
    hashed = hash_pin(pin)

    db['stats_col'].update_one({'auth_mobile': mobile}, {'$set': {'secret_pin': hashed}})
    db['gen_voters_col'].update_one({'mobile': mobile}, {'$set': {'secret_pin': hashed}})
    db['otp_col'].delete_one({'mobile': mobile})

    sess.pop('reset_pin', None)
    logger.info(f"PIN reset via WhatsApp for {mobile[:2]}****{mobile[-2:]}")

    api.send_text(phone, "✅ PIN reset successfully!")

    sess['authenticated'] = True
    # Show card and menu
    stat = db['stats_col'].find_one({'auth_mobile': mobile}, {'card_url': 1, 'epic_no': 1})
    if stat and stat.get('card_url'):
        sess['epic_no'] = stat.get('epic_no', '')
        gen_doc = db['gen_voters_col'].find_one({'mobile': mobile}, {'name': 1})
        name = gen_doc.get('name', '') if gen_doc else ''
        api.send_image(phone, stat['card_url'],
                       caption=f"🪪 Your ID Card\n*Name:* {name}\n*EPIC:* {sess.get('epic_no', '')}")

    sess['state'] = STATE_MENU
    _send_main_menu(phone, "What would you like to do?")


# ══════════════════════════════════════════════════════════════════
#  MAIN MENU (List Message – mirrors website sidebar)
# ══════════════════════════════════════════════════════════════════

def _send_main_menu(phone: str, intro_text: str):
    """Send the main menu as a WhatsApp list message."""
    api.send_list(
        phone,
        f"{intro_text}\n\nSelect an option from the menu below:",
        "📋 Menu",
        [
            {
                "title": "ID Card",
                "rows": [
                    {"id": "menu_view_card", "title": "🪪 View ID Card", "description": "View & download your ID card"},
                    {"id": "menu_profile", "title": "👤 View Profile", "description": "View your voter profile details"},
                    {"id": "menu_booth", "title": "🗳️ Booth Info", "description": "View polling station info"},
                ],
            },
            {
                "title": "Community",
                "rows": [
                    {"id": "menu_referral", "title": "🔗 Referral Link", "description": "Get your unique referral link"},
                    {"id": "menu_members", "title": "👥 My Members", "description": "View referred members"},
                    {"id": "menu_volunteer", "title": "🙋 Volunteer", "description": "Submit volunteer request"},
                    {"id": "menu_booth_agent", "title": "🏢 Booth Agent", "description": "Submit booth agent request"},
                ],
            },
        ],
        header="Main Menu",
        footer="Send 'Hi' anytime to restart",
    )


# ══════════════════════════════════════════════════════════════════
#  MENU ACTION HANDLERS
# ══════════════════════════════════════════════════════════════════

def _handle_menu_selection(phone: str, list_row_id: str, button_id: str, text: str):
    """Handle menu item selection."""
    sess = _get_session(phone)
    action = list_row_id or button_id or text.strip().lower()

    handlers = {
        "menu_view_card": _menu_view_card,
        "menu_profile": _menu_profile,
        "menu_booth": _menu_booth,
        "menu_referral": _menu_referral,
        "menu_members": _menu_members,
        "menu_volunteer": _menu_volunteer,
        "menu_booth_agent": _menu_booth_agent,
    }

    handler = handlers.get(action)
    if handler:
        handler(phone, sess)
    else:
        _send_main_menu(phone, "Please select an option from the menu:")


def _menu_view_card(phone: str, sess: dict):
    """View ID Card."""
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    stat = db['stats_col'].find_one({'auth_mobile': mobile}, {'card_url': 1, 'epic_no': 1})
    if stat and stat.get('card_url'):
        gen_doc = db['gen_voters_col'].find_one({'mobile': mobile}, {'name': 1})
        name = gen_doc.get('name', '') if gen_doc else ''
        epic = stat.get('epic_no', '')
        card_url = stat['card_url']
        api.send_image(
            phone,
            card_url,
            caption=f"🪪 *Your ID Card*\n\n*Name:* {name}\n*EPIC:* {epic}",
        )
        # Direct Cloudinary download link (no web session needed)
        if '/upload/' in card_url:
            download_link = card_url.replace('/upload/', f'/upload/fl_attachment:{epic}_VoterID/')
        else:
            download_link = card_url
        api.send_cta_url(
            phone,
            "📥 Tap below to download your ID card",
            "📥 Download Card",
            download_link,
        )
    else:
        api.send_text(phone, "❌ No ID card found.")

    _send_main_menu(phone, "What else would you like to do?")


def _menu_profile(phone: str, sess: dict):
    """View voter profile."""
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    gen_doc = db['gen_voters_col'].find_one({'mobile': mobile}, {'_id': 0, 'secret_pin': 0})
    if not gen_doc:
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    # Get original voter data
    voter_doc = None
    epic = gen_doc.get('epic_no', '')
    if epic:
        voter_doc = db['voters_col'].find_one({'epic_no': epic}, {'_id': 0})

    profile = {}
    if voter_doc:
        profile.update(voter_doc)
    profile.update({k: v for k, v in gen_doc.items() if v})
    profile.pop('secret_pin', None)

    # Format profile text
    lines = ["👤 *Your Profile*\n"]
    field_labels = {
        'name': 'Name', 'epic_no': 'EPIC No', 'assembly': 'Assembly',
        'district': 'District', 'age': 'Age', 'sex': 'Gender',
        'mobile': 'Mobile', 'ptc_code': 'PTC Code',
        'relation_type': 'Relation Type', 'relation_name': 'Relation Name',
        'polling_station': 'Polling Station', 'part_no': 'Part No',
    }
    for key, label in field_labels.items():
        val = profile.get(key, '')
        if val:
            lines.append(f"*{label}:* {val}")

    profile_text = "\n".join(lines)

    # Send photo with profile details as caption if photo exists
    photo_url = profile.get('photo_url', '')
    if not photo_url:
        stat = db['stats_col'].find_one({'epic_no': epic}, {'photo_url': 1})
        photo_url = stat.get('photo_url', '') if stat else ''

    if photo_url:
        api.send_image(phone, photo_url, caption=profile_text)
    else:
        api.send_text(phone, profile_text)

    _send_main_menu(phone, "What else would you like to do?")


def _menu_booth(phone: str, sess: dict):
    """View booth / polling station info."""
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    gen_doc = db['gen_voters_col'].find_one({'mobile': mobile}, {'_id': 0})
    if not gen_doc:
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    epic = gen_doc.get('epic_no', '')
    voter_doc = db['voters_col'].find_one({'epic_no': epic}, {'_id': 0}) if epic else None

    merged = {}
    if voter_doc:
        merged.update(voter_doc)
    merged.update({k: v for k, v in gen_doc.items() if v})

    lines = ["🗳️ *Booth / Polling Station Info*\n"]
    booth_fields = {
        'assembly': 'Assembly', 'district': 'District', 'part_no': 'Part No',
        'polling_station': 'Polling Station', 'booth_address': 'Booth Address',
    }
    for key, label in booth_fields.items():
        val = merged.get(key, '')
        if val:
            lines.append(f"*{label}:* {val}")

    booth_text = "\n".join(lines)

    # CTA button for Google Maps if lat/long available, else use address
    lat = merged.get('latitude', '')
    lon = merged.get('longitude', '')
    if lat and lon:
        maps_url = f"https://maps.google.com/?q={lat},{lon}"
        api.send_cta_url(phone, booth_text, "📍 Open in Maps", maps_url)
    elif merged.get('booth_address', '') or merged.get('polling_station', ''):
        import urllib.parse
        address = merged.get('booth_address', '') or merged.get('polling_station', '')
        maps_url = f"https://maps.google.com/maps?q={urllib.parse.quote(address)}"
        api.send_cta_url(phone, booth_text, "📍 Search in Maps", maps_url)
    else:
        api.send_text(phone, booth_text)

    _send_main_menu(phone, "What else would you like to do?")


def _menu_referral(phone: str, sess: dict):
    """Get referral link."""
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    voter = db['gen_voters_col'].find_one({'mobile': mobile})
    if not voter or not voter.get('ptc_code'):
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    result = db['get_or_create_referral'](voter['ptc_code'])
    if result:
        link = result['referral_link']
        # Send as a single shareable message — user can long-press to forward
        api.send_text(
            phone,
            f"🔗 *Your Referral Link*\n\n"
            f"Share this with friends and family to help them get their ID cards!\n\n"
            f"👇 *Tap and hold this message to forward:*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🪪 Generate your *Voter ID Card* for free!\n\n"
            f"Click here 👉 {link}\n"
            f"━━━━━━━━━━━━━━━━━━",
        )
    else:
        api.send_text(phone, "❌ Could not generate referral link.")

    _send_main_menu(phone, "What else would you like to do?")


def _menu_members(phone: str, sess: dict):
    """View referred members."""
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    voter = db['gen_voters_col'].find_one({'mobile': mobile})
    if not voter or not voter.get('ptc_code'):
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    members = list(db['gen_voters_col'].find(
        {'referred_by_ptc': voter['ptc_code']},
        {'_id': 0, 'name': 1, 'epic_no': 1, 'assembly': 1},
    ).sort('generated_at', -1).limit(20))

    if not members:
        api.send_text(phone, "👥 You haven't referred any members yet.\n\nShare your referral link to get started!")
    else:
        lines = [f"👥 *Your Referred Members ({len(members)}):*\n"]
        for i, m in enumerate(members, 1):
            lines.append(f"{i}. {m.get('name', '-')} ({m.get('epic_no', '-')}) - {m.get('assembly', '-')}")
        api.send_text(phone, "\n".join(lines))

    _send_main_menu(phone, "What else would you like to do?")


def _menu_volunteer(phone: str, sess: dict):
    """Submit volunteer request."""
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    voter = db['gen_voters_col'].find_one({'mobile': mobile})
    if not voter or not voter.get('ptc_code'):
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    existing = db['volunteer_requests_col'].find_one({'ptc_code': voter['ptc_code']})
    if existing:
        status = existing.get('status', 'pending')
        api.send_text(phone, f"🙋 You have already submitted a volunteer request.\n*Status:* {status.title()}")
        _send_main_menu(phone, "What else would you like to do?")
        return

    # Ask for confirmation
    sess['state'] = STATE_CONFIRM_VOLUNTEER
    api.send_buttons(
        phone,
        "🙋 *Volunteer Request*\n\n"
        f"*Name:* {voter.get('name', '')}\n"
        f"*EPIC:* {voter.get('epic_no', '')}\n"
        f"*Assembly:* {voter.get('assembly', '')}\n\n"
        "Would you like to submit a volunteer request?",
        [
            {"id": "confirm_volunteer", "title": "✅ Confirm"},
            {"id": "cancel_volunteer", "title": "❌ Cancel"},
        ],
        footer="Tap Confirm to submit or Cancel to go back",
    )


def _handle_confirm_volunteer(phone: str, button_id: str, text: str):
    """Handle volunteer confirmation."""
    sess = _get_session(phone)
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    if button_id == "cancel_volunteer" or text.lower() in ("cancel", "no"):
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "Request cancelled. What else would you like to do?")
        return

    if button_id != "confirm_volunteer" and text.lower() not in ("confirm", "yes"):
        api.send_text(phone, "Please tap *Confirm* or *Cancel*.")
        return

    voter = db['gen_voters_col'].find_one({'mobile': mobile})
    if not voter:
        api.send_text(phone, "❌ Profile not found.")
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "What else would you like to do?")
        return

    doc = {
        'ptc_code': voter['ptc_code'],
        'epic_no': voter.get('epic_no', ''),
        'name': voter.get('name', ''),
        'mobile': mobile,
        'assembly': voter.get('assembly', ''),
        'district': voter.get('district', ''),
        'photo_url': voter.get('photo_url', ''),
        'status': 'pending',
        'requested_at': datetime.now(timezone.utc).isoformat(),
        'reviewed_at': None,
        'reviewed_by': None,
        'source': 'whatsapp',
    }
    db['volunteer_requests_col'].insert_one(doc)
    api.send_text(phone, "✅ Volunteer request submitted successfully!\nYou will be notified when it's reviewed.")

    sess['state'] = STATE_MENU
    _send_main_menu(phone, "What else would you like to do?")


def _menu_booth_agent(phone: str, sess: dict):
    """Submit booth agent request."""
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    voter = db['gen_voters_col'].find_one({'mobile': mobile})
    if not voter or not voter.get('ptc_code'):
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    existing = db['booth_agent_requests_col'].find_one({'ptc_code': voter['ptc_code']})
    if existing:
        status = existing.get('status', 'pending')
        api.send_text(phone, f"🏢 You have already submitted a booth agent request.\n*Status:* {status.title()}")
        _send_main_menu(phone, "What else would you like to do?")
        return

    # Ask for confirmation
    sess['state'] = STATE_CONFIRM_BOOTH_AGENT
    api.send_buttons(
        phone,
        "🏢 *Booth Agent Request*\n\n"
        f"*Name:* {voter.get('name', '')}\n"
        f"*EPIC:* {voter.get('epic_no', '')}\n"
        f"*Assembly:* {voter.get('assembly', '')}\n\n"
        "Would you like to submit a booth agent request?",
        [
            {"id": "confirm_booth_agent", "title": "✅ Confirm"},
            {"id": "cancel_booth_agent", "title": "❌ Cancel"},
        ],
        footer="Tap Confirm to submit or Cancel to go back",
    )


def _handle_confirm_booth_agent(phone: str, button_id: str, text: str):
    """Handle booth agent confirmation."""
    sess = _get_session(phone)
    db = _get_db_collections()
    mobile = sess.get('mobile', phone[-10:])

    if button_id == "cancel_booth_agent" or text.lower() in ("cancel", "no"):
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "Request cancelled. What else would you like to do?")
        return

    if button_id != "confirm_booth_agent" and text.lower() not in ("confirm", "yes"):
        api.send_text(phone, "Please tap *Confirm* or *Cancel*.")
        return

    voter = db['gen_voters_col'].find_one({'mobile': mobile})
    if not voter:
        api.send_text(phone, "❌ Profile not found.")
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "What else would you like to do?")
        return

    doc = {
        'ptc_code': voter['ptc_code'],
        'epic_no': voter.get('epic_no', ''),
        'name': voter.get('name', ''),
        'mobile': mobile,
        'assembly': voter.get('assembly', ''),
        'district': voter.get('district', ''),
        'photo_url': voter.get('photo_url', ''),
        'status': 'pending',
        'requested_at': datetime.now(timezone.utc).isoformat(),
        'reviewed_at': None,
        'reviewed_by': None,
        'source': 'whatsapp',
    }
    db['booth_agent_requests_col'].insert_one(doc)
    api.send_text(phone, "✅ Booth agent request submitted successfully!\nYou will be notified when it's reviewed.")

    sess['state'] = STATE_MENU
    _send_main_menu(phone, "What else would you like to do?")
