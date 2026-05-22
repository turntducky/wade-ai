const {
    default: makeWASocket,
    useMultiFileAuthState,
    fetchLatestWaWebVersion,
    jidNormalizedUser,
} = require('@whiskeysockets/baileys');
const pino = require('pino');
const path = require('path');
const fs   = require('fs');

const AUTH_DIR = path.join(__dirname, 'baileys_auth_info');

// Helpers
function dump(label, obj) {
    console.log('\n' + '═'.repeat(72));
    console.log(`▶  ${label}`);
    console.log('─'.repeat(72));
    try {
        console.log(JSON.stringify(obj, replacer, 2));
    } catch (_) {
        console.log(String(obj));
    }
    console.log('═'.repeat(72));
}

function replacer(key, val) {
    if (val instanceof Uint8Array || Buffer.isBuffer(val)) {
        return '<Buffer ' + Buffer.from(val).toString('hex').slice(0, 32) + (val.length > 16 ? '…' : '') + '>';
    }
    return val;
}

function extractAllJidFields(obj, prefix = '') {
    const hits = [];
    if (!obj || typeof obj !== 'object') return hits;
    for (const [k, v] of Object.entries(obj)) {
        const path = prefix ? `${prefix}.${k}` : k;
        if (typeof v === 'string' && (v.includes('@') || /^\d{7,}$/.test(v))) {
            hits.push({ path, value: v });
        } else if (v && typeof v === 'object' && !Buffer.isBuffer(v) && !(v instanceof Uint8Array)) {
            hits.push(...extractAllJidFields(v, path));
        }
    }
    return hits;
}

// Main
async function main() {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version } = await fetchLatestWaWebVersion();

    console.log(`\n🔍 WA Debug — Baileys v${version.join('.')} — waiting for messages…`);
    console.log('Send a message from the LID account you want to inspect.\n');

    const sock = makeWASocket({
        version,
        auth:    state,
        logger:  pino({ level: 'silent' }),
        browser: ['Chrome', 'Windows', '110.0.5481.177'],
    });

    sock.ev.on('creds.update', saveCreds);

    // Contact events
    sock.ev.on('contacts.upsert', contacts => {
        for (const c of contacts) {
            if (!c.id) continue;
            const norm = jidNormalizedUser(c.id);
            if (!norm?.endsWith('@s.whatsapp.net') && !norm?.includes('@lid')) continue;
            dump(`contacts.upsert — ${c.id}`, c);
            console.log('\n  JID-like fields in this contact:');
            for (const hit of extractAllJidFields(c)) {
                console.log(`    ${hit.path} = ${hit.value}`);
            }
        }
    });

    sock.ev.on('contacts.update', contacts => {
        for (const c of contacts) {
            if (!c.id) continue;
            dump(`contacts.update — ${c.id}`, c);
        }
    });

    sock.ev.on('messaging-history.set', ({ contacts, messages }) => {
        if (contacts?.length) {
            console.log(`\n📋 messaging-history.set: ${contacts.length} contacts`);
            for (const c of contacts.slice(0, 5)) {
                console.log(`   ${c.id}  lid=${c.lid || 'none'}  name=${c.name || c.notify || 'none'}`);
            }
            if (contacts.length > 5) console.log(`   … and ${contacts.length - 5} more`);
        }
    });

    // Message events
    sock.ev.on('messages.upsert', ({ messages, type }) => {
        for (const msg of messages) {
            if (msg.key.fromMe) continue;

            const remoteJid = msg.key.remoteJid || '';
            const isLid     = remoteJid.includes('@lid') || (msg.key.participant || '').includes('@lid');

            dump(`messages.upsert [type=${type}] — key`, msg.key);

            console.log('\n  JID-like fields in msg.key:');
            for (const hit of extractAllJidFields(msg.key)) {
                console.log(`    ${hit.path} = ${hit.value}`);
            }

            const msgMeta = {
                pushName:        msg.pushName,
                status:          msg.status,
                broadcast:       msg.broadcast,
                messageTimestamp: msg.messageTimestamp,
                participant:     msg.participant,
                messageStubType: msg.messageStubType,
                messageStubParameters: msg.messageStubParameters,
                verifiedBizName: msg.verifiedBizName,
                messageTypes: msg.message ? Object.keys(msg.message) : [],
            };
            dump('messages.upsert — msg metadata (no content)', msgMeta);

            if (isLid) {
                console.log('\n🚨 LID MESSAGE DETECTED — full analysis:');
                console.log(`   remoteJid  : ${remoteJid}`);
                console.log(`   participant: ${msg.key.participant || 'n/a'}`);
                console.log(`   senderPn   : ${msg.key.senderPn || 'NOT PRESENT'}`);
                console.log(`   pushName   : ${msg.pushName || 'n/a'}`);

                console.log('\n  All JID/phone-like fields anywhere in full msg:');
                for (const hit of extractAllJidFields(msg)) {
                    console.log(`    ${hit.path} = ${hit.value}`);
                }

                dump('LID message — raw msg.key (full)', msg.key);
            }
        }
    });

    sock.ev.on('connection.update', ({ connection, qr }) => {
        if (qr)                     console.log('\n📱 QR ready — scan to connect.');
        if (connection === 'open')  console.log('\n✅ Connected. Send a message to see debug output.');
        if (connection === 'close') console.log('\n⚠️  Connection closed.');
    });
}

main().catch(e => { console.error(e); process.exit(1); });