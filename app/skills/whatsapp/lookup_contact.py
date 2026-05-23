from app.skills.registry import register_tool
from app.services.messenger import get_whatsapp_contacts

@register_tool("whatsapp_lookup_contact")
async def whatsapp_lookup_contact(name: str) -> str:
    """Search the WhatsApp contact list by name and return matching entries."""
    contacts = await get_whatsapp_contacts()
    if not contacts:
        return "No contacts found in the contact list. Make sure the bridge is running and has synced contacts."

    query = name.strip().lower()
    matches = [
        c for c in contacts
        if query in c.get("name", "").lower() or query in c.get("notify", "").lower()
    ]

    if not matches:
        return f"No contacts found matching '{name}'."

    lines = [f"Found {len(matches)} contact(s) matching '{name}':"]
    for c in matches:
        lines.append(f"  - {c['name']}  |  {c['phone']}  |  JID: {c['jid']}")
    return "\n".join(lines)
