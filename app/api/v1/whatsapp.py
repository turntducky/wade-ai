import io
import os
import re
import time
import uuid
import httpx
import base64
import asyncio
import tempfile

import qrcode as _qrcode

from typing import List
from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks

from app.core.config import ConfigManager
from app.core.utils import strip_internal_tags
from app.core.orchestrator import orchestrator
from app.core.user_registry import user_registry
from app.services.voice import get_voice_service
from app.services.messenger import send_whatsapp_message, send_whatsapp_voice

router = APIRouter(prefix="/api/v1/whatsapp", tags=["whatsapp"])

_BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://localhost:3000")

def _raw_qr_to_data_url(raw: str) -> str:
    qr = _qrcode.QRCode(border=2)
    qr.add_data(raw)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#ffffff", back_color="#09090b")
    buf = io.BytesIO()
    img.save(buf, kind="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"

gpu_voice_lock = asyncio.Lock()

SESSION_TIMEOUT_SECS = 1800
_stranger_sessions: dict[str, tuple[str, float]] = {}
_stranger_sessions_lock = asyncio.Lock()

async def _get_or_create_conv_id(sender: str) -> str:
    """Get or create a conversation ID for a given sender. This allows W.A.D.E. to maintain separate conversation threads per WhatsApp contact, rather than mixing them all into one session. Admin users bypass this and share a single session since they are likely testing and don't need separate threads."""
    async with _stranger_sessions_lock:
        conv_id, last_seen = _stranger_sessions.get(sender, (None, 0.0))
        now = time.monotonic()
        if conv_id is None or (now - last_seen) > SESSION_TIMEOUT_SECS:
            conv_id = uuid.uuid4().hex[:8]
        _stranger_sessions[sender] = (conv_id, now)
        return conv_id

class WAPairCodeRequest(BaseModel):
    phone: str

@router.get("/status")
async def whatsapp_bridge_status():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{_BRIDGE_URL}/status", timeout=5.0)
            return r.json()
    except Exception:
        return {"connected": False, "botJid": None, "hasQr": False}

@router.get("/qr")
async def whatsapp_bridge_qr():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{_BRIDGE_URL}/qr", timeout=10.0)
            data = r.json()
        if data.get("connected"):
            return {"connected": True, "qr": None}
        raw = data.get("qr")
        if not raw:
            return {"connected": False, "qr": None}
        data_url = await asyncio.to_thread(_raw_qr_to_data_url, raw)
        return {"connected": False, "qr": data_url}
    except Exception:
        return {"connected": False, "qr": None}

@router.post("/pair-code")
async def whatsapp_pair_code(payload: WAPairCodeRequest):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_BRIDGE_URL}/pair-code", json={"phone": payload.phone}, timeout=20.0)
            return r.json()
    except Exception as e:
        return {"error": str(e)}

@router.post("/disconnect")
async def whatsapp_disconnect():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_BRIDGE_URL}/logout", timeout=10.0)
            return r.json()
    except Exception as e:
        return {"error": str(e)}

class WhatsAppMessage(BaseModel):
    sender: str
    message: str

class WhatsAppVoiceMessage(BaseModel):
    sender: str
    audio_b64: str

class GroupContextEntry(BaseModel):
    name: str
    text: str

class WhatsAppGroupMessage(BaseModel):
    group_jid: str
    sender_jid: str
    sender_name: str
    message: str
    conversation_context: List[GroupContextEntry] = []

class WhatsAppGroupVoiceMessage(BaseModel):
    group_jid: str
    sender_jid: str
    sender_name: str
    audio_b64: str
    conversation_context: List[GroupContextEntry] = []

async def process_and_reply(sender: str, message: str):
    """Background worker that handles the long-running LLM generation and sends the response back to WhatsApp."""
    try:
        tier_ctx = user_registry.resolve(sender)
        session_id = tier_ctx.session_id_for(sender)
        conv_id = await _get_or_create_conv_id(sender) if not tier_ctx.is_admin else None

        reply_parts = []
        async for chunk in orchestrator.process(
            message,
            session_id=session_id,
            tier_ctx=tier_ctx,
            conv_id=conv_id,
        ):
            reply_parts.append(chunk)

        reply = strip_internal_tags("".join(reply_parts))

        if reply:
            await send_whatsapp_message(
                recipient=sender,
                message=reply
            )
    except Exception as e:
        print(f"⚠️ Background processing error for WhatsApp message: {e}")

@router.post("/incoming")
async def receive_whatsapp_message(payload: WhatsAppMessage, background_tasks: BackgroundTasks):
    """Receives incoming WhatsApp messages from Baileys bridge."""
    background_tasks.add_task(process_and_reply, payload.sender, payload.message)

    return {"status": "processing_queued"}

async def process_voice_and_reply(sender: str, audio_b64: str):
    """Background worker for the Voice Note pipeline."""
    in_path = None
    out_path = None
    try:
        tier_ctx = user_registry.resolve(sender)
        session_id = tier_ctx.session_id_for(sender)
        conv_id = await _get_or_create_conv_id(sender) if not tier_ctx.is_admin else None

        in_fd, in_path = tempfile.mkstemp(suffix=".ogg")
        with os.fdopen(in_fd, 'wb') as f:
            f.write(base64.b64decode(audio_b64))

        async with gpu_voice_lock:
            voice = get_voice_service()

            user_text = await asyncio.to_thread(voice.transcribe_file, in_path)
            print(f"👤 YOU (Voice): {user_text}")

            voice_instruction = (
                f"You are {ConfigManager.get_assistant_name()}, an AI voice assistant responding to an audio message. "
                "CRITICAL RULES: "
                "1. NEVER output markdown, backticks, asterisks, or raw code. "
                "2. If the user asks you to write or run a script, execute it using your tools silently, "
                "then ONLY speak the final natural language result. "
                "3. Speak conversationally, naturally, and concisely."
            )
            full_voice_prompt = f"{voice_instruction}\n\nUser said: {user_text}"

            reply_parts = []
            async for chunk in orchestrator.process(
                full_voice_prompt,
                session_id=session_id,
                tier_ctx=tier_ctx,
                conv_id=conv_id,
            ):
                reply_parts.append(chunk)

            reply_text = strip_internal_tags("".join(reply_parts))
            print(f"🤖 W.A.D.E.: {reply_text}")
            
            out_fd, out_path = tempfile.mkstemp(suffix=".ogg")
            os.close(out_fd) 
            await asyncio.to_thread(voice.generate_audio_file, reply_text, out_path)
        
        with open(out_path, "rb") as f:
            out_b64 = base64.b64encode(f.read()).decode("utf-8")
        
        async with httpx.AsyncClient() as client:
            await client.post(
                "http://localhost:3000/send-voice", 
                json={"to": sender, "audio_b64": out_b64},
                timeout=30.0
            )

    except Exception as e:
        print(f"⚠️ Background processing error for Voice message: {e}")
    finally:
        if in_path and os.path.exists(in_path):
            os.remove(in_path)
        if out_path and os.path.exists(out_path):
            os.remove(out_path)

@router.post("/incoming-voice")
async def receive_whatsapp_voice(payload: WhatsAppVoiceMessage, background_tasks: BackgroundTasks):
    """Receives base64 audio payload from the Baileys bridge."""
    background_tasks.add_task(process_voice_and_reply, payload.sender, payload.audio_b64)
    return {"status": "processing_voice_queued"}

def _build_group_prompt(sender_name: str, message: str, context: List[GroupContextEntry]) -> str:
    """Builds a prompt for a group-chat message that mentions W.A.D.E., including recent conversation context if available. The prompt instructs W.A.D.E. to reply naturally as a participant in the group conversation, rather than as a help desk, and to match the tone of the conversation."""
    parts: list[str] = []

    if context:
        parts.append("<group_chat_context>")
        for entry in context:
            parts.append(f"[{entry.name}]: {entry.text}")
        parts.append("</group_chat_context>")
        parts.append("")

    parts.append(
        f"[You have been addressed in a WhatsApp group by {sender_name}]"
    )
    parts.append(f"{sender_name}: {message}")
    parts.append("")
    parts.append(
        "Reply naturally as a participant in this group conversation — not as a help desk. "
        "Keep it concise. Match the tone of the conversation. "
        f"Address {sender_name} by name if it feels natural."
    )
    return "\n".join(parts)

async def process_group_and_reply(
    group_jid: str,
    sender_jid: str,
    sender_name: str,
    message: str,
    context: List[GroupContextEntry],
):
    """Background worker: processes a group-chat mention and replies to the group."""
    try:
        tier_ctx = user_registry.resolve(sender_jid)
        group_digits = re.sub(r"\D", "", group_jid)
        session_id = f"wa_group_{group_digits}"

        sender_facts_dir = (
            tier_ctx.user_memory_dir(tier_ctx.session_id_for(sender_jid))
            if not tier_ctx.is_admin else None
        )

        prompt = _build_group_prompt(sender_name, message, context)

        reply_parts: list[str] = []
        async for chunk in orchestrator.process(
            prompt,
            session_id=session_id,
            tier_ctx=tier_ctx,
            sender_facts_dir=sender_facts_dir,
        ):
            reply_parts.append(chunk)

        reply = strip_internal_tags("".join(reply_parts))
        if reply:
            await send_whatsapp_message(recipient=group_jid, message=reply)
    except Exception as e:
        print(f"⚠️ Group processing error for {group_jid}: {e}")

@router.post("/incoming-group")
async def receive_group_message(payload: WhatsAppGroupMessage, background_tasks: BackgroundTasks):
    """Receives a group-chat mention from the Baileys bridge."""
    background_tasks.add_task(
        process_group_and_reply,
        payload.group_jid,
        payload.sender_jid,
        payload.sender_name,
        payload.message,
        payload.conversation_context,
    )
    return {"status": "processing_group_queued"}

async def process_group_voice_and_reply(
    group_jid: str,
    sender_jid: str,
    sender_name: str,
    audio_b64: str,
    context: List[GroupContextEntry],
):
    """Background worker: transcribes a group voice note and replies with a voice note if WADE is mentioned."""
    in_path = None
    out_path = None
    try:
        in_fd, in_path = tempfile.mkstemp(suffix=".ogg")
        with os.fdopen(in_fd, "wb") as f:
            f.write(base64.b64decode(audio_b64))

        async with gpu_voice_lock:
            voice = get_voice_service()
            user_text = await asyncio.to_thread(voice.transcribe_file, in_path)

            if not user_text or not re.search(r"\bwade\b", user_text, re.IGNORECASE):
                print(f"👥🎤 {sender_name} (voice, no mention): {user_text or '[empty]'}")
                return

            print(f"👥🎤 {sender_name} (voice): {user_text}")

            tier_ctx = user_registry.resolve(sender_jid)
            group_digits = re.sub(r"\D", "", group_jid)
            session_id = f"wa_group_{group_digits}"

            sender_facts_dir = (
                tier_ctx.user_memory_dir(tier_ctx.session_id_for(sender_jid))
                if not tier_ctx.is_admin else None
            )

            prompt = _build_group_prompt(sender_name, user_text, context)

            reply_parts: list[str] = []
            async for chunk in orchestrator.process(prompt, session_id=session_id, tier_ctx=tier_ctx, sender_facts_dir=sender_facts_dir):
                reply_parts.append(chunk)

            reply = strip_internal_tags("".join(reply_parts))
            if not reply:
                return

            out_fd, out_path = tempfile.mkstemp(suffix=".ogg")
            os.close(out_fd)
            await asyncio.to_thread(voice.generate_audio_file, reply, out_path)

        with open(out_path, "rb") as f:
            out_b64 = base64.b64encode(f.read()).decode("utf-8")

        await send_whatsapp_voice(recipient=group_jid, audio_b64=out_b64)

    except Exception as e:
        print(f"⚠️ Group voice processing error for {group_jid}: {e}")
    finally:
        for p in (in_path, out_path):
            if p and os.path.exists(p):
                os.remove(p)

@router.post("/incoming-group-voice")
async def receive_group_voice(payload: WhatsAppGroupVoiceMessage, background_tasks: BackgroundTasks):
    """Receives a group voice note from the Baileys bridge."""
    background_tasks.add_task(
        process_group_voice_and_reply,
        payload.group_jid,
        payload.sender_jid,
        payload.sender_name,
        payload.audio_b64,
        payload.conversation_context,
    )
    return {"status": "processing_group_voice_queued"}