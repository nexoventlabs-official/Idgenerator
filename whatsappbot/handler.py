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


def _get_db_helpers():
    """Lazy import to avoid circular imports – returns DB helper functions from app.py."""
    from app import (
        _get_mysql, find_voter_by_epic,
        generate_ptc_code, get_or_create_referral,
    )
    return {
        '_get_mysql': _get_mysql,
        'find_voter_by_epic': find_voter_by_epic,
        'generate_ptc_code': generate_ptc_code,
        'get_or_create_referral': get_or_create_referral,
    }


def _get_mysql():
    """Shortcut to get MySQL connection."""
    from app import _get_mysql
    return _get_mysql()


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
    # Extract 10-digit mobile (Indian format: 91XXXXXXXXXX)
    mobile_10 = phone[-10:] if len(phone) >= 10 else phone

    # Check if this number already has a card
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no, card_url, secret_pin FROM generation_stats WHERE auth_mobile = %s", (mobile_10,))
            stat = cur.fetchone()
    finally:
        conn.close()

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

    # Mark mobile as verified
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO verified_mobiles (mobile, verified_at) "
                "VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE verified_at=VALUES(verified_at)",
                (mobile, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            )
    finally:
        conn.close()

    # Check if already has a card (edge case: registered on website)
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no, card_url FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
    finally:
        conn.close()
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

    sess = _get_session(phone)

    otp = str(secrets.randbelow(900000) + 100000)

    # Send via 2Factor.in FIRST, only store in DB if sent successfully
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
        # Store OTP in DB only after SMS was sent successfully
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO otp_sessions (mobile, otp, created_at, verified) "
                    "VALUES (%s, %s, %s, 0) "
                    "ON DUPLICATE KEY UPDATE otp=VALUES(otp), created_at=VALUES(created_at), verified=0",
                    (mobile, otp, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
                )
        finally:
            conn.close()

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

    otp = text.strip()
    if not re.match(r'^\d{6}$', otp):
        api.send_text(phone, "Please enter a valid *6-digit OTP*.\n\n_Type only numbers (e.g. 123456)_")
        return

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT otp, created_at FROM otp_sessions WHERE mobile = %s", (mobile,))
            doc = cur.fetchone()
    finally:
        conn.close()
    if not doc or doc.get('otp') != otp:
        api.send_text(phone, "❌ Invalid OTP. Please try again.\n\n_Type the 6-digit number from your SMS_")
        return

    # Check expiry
    try:
        created = doc['created_at']
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created).total_seconds() > 300:
            api.send_text(phone, "⏰ OTP expired. Send *Hi* to start again.")
            _clear_session(phone)
            return
    except Exception:
        pass

    # Mark verified
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE otp_sessions SET verified = 1 WHERE mobile = %s", (mobile,))
    finally:
        conn.close()

    api.send_text(phone, "✅ OTP verified successfully!")

    # Check if already has a card (edge case: registered on website)
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT epic_no, card_url FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
    finally:
        conn.close()
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
    from app import find_voter_by_epic

    epic_no = text.strip().upper()
    if len(epic_no) < 3 or len(epic_no) > 20:
        api.send_text(phone, "❌ Invalid EPIC format.\n\nFormat: *3 letters + 7 digits*\nExample: *ABC1234567*\n\n_First type 3 letters, then 7 numbers_")
        return

    voter = find_voter_by_epic(epic_no)
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
        result = _generate_and_upload_card(voter, epic_no, mobile, photo_image)
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
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE generation_stats SET secret_pin = %s WHERE auth_mobile = %s", (hashed_pin, mobile))
                cur.execute("UPDATE generated_voters SET secret_pin = %s WHERE MOBILE_NO = %s", (hashed_pin, mobile))
        finally:
            conn.close()
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
                               photo_image: Image.Image) -> dict:
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
    from app import generate_ptc_code
    ptc_code = generate_ptc_code()
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
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
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
                "ptc_code, photo_url, card_url, generated_at, created_at"
                ") VALUES ("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s,%s"
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
                "generated_at=VALUES(generated_at)",
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
                    ptc_code, photo_url, card_url, now, now
                )
            )

            # Update stats
            cur.execute(
                "INSERT INTO generation_stats (epic_no, card_url, photo_url, last_generated, auth_mobile, count) "
                "VALUES (%s, %s, %s, %s, %s, 1) "
                "ON DUPLICATE KEY UPDATE card_url=VALUES(card_url), photo_url=VALUES(photo_url), "
                "last_generated=VALUES(last_generated), auth_mobile=VALUES(auth_mobile), count=count+1",
                (epic_no, card_url, photo_url, now, mobile)
            )

            # Mark mobile as verified
            cur.execute(
                "INSERT INTO verified_mobiles (mobile, epic_no, verified_at) "
                "VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE epic_no=VALUES(epic_no), verified_at=VALUES(verified_at)",
                (mobile, epic_no, now)
            )
    finally:
        conn.close()

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

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE generation_stats SET secret_pin = %s WHERE auth_mobile = %s", (hashed, mobile))
            cur.execute("UPDATE generated_voters SET secret_pin = %s WHERE MOBILE_NO = %s", (hashed, mobile))
    finally:
        conn.close()

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

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT secret_pin, epic_no, card_url FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
    finally:
        conn.close()
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
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
                gen_doc = cur.fetchone()
        finally:
            conn.close()
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

    otp = text.strip()
    if not re.match(r'^\d{6}$', otp):
        api.send_text(phone, "Please enter a valid *6-digit OTP*.\n\n_Type only numbers (e.g. 123456)_")
        return

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT otp, created_at FROM otp_sessions WHERE mobile = %s", (mobile,))
            doc = cur.fetchone()
    finally:
        conn.close()
    if not doc or doc.get('otp') != otp:
        api.send_text(phone, "❌ Invalid OTP. Please try again.\n\n_Type the 6-digit number from your SMS_")
        return

    try:
        created = doc['created_at']
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
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

    pin = text.strip()
    if pin != sess.get('reset_pin', ''):
        api.send_text(phone, "❌ PINs don't match.\n\nEnter a new *4-digit numeric PIN*:\n_Type only numbers (e.g. 1234)_")
        sess['state'] = STATE_AWAITING_RESET_PIN
        sess.pop('reset_pin', None)
        return

    mobile = sess.get('mobile', phone[-10:])

    from security_fixes import hash_pin
    hashed = hash_pin(pin)

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE generation_stats SET secret_pin = %s WHERE auth_mobile = %s", (hashed, mobile))
            cur.execute("UPDATE generated_voters SET secret_pin = %s WHERE MOBILE_NO = %s", (hashed, mobile))
            cur.execute("DELETE FROM otp_sessions WHERE mobile = %s", (mobile,))
    finally:
        conn.close()

    sess.pop('reset_pin', None)
    logger.info(f"PIN reset via WhatsApp for {mobile[:2]}****{mobile[-2:]}")

    api.send_text(phone, "✅ PIN reset successfully!")

    sess['authenticated'] = True
    # Show card and menu
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT card_url, epic_no FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
    finally:
        conn.close()
    if stat and stat.get('card_url'):
        sess['epic_no'] = stat.get('epic_no', '')
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
                gen_doc = cur.fetchone()
        finally:
            conn.close()
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
    mobile = sess.get('mobile', phone[-10:])

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT card_url, epic_no FROM generation_stats WHERE auth_mobile = %s", (mobile,))
            stat = cur.fetchone()
    finally:
        conn.close()
    if stat and stat.get('card_url'):
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
                gen_doc = cur.fetchone()
        finally:
            conn.close()
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
    mobile = sess.get('mobile', phone[-10:])

    from app import _translate_gen_row
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            gen_doc = _translate_gen_row(cur.fetchone())
    finally:
        conn.close()
    if not gen_doc:
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    # Remove sensitive fields
    gen_doc.pop('secret_pin', None)

    # Get original voter data from MySQL
    from app import find_voter_by_epic
    voter_doc = None
    epic = gen_doc.get('epic_no', '')
    if epic:
        voter_doc = find_voter_by_epic(epic)

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
        conn = _get_mysql()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT photo_url FROM generation_stats WHERE epic_no = %s", (epic,))
                stat_row = cur.fetchone()
        finally:
            conn.close()
        photo_url = stat_row.get('photo_url', '') if stat_row else ''

    if photo_url:
        api.send_image(phone, photo_url, caption=profile_text)
    else:
        api.send_text(phone, profile_text)

    _send_main_menu(phone, "What else would you like to do?")


def _menu_booth(phone: str, sess: dict):
    """View booth / polling station info."""
    mobile = sess.get('mobile', phone[-10:])

    from app import _translate_gen_row
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            gen_doc = _translate_gen_row(cur.fetchone())
    finally:
        conn.close()
    if not gen_doc:
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    epic = gen_doc.get('epic_no', '')
    from app import find_voter_by_epic
    voter_doc = find_voter_by_epic(epic) if epic else None

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
    mobile = sess.get('mobile', phone[-10:])

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ptc_code FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter or not voter.get('ptc_code'):
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    from app import get_or_create_referral
    result = get_or_create_referral(voter['ptc_code'])
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
    mobile = sess.get('mobile', phone[-10:])

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ptc_code FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter or not voter.get('ptc_code'):
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, "
                "EPIC_NO AS epic_no, CAST(AC_NO AS CHAR) AS assembly FROM generated_voters "
                "WHERE referred_by_ptc = %s ORDER BY generated_at DESC LIMIT 20",
                (voter['ptc_code'],)
            )
            members = cur.fetchall()
    finally:
        conn.close()

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
    mobile = sess.get('mobile', phone[-10:])

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ptc_code, EPIC_NO AS epic_no, CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, COALESCE(ASSEMBLY_NAME, CAST(AC_NO AS CHAR)) AS assembly, DISTRICT_NAME AS district FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter or not voter.get('ptc_code'):
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM volunteer_requests WHERE ptc_code = %s", (voter['ptc_code'],))
            existing = cur.fetchone()
    finally:
        conn.close()
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
    mobile = sess.get('mobile', phone[-10:])

    if button_id == "cancel_volunteer" or text.lower() in ("cancel", "no"):
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "Request cancelled. What else would you like to do?")
        return

    if button_id != "confirm_volunteer" and text.lower() not in ("confirm", "yes"):
        api.send_text(phone, "Please tap *Confirm* or *Cancel*.")
        return

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ptc_code, EPIC_NO AS epic_no, CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, COALESCE(ASSEMBLY_NAME, CAST(AC_NO AS CHAR)) AS assembly, DISTRICT_NAME AS district, photo_url FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter:
        api.send_text(phone, "❌ Profile not found.")
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "What else would you like to do?")
        return

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO volunteer_requests (ptc_code, epic_no, name, mobile, assembly, photo_url, status, requested_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (voter['ptc_code'], voter.get('epic_no', ''), voter.get('name', ''),
                 mobile, voter.get('assembly', ''), voter.get('photo_url', ''),
                 'pending', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            )
    finally:
        conn.close()
    api.send_text(phone, "✅ Volunteer request submitted successfully!\nYou will be notified when it's reviewed.")

    sess['state'] = STATE_MENU
    _send_main_menu(phone, "What else would you like to do?")


def _menu_booth_agent(phone: str, sess: dict):
    """Submit booth agent request."""
    mobile = sess.get('mobile', phone[-10:])

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ptc_code, EPIC_NO AS epic_no, CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, COALESCE(ASSEMBLY_NAME, CAST(AC_NO AS CHAR)) AS assembly, DISTRICT_NAME AS district FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter or not voter.get('ptc_code'):
        api.send_text(phone, "❌ Profile not found.")
        _send_main_menu(phone, "What else would you like to do?")
        return

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM booth_agent_requests WHERE ptc_code = %s", (voter['ptc_code'],))
            existing = cur.fetchone()
    finally:
        conn.close()
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
    mobile = sess.get('mobile', phone[-10:])

    if button_id == "cancel_booth_agent" or text.lower() in ("cancel", "no"):
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "Request cancelled. What else would you like to do?")
        return

    if button_id != "confirm_booth_agent" and text.lower() not in ("confirm", "yes"):
        api.send_text(phone, "Please tap *Confirm* or *Cancel*.")
        return

    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ptc_code, EPIC_NO AS epic_no, CONCAT(COALESCE(FM_NAME_EN,''),' ',COALESCE(LASTNAME_EN,'')) AS name, MOBILE_NO AS mobile, COALESCE(ASSEMBLY_NAME, CAST(AC_NO AS CHAR)) AS assembly, DISTRICT_NAME AS district, photo_url FROM generated_voters WHERE MOBILE_NO = %s LIMIT 1", (mobile,))
            voter = cur.fetchone()
    finally:
        conn.close()
    if not voter:
        api.send_text(phone, "❌ Profile not found.")
        sess['state'] = STATE_MENU
        _send_main_menu(phone, "What else would you like to do?")
        return

    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    conn = _get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO booth_agent_requests
                   (ptc_code, epic_no, name, mobile, assembly, photo_url, status, requested_at, source)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, 'whatsapp')""",
                (voter['ptc_code'], voter.get('epic_no', ''), voter.get('name', ''),
                 mobile, voter.get('assembly', ''), voter.get('photo_url', ''), now_iso)
            )
            conn.commit()
    finally:
        conn.close()
    api.send_text(phone, "✅ Booth agent request submitted successfully!\nYou will be notified when it's reviewed.")

    sess['state'] = STATE_MENU
    _send_main_menu(phone, "What else would you like to do?")
