import re

from app.skills.registry import register_tool
from app.services.messenger import get_whatsapp_contacts, create_whatsapp_group

_ME_ALIASES = {"me", "myself", "i", "my number"}

def _digits_to_jid(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return f"{digits}@s.whatsapp.net"

async def _resolve_participants(participants: list[str]) -> tuple[list[str], list[str]]:
    """Resolves a list of participant identifiers (names, phone numbers, or "me") to WhatsApp JIDs."""
    from app.core.user_registry import user_registry

    contacts = await get_whatsapp_contacts()
    contacts_lower = {c.get("name", "").lower(): c["jid"] for c in contacts}
    contacts_lower.update({c.get("notify", "").lower(): c["jid"] for c in contacts if c.get("notify")})

    admin_jids = user_registry.get_admin_jids()
    admin_jid = admin_jids[0] if admin_jids else None

    jids: list[str] = []
    unresolved: list[str] = []

    for p in participants:
        p_stripped = p.strip()
        p_lower = p_stripped.lower()

        if p_lower in _ME_ALIASES:
            if admin_jid:
                jids.append(admin_jid)
            else:
                unresolved.append(p_stripped)
            continue

        if "@" in p_stripped:
            jids.append(p_stripped)
            continue

        if re.match(r"^\+?[\d\s\-().]{7,}$", p_stripped):
            jids.append(_digits_to_jid(p_stripped))
            continue

        if p_lower in contacts_lower:
            jids.append(contacts_lower[p_lower])
            continue

        partial = [jid for name, jid in contacts_lower.items() if p_lower in name and name]
        if len(partial) == 1:
            jids.append(partial[0])
        elif len(partial) > 1:
            unresolved.append(f"{p_stripped} (ambiguous: {len(partial)} matches)")
        else:
            unresolved.append(p_stripped)

    return jids, unresolved

@register_tool("whatsapp_create_group")
async def whatsapp_create_group(group_name: str, participants: list) -> str:
    """Create a WhatsApp group chat with the specified participants."""
    if not group_name:
        return "Error: group_name is required."
    if not participants:
        return "Error: participants list is required."

    jids, unresolved = await _resolve_participants([str(p) for p in participants])

    if unresolved:
        return (
            f"Could not resolve the following participants: {', '.join(unresolved)}. "
            "Please provide their phone numbers directly or check the contact name spelling."
        )

    if not jids:
        return "Error: no valid participants could be resolved."

    result = await create_whatsapp_group(group_name, jids)
    group_id = result.get("group_id", "unknown")
    return (
        f"Group '{group_name}' created successfully.\n"
        f"Group ID: {group_id}\n"
        f"Participants added: {', '.join(jids)}"
    )