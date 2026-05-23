import re

from app.skills.registry import register_tool
from app.services.messenger import send_whatsapp_message

def _to_jid(recipient: str) -> str:
    if "@" in recipient:
        return recipient
    digits = re.sub(r"\D", "", recipient)
    return f"{digits}@s.whatsapp.net"

@register_tool("whatsapp_send_message")
async def whatsapp_send_message(recipient: str, message: str) -> str:
    """Send a WhatsApp text message to a phone number or contact JID."""
    jid = _to_jid(recipient.strip())
    result = await send_whatsapp_message(jid, message)
    return f"Message sent to {jid}. Bridge response: {result.get('status', 'ok')}"