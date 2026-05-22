import os
import httpx

BAILEYS_API_URL = os.getenv("BAILEYS_API_URL", "http://localhost:3000")

async def send_whatsapp_message(recipient: str, message: str) -> dict:
    """Sends a text message through the Baileys bridge."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{BAILEYS_API_URL}/send-message",
            json={"to": recipient, "message": message},
        )
    response.raise_for_status()
    return response.json()

async def create_whatsapp_group(name: str, participants: list[str]) -> dict:
    """Creates a WhatsApp group with the given participant JIDs."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{BAILEYS_API_URL}/create-group",
            json={"name": name, "participants": participants},
        )
    response.raise_for_status()
    return response.json()

async def send_whatsapp_voice(recipient: str, audio_b64: str) -> dict:
    """Sends an OGG Opus voice note through the Baileys bridge."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{BAILEYS_API_URL}/send-voice",
            json={"to": recipient, "audio_b64": audio_b64},
        )
    response.raise_for_status()
    return response.json()

async def get_whatsapp_contacts() -> list[dict]:
    """Returns the contact list from the Baileys bridge (name + JID + phone)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{BAILEYS_API_URL}/contacts")
    response.raise_for_status()
    return response.json()