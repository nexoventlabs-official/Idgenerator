"""
WhatsApp Cloud API Helper
==========================
Send text, button, list, image, and interactive messages
via Meta WhatsApp Business Cloud API.
"""
import requests
import logging
from . import wa_config

logger = logging.getLogger('whatsapp_bot')

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {wa_config.WHATSAPP_ACCESS_TOKEN}",
}


def _post(payload: dict) -> dict:
    """Send a message via WhatsApp Cloud API."""
    try:
        resp = requests.post(
            wa_config.WHATSAPP_API_URL,
            json=payload,
            headers=HEADERS,
            timeout=30,
        )
        data = resp.json()
        if resp.status_code not in (200, 201):
            logger.error(f"WhatsApp API error: {resp.status_code} - {data}")
        return data
    except Exception as e:
        logger.error(f"WhatsApp API request failed: {e}")
        return {}


def send_text(to: str, text: str, preview_url: bool = False) -> dict:
    """Send a plain text message."""
    return _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text, "preview_url": preview_url},
    })


def send_image(to: str, image_url: str, caption: str = "") -> dict:
    """Send an image by URL."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"link": image_url},
    }
    if caption:
        payload["image"]["caption"] = caption
    return _post(payload)


def send_buttons(to: str, body: str, buttons: list[dict], header: str = "", footer: str = "") -> dict:
    """Send interactive reply buttons (max 3 buttons).

    buttons format: [{"id": "btn_id", "title": "Button Label"}, ...]
    """
    action_buttons = []
    for btn in buttons[:3]:
        action_buttons.append({
            "type": "reply",
            "reply": {"id": btn["id"], "title": btn["title"][:20]},
        })

    interactive = {
        "type": "button",
        "body": {"text": body},
        "action": {"buttons": action_buttons},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    return _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    })


def send_list(to: str, body: str, button_text: str, sections: list[dict],
              header: str = "", footer: str = "") -> dict:
    """Send an interactive list message.

    sections format:
    [
        {
            "title": "Section Title",
            "rows": [
                {"id": "row_id", "title": "Row Title", "description": "Row Desc"},
            ]
        }
    ]
    """
    interactive = {
        "type": "list",
        "body": {"text": body},
        "action": {
            "button": button_text[:20],
            "sections": sections,
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    return _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    })


def mark_read(message_id: str) -> dict:
    """Mark a message as read."""
    return _post({
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    })


def download_media(media_id: str) -> bytes | None:
    """Download media from WhatsApp (user-uploaded photos).
    Step 1: GET media URL from media_id
    Step 2: Download the actual binary
    """
    try:
        # Get media URL
        url_resp = requests.get(
            f"{wa_config.WHATSAPP_MEDIA_URL}/{media_id}",
            headers={"Authorization": f"Bearer {wa_config.WHATSAPP_ACCESS_TOKEN}"},
            timeout=15,
        )
        if url_resp.status_code != 200:
            logger.error(f"Media URL fetch failed: {url_resp.status_code}")
            return None
        media_url = url_resp.json().get("url")
        if not media_url:
            return None

        # Download binary
        dl_resp = requests.get(
            media_url,
            headers={"Authorization": f"Bearer {wa_config.WHATSAPP_ACCESS_TOKEN}"},
            timeout=60,
        )
        if dl_resp.status_code == 200:
            return dl_resp.content
        logger.error(f"Media download failed: {dl_resp.status_code}")
        return None
    except Exception as e:
        logger.error(f"Media download error: {e}")
        return None
