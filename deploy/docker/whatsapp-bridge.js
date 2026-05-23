const app = express();
const fs = require('fs');
const pino = require('pino');
const axios = require('axios');
const express = require('express');
const qrcode = require('qrcode-terminal');
const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestWaWebVersion,
    downloadMediaMessage,
    jidNormalizedUser
} = require('@whiskeysockets/baileys');

app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ limit: '50mb', extended: true }));

const PYTHON_BASE_URL  = process.env.PYTHON_BASE_URL || 'http://localhost:8000';
const PYTHON_API_URL   = `${PYTHON_BASE_URL}/api/v1/whatsapp/incoming`;
const PYTHON_VOICE_URL = `${PYTHON_BASE_URL}/api/v1/whatsapp/incoming-voice`;
const PYTHON_GROUP_URL       = `${PYTHON_BASE_URL}/api/v1/whatsapp/incoming-group`;
const PYTHON_GROUP_VOICE_URL = `${PYTHON_BASE_URL}/api/v1/whatsapp/incoming-group-voice`;
const PORT = process.env.PORT || 3000;

let sock;
let botJid         = null;
let _isConnected   = false;
let _currentQrData = null;

let _reconnectDelay = 3000;
const _reconnectDelayMax = 60000;
let _lastQrPrint = 0;
const _qrPrintInterval = 30000;

const LID_MAP_PATH      = './baileys_lid_map.json';
const CONTACTS_MAP_PATH = './baileys_contacts.json';
const lidToJid     = new Map();
const contactsMap  = new Map();

(function loadPersistedMaps() {
    try {
        const entries = JSON.parse(fs.readFileSync(LID_MAP_PATH, 'utf8'));
        for (const [k, v] of entries) lidToJid.set(k, v);
        console.log(`📋 Loaded ${lidToJid.size} LID→JID mapping(s) from disk`);
    } catch (_) { /* first run */ }
    try {
        const entries = JSON.parse(fs.readFileSync(CONTACTS_MAP_PATH, 'utf8'));
        for (const [k, v] of entries) contactsMap.set(k, v);
        console.log(`📋 Loaded ${contactsMap.size} contact name(s) from disk`);
    } catch (_) { /* first run */ }
})();

function saveLidMap() {
    try {
        fs.writeFileSync(LID_MAP_PATH, JSON.stringify([...lidToJid.entries()]), 'utf8');
    } catch (e) {
        console.error('⚠️ Failed to save LID map:', e.message);
    }
}

function saveContactsMap() {
    try {
        fs.writeFileSync(CONTACTS_MAP_PATH, JSON.stringify([...contactsMap.entries()]), 'utf8');
    } catch (e) {
        console.error('⚠️ Failed to save contacts map:', e.message);
    }
}

function trackContacts(contacts) {
    let changed = false;
    for (const c of contacts) {
        if (!c.id) continue;
        const jid = jidNormalizedUser(c.id);
        if (!jid) continue;

        if (c.lid && jid.endsWith('@s.whatsapp.net')) {
            const lid = jidNormalizedUser(c.lid);
            if (lid && lidToJid.get(lid) !== jid) {
                lidToJid.set(lid, jid);
                changed = true;
                console.log(`🔗 Mapped LID ${lid} to JID ${jid}`);
            }
        }

        const name = c.name || c.verifiedName || c.notify;
        if (name) {
            const existing = contactsMap.get(jid) || {};
            if (existing.name !== name || existing.notify !== c.notify) {
                contactsMap.set(jid, { 
                    name: name || existing.name || '', 
                    notify: c.notify || existing.notify || '',
                    lid: c.lid || existing.lid || (jid.endsWith('@lid.us') ? jid : null)
                });
                changed = true;
            }
        }
    }
    if (changed) {
        saveLidMap();
        saveContactsMap();
    }
}

const GROUP_BUFFER_SIZE = 15;
const groupBuffers = new Map();

function bufferGroupMessage(groupJid, senderName, text) {
    if (!groupBuffers.has(groupJid)) groupBuffers.set(groupJid, []);
    const buf = groupBuffers.get(groupJid);
    buf.push({ name: senderName, text, ts: Date.now() });
    while (buf.length > GROUP_BUFFER_SIZE) buf.shift();
}

function getGroupContext(groupJid) {
    return groupBuffers.get(groupJid) || [];
}

function shouldWadeRespond(text, mentionedJids) {
    if (!text) return false;
    if (/\bwade\b/i.test(text)) return true;
    if (mentionedJids?.length && botJid) {
        const botNum = botJid.split('@')[0];
        if (mentionedJids.some(jid => String(jid).startsWith(botNum))) return true;
    }
    return false;
}

function resolveJid(rawJid) {
    if (!rawJid) return null;
    const normalized = jidNormalizedUser(rawJid);
    if (!normalized) return null;
    if (normalized.endsWith('@lid.us')) {
        const resolved = lidToJid.get(normalized);
        if (resolved) {
            return resolved;
        }
        console.log(`⚠️ LID not yet mapped: ${normalized} — forwarding to Python for registry lookup`);
        return normalized;
    }
    return normalized;
}

async function waitForAPI(maxWaitMs = 120000) {
    const start = Date.now();
    while (Date.now() - start < maxWaitMs) {
        try {
            await axios.get(`${PYTHON_BASE_URL}/health`, { timeout: 3000 });
            console.log('✅ WADE API is ready.');
            return;
        } catch (_) {
            console.log('⏳ Waiting for WADE API to become ready...');
            await new Promise(resolve => setTimeout(resolve, 4000));
        }
    }
    throw new Error('❌ WADE API did not become ready within 120s. Aborting.');
}

async function postWithRetry(url, data, maxRetries = 4, delayMs = 2500) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            return await axios.post(url, data);
        } catch (error) {
            if (error.code === 'ECONNREFUSED' && attempt < maxRetries) {
                console.log(`⚠️ WADE API not ready (attempt ${attempt}/${maxRetries}), retrying in ${delayMs}ms...`);
                await new Promise(resolve => setTimeout(resolve, delayMs));
            } else {
                throw error;
            }
        }
    }
}

function extractMessageContent(msgObj) {
    if (!msgObj) return null;
    if (msgObj.ephemeralMessage) return msgObj.ephemeralMessage.message;
    if (msgObj.viewOnceMessageV2) return msgObj.viewOnceMessageV2.message;
    if (msgObj.viewOnceMessage) return msgObj.viewOnceMessage.message;
    return msgObj;
}

async function connectToWhatsApp() {
    const { state, saveCreds } = await useMultiFileAuthState('baileys_auth_info');
    
    const { version, isLatest } = await fetchLatestWaWebVersion();
    console.log(`\n🌐 Using WhatsApp Web v${version.join('.')} (isLatest: ${isLatest})`);

    sock = makeWASocket({
        version,
        auth: state,
        browser: ['Chrome', 'Windows', '110.0.5481.177'],
        logger: pino({ level: 'silent' }) 
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('contacts.upsert', trackContacts);
    sock.ev.on('contacts.update', trackContacts);
    sock.ev.on('messaging-history.set', ({ contacts }) => {
        if (contacts) {
            console.log(`📋 Received ${contacts.length} contacts from history sync`);
            trackContacts(contacts);
        }
    });

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        if (qr) {
            _currentQrData = qr;
            const now = Date.now();
            if (now - _lastQrPrint >= _qrPrintInterval) {
                _lastQrPrint = now;
                console.log('\n📱 Scan this QR code with your WhatsApp app (run "wade pair" for interactive mode):');
                qrcode.generate(qr, { small: true });
            } else {
                console.log('📱 QR code updated (suppressed — run "wade pair" to scan)');
            }
        }

        if (connection === 'close') {
            _isConnected = false;
            const shouldReconnect = lastDisconnect.error?.output?.statusCode !== DisconnectReason.loggedOut;
            const reason = lastDisconnect.error?.message || lastDisconnect.error?.name || 'Unknown';

            console.log(`⚠️ Connection closed (${reason}). Reconnecting: ${shouldReconnect} (delay: ${_reconnectDelay / 1000}s)`);

            if (shouldReconnect) {
                setTimeout(() => {
                    _reconnectDelay = Math.min(_reconnectDelay * 2, _reconnectDelayMax);
                    connectToWhatsApp();
                }, _reconnectDelay);
            }
        } else if (connection === 'open') {
            _isConnected = true;
            _currentQrData = null;
            _reconnectDelay = 3000;
            botJid = sock.user?.id ? jidNormalizedUser(sock.user.id) : null;
            console.log(`\n✅ Connected to WhatsApp Web via Baileys Multi-Device! (bot JID: ${botJid || 'unknown'})`);
        }
    });

    sock.ev.on('call', async (calls) => {
        for (const call of calls) {
            if (call.status === 'offer') {
                console.log(`📞 Rejecting incoming call from ${call.from}`);
                
                await sock.rejectCall(call.id, call.from);
                
                await sock.sendMessage(call.from, { 
                    text: "Hey! I can't take live calls right now. Drop me a voice note or a text and I'll get back to you!" 
                });
            }
        }
    });

    sock.ev.on('messages.upsert', async (m) => {
        if (!m?.messages?.length) return;
        const msg = m.messages[0];
        if (!msg.message || msg.key.fromMe) return;

        let rawRemoteJid = msg.key.remoteJid;
        if (!rawRemoteJid) return;

        if (/@lid/.test(rawRemoteJid)) {
            let phoneJid = null;
            if (msg.key.senderPn) {
                phoneJid = jidNormalizedUser(msg.key.senderPn) || msg.key.senderPn;
            } else if (msg.key.remoteJidAlt?.endsWith('@s.whatsapp.net')) {
                phoneJid = msg.key.remoteJidAlt;
            }
            if (phoneJid) {
                console.log(`🔗 LID resolved: ${rawRemoteJid} → ${phoneJid}`);
                if (!lidToJid.has(rawRemoteJid)) {
                    lidToJid.set(rawRemoteJid, phoneJid);
                    saveLidMap();
                }
                rawRemoteJid = phoneJid;
            } else {
                console.log(`⚠️  LID ${rawRemoteJid} — no senderPn/remoteJidAlt, trying contacts map`);
            }
        }

        const actualMessage = extractMessageContent(msg.message);
        if (!actualMessage) return;

        try { await sock.readMessages([msg.key]); } catch (err) {
            console.error('Failed to send read receipt:', err.message);
        }

        if (rawRemoteJid.endsWith('@g.us')) {
            const groupJid  = rawRemoteJid;
            const senderRaw = msg.key.participant || '';
            const senderJid = resolveJid(senderRaw);
            if (!senderJid) return;

            const senderName = msg.pushName
                || contactsMap.get(senderJid)?.name
                || senderJid.split('@')[0];

            if (actualMessage.audioMessage) {
                bufferGroupMessage(groupJid, senderName, '[voice note]');
                console.log(`🎤 Group voice note from ${senderName} in ${groupJid}`);
                try {
                    await sock.sendPresenceUpdate('recording', groupJid);
                    const buffer = await downloadMediaMessage(msg, 'buffer', {}, { logger: pino({ level: 'silent' }) });
                    await postWithRetry(PYTHON_GROUP_VOICE_URL, {
                        group_jid:            groupJid,
                        sender_jid:           senderJid,
                        sender_name:          senderName,
                        audio_b64:            buffer.toString('base64'),
                        conversation_context: getGroupContext(groupJid),
                    });
                } catch (error) {
                    console.error('❌ Failed to forward group voice note:', error);
                    await sock.sendPresenceUpdate('paused', groupJid);
                }
                return;
            }

            const textMessage = actualMessage.conversation || actualMessage.extendedTextMessage?.text;
            if (!textMessage) return;

            bufferGroupMessage(groupJid, senderName, textMessage);

            const mentionedJids = actualMessage.extendedTextMessage?.contextInfo?.mentionedJid || [];
            if (!shouldWadeRespond(textMessage, mentionedJids)) return;

            console.log(`👥 Group mention from ${senderName} in ${groupJid}: ${textMessage}`);
            try {
                await sock.sendPresenceUpdate('composing', groupJid);
                await postWithRetry(PYTHON_GROUP_URL, {
                    group_jid:            groupJid,
                    sender_jid:           senderJid,
                    sender_name:          senderName,
                    message:              textMessage,
                    conversation_context: getGroupContext(groupJid),
                });
            } catch (error) {
                console.error('❌ Failed to forward group message:', error);
                await sock.sendPresenceUpdate('paused', groupJid);
            }
            return;
        }

        const sender = resolveJid(rawRemoteJid);
        if (!sender) return;

        const isAudio   = actualMessage.audioMessage;
        const textMsg1v1 = actualMessage.conversation || actualMessage.extendedTextMessage?.text;

        if (isAudio) {
            console.log(`🎤 Inbound voice note from ${sender}`);
            try {
                await sock.sendPresenceUpdate('recording', sender);
                const buffer = await downloadMediaMessage(msg, 'buffer', {}, { logger: pino({ level: 'silent' }) });
                await postWithRetry(PYTHON_VOICE_URL, {
                    sender:    sender,
                    audio_b64: buffer.toString('base64'),
                });
            } catch (error) {
                console.error('❌ Failed to process incoming audio:', error);
                await sock.sendPresenceUpdate('paused', sender);
            }
        } else if (textMsg1v1) {
            console.log(`📥 Inbound text from ${sender}: ${textMsg1v1}`);
            try {
                await sock.sendPresenceUpdate('composing', sender);
                await postWithRetry(PYTHON_API_URL, {
                    sender:  sender,
                    message: textMsg1v1,
                });
            } catch (error) {
                console.error('❌ Failed to forward text:', error);
                await sock.sendPresenceUpdate('paused', sender);
            }
        }
    });
}

app.post('/send-message', async (req, res) => {
    const { to, message } = req.body;
    
    if (!to || !message) {
        return res.status(400).json({ error: 'Missing "to" or "message"' });
    }

    try {
        const jid = to.includes('@') ? to : `${to}@s.whatsapp.net`;
        
        await sock.sendPresenceUpdate('paused', jid);
            
        await sock.sendMessage(jid, { text: message });
        console.log(`📤 Outbound to ${jid}: ${message}`);
        
        res.json({ status: 'sent', to: jid });
    } catch (error) {
        console.error('❌ Error sending message to WhatsApp:', error);
        res.status(500).json({ error: 'Failed to send message over WebSocket' });
    }
});

app.post('/send-voice', async (req, res) => {
    const { to, audio_b64 } = req.body;
    
    if (!to || !audio_b64) {
        return res.status(400).json({ error: 'Missing "to" or "audio_b64"' });
    }

    try {
        const jid = to.includes('@') ? to : `${to}@s.whatsapp.net`;
        const buffer = Buffer.from(audio_b64, 'base64');

        await sock.sendPresenceUpdate('paused', jid);
        
        await sock.sendMessage(jid, { 
            audio: buffer, 
            mimetype: 'audio/ogg; codecs=opus', 
            ptt: true 
        });
        
        console.log(`📤 Outbound voice note to ${jid}`);
        res.json({ status: 'sent', to: jid });
    } catch (error) {
        console.error('❌ Error sending voice note to WhatsApp:', error);
        res.status(500).json({ error: 'Failed to send voice over WebSocket' });
    }
});

app.get('/contacts', (req, res) => {
    const merged = new Map();

    for (const [jid, info] of contactsMap.entries()) {
        let phoneJid = jid.endsWith('@s.whatsapp.net') ? jid : lidToJid.get(jid);
        const effectiveJid = phoneJid || jid;
        
        const existing = merged.get(effectiveJid) || { jid: effectiveJid, name: '', notify: '', phone: '' };
        
        if (info.name && (!existing.name || existing.name.length < info.name.length)) {
            existing.name = info.name;
        }
        if (info.notify && !existing.notify) existing.notify = info.notify;
        
        if (effectiveJid.endsWith('@s.whatsapp.net')) {
            existing.phone = `+${effectiveJid.split('@')[0]}`;
        } else {
            existing.phone = `ID: ${effectiveJid.split('@')[0]}`;
        }
        
        merged.set(effectiveJid, existing);
    }

    const result = Array.from(merged.values());
    result.sort((a, b) => (a.name || 'z').localeCompare(b.name || 'z'));
    res.json(result);
});

app.get('/lid-map', (req, res) => {
    res.json(Object.fromEntries(lidToJid));
});

app.post('/create-group', async (req, res) => {
    const { name, participants } = req.body;

    if (!name || !Array.isArray(participants) || participants.length === 0) {
        return res.status(400).json({ error: 'Missing "name" or "participants" array' });
    }

    try {
        const jids = participants.map(p => {
            if (typeof p === 'string' && p.includes('@')) return p;
            const digits = String(p).replace(/\D/g, '');
            return `${digits}@s.whatsapp.net`;
        });

        const result = await sock.groupCreate(name, jids);
        console.log(`👥 Group created: "${name}" id=${result.id} participants=${jids.join(', ')}`);
        res.json({ status: 'created', group_id: result.id, name, participants: jids });
    } catch (error) {
        console.error('❌ Error creating group:', error);
        res.status(500).json({ error: 'Failed to create group', detail: error.message });
    }
});

app.get('/status', (req, res) => {
    res.json({ connected: _isConnected, botJid: botJid || null, hasQr: !!_currentQrData });
});

app.get('/qr', (req, res) => {
    res.json({ connected: _isConnected, qr: _isConnected ? null : (_currentQrData || null) });
});

app.post('/pair-code', async (req, res) => {
    const { phone } = req.body;
    if (!phone) return res.status(400).json({ error: 'Missing phone number' });
    if (!sock) return res.status(503).json({ error: 'Bridge not ready' });
    try {
        const digits = String(phone).replace(/\D/g, '');
        const code = await sock.requestPairingCode(digits);
        res.json({ code });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/logout', async (req, res) => {
    try { if (sock) await sock.logout().catch(() => {}); } catch (_) {}
    _isConnected = false;
    _currentQrData = null;
    botJid = null;
    _reconnectDelay = 3000;
    try { fs.rmSync('./baileys_auth_info', { recursive: true, force: true }); } catch (_) {}
    setTimeout(connectToWhatsApp, 2000);
    res.json({ ok: true });
});

app.listen(PORT, async () => {
    console.log(`🤖 WhatsApp Bridge API listening on port ${PORT}`);
    await waitForAPI();
    connectToWhatsApp();
});