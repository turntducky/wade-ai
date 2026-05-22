const _CREDS_META = {
    openai:    { label: 'OpenAI',    desc: 'GPT-4o · Embeddings',   group: 'llm',          fields: [{ key: 'api_key',       label: 'API Key',          type: 'password' }] },
    anthropic: { label: 'Anthropic', desc: 'Claude 3.5 Sonnet',     group: 'llm',          fields: [{ key: 'api_key',       label: 'API Key',          type: 'password' }] },
    gemini:    { label: 'Gemini',    desc: 'Flash · Pro',            group: 'llm',          fields: [{ key: 'api_key',       label: 'API Key',          type: 'password' }] },
    notion:    { label: 'Notion',    desc: 'Workspace sync',         group: 'integrations', fields: [{ key: 'token',         label: 'Integration Token',type: 'password' }] },
    blink:     { label: 'Blink',     desc: 'Camera feeds',           group: 'integrations', fields: [{ key: 'email',         label: 'Email',            type: 'email'    },
                                                                                                      { key: 'password',      label: 'Password',         type: 'password' }],
               twofa: { loginUrl: '/api/blink/login', verifyUrl: '/api/blink/verify', statusUrl: '/api/blink/status', disconnectUrl: '/api/blink/disconnect',
                        hint: "Click Login — Blink will text a verification code to your registered phone number." } },
    spotify:   { label: 'Spotify',   desc: 'Playback · Search · History', group: 'integrations', fields: [{ key: 'client_id',     label: 'Client ID',        type: 'text'     },
                                                                                                           { key: 'client_secret', label: 'Client Secret',    type: 'password' }],
               oauth: { authUrl: '/api/spotify/auth', statusUrl: '/api/spotify/status', disconnectUrl: '/api/spotify/disconnect',
                        hint: "In your Spotify app dashboard, add the Redirect URI shown below, then save your Client ID & Secret and click Connect." } },
};

let _credsOpen         = null;
let _credsStatus       = {};
let _credsOAuthStatus  = {};
let _credsTwofaStatus  = {};
let _credsPinPending   = null;
let _credsClearPending = null;

// WhatsApp pairing state
let _waStatus    = null;
let _waQr        = null;
let _waPairCode  = null;
let _waTab       = 'qr';
let _waPollRef   = null;

let _assistantName = 'W.A.D.E.';

function _htmlEscape(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

async function _loadAssistantName() {
    try {
        const r = await fetch('/api/v1/config', { headers: _authHeaders() });
        const data = await r.json();
        _assistantName = data.assistant_name || 'W.A.D.E.';
    } catch (e) {
        _assistantName = 'W.A.D.E.';
    }
}

async function saveAssistantName() {
    const input = document.getElementById('assistant-name-input');
    const btn   = document.getElementById('assistant-name-save-btn');
    const name  = input ? input.value.trim() : '';
    if (!name) return;
    try {
        const r = await fetch('/api/v1/config', {
            method: 'PATCH',
            headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ assistant_name: name }),
        });
        if (r.ok) {
            const data = await r.json();
            _assistantName = data.assistant_name;
            if (btn) { btn.textContent = 'Saved'; setTimeout(() => { btn.textContent = 'Save'; }, 2000); }
        }
    } catch (e) {}
}

function _renderGeneralSettings() {
    return `
        <div class="mb-8 pb-6 border-b border-white/5">
            <p class="text-[11px] font-bold text-zinc-500 uppercase tracking-widest mb-3">General</p>
            <div class="flex items-end gap-3">
                <div class="flex-1">
                    <label class="text-[10px] font-bold text-zinc-500 uppercase tracking-widest block mb-1">Assistant Name</label>
                    <input id="assistant-name-input"
                           type="text"
                           value="${_htmlEscape(_assistantName)}"
                           placeholder="W.A.D.E."
                           maxlength="64"
                           class="w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:border-violet-500/50" />
                </div>
                <button id="assistant-name-save-btn"
                        onclick="saveAssistantName()"
                        class="px-4 py-2 text-xs font-semibold rounded bg-violet-600 hover:bg-violet-500 text-white transition-colors whitespace-nowrap">Save</button>
            </div>
        </div>
    `;
}

function _renderWhatsAppSection() {
    const connected = _waStatus?.connected === true;
    const botJid    = _waStatus?.botJid || null;
    let statusBadge, bodyHtml;

    if (_waStatus === null) {
        statusBadge = `<span class="text-[10px] text-zinc-600">Bridge unreachable</span>`;
        bodyHtml    = `<p class="text-[11px] text-zinc-600 italic">The WhatsApp bridge is not running. Start it with <code class="text-zinc-400">docker compose up whatsapp</code>.</p>`;
    } else if (connected) {
        const phone = botJid ? `+${botJid.split('@')[0]}` : 'Unknown';
        statusBadge = `<span class="inline-flex items-center gap-1 text-[10px] text-green-400"><span class="w-1.5 h-1.5 rounded-full bg-green-400 inline-block"></span> Connected as ${_htmlEscape(phone)}</span>`;
        bodyHtml    = `
            <p class="text-[11px] text-zinc-600 mb-3">W.A.D.E. is linked to your WhatsApp account. Disconnecting will log out this session on WhatsApp too.</p>
            <button onclick="waDisconnect()" class="h-8 px-4 text-xs border border-white/10 rounded-lg text-red-400 hover:border-red-500/30 transition-colors">Disconnect</button>
        `;
    } else {
        statusBadge = `<span class="text-[10px] text-zinc-600">Not connected</span>`;
        const isQr   = _waTab === 'qr';
        const isPair = _waTab === 'pair';

        const qrContent = isQr
            ? (_waQr
                ? `<div class="flex flex-col items-center gap-3 py-2">
                       <img src="${_waQr}" class="w-52 h-52 rounded-xl border border-white/10" alt="WhatsApp QR Code" />
                       <p class="text-[10px] text-zinc-500 text-center">Open WhatsApp → Linked Devices → Link a Device → scan this code.</p>
                       <p class="text-[10px] text-zinc-600 text-center">Refreshes automatically.</p>
                   </div>`
                : `<div class="flex items-center gap-2 py-4 text-zinc-600 text-[11px]">
                       <div class="w-4 h-4 border-2 border-zinc-700 border-t-violet-500 rounded-full animate-spin flex-shrink-0"></div>
                       Waiting for QR code from bridge…
                   </div>`)
            : '';

        const pairContent = isPair
            ? `<div class="flex flex-col gap-3 py-1">
                   <div>
                       <label class="text-[10px] font-bold text-zinc-500 uppercase tracking-widest block mb-1">Phone Number</label>
                       <div class="flex gap-2">
                           <input id="wa-phone-input" type="tel" placeholder="+1 234 567 8900"
                                  class="flex-1 bg-void border border-white/10 text-white placeholder-zinc-700 px-3 py-2 rounded-lg outline-none focus:border-violet-500/50 font-mono text-xs transition-all" />
                           <button onclick="waRequestPairCode()" id="wa-pair-btn"
                                   class="h-9 px-4 text-xs font-semibold rounded-lg border border-white/15 text-zinc-200 hover:border-violet-500/50 hover:text-white transition-colors whitespace-nowrap">
                               Get Code
                           </button>
                       </div>
                       <p class="text-[10px] text-zinc-600 mt-1">Include country code (e.g. +1 for US).</p>
                   </div>
                   ${_waPairCode ? `
                   <div>
                       <p class="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-2">Your Pairing Code</p>
                       <div class="flex items-center gap-3">
                           <code class="text-2xl font-mono font-bold text-white tracking-[0.25em] bg-void border border-white/10 px-5 py-2.5 rounded-xl">${_htmlEscape(_waPairCode)}</code>
                           <button onclick="navigator.clipboard.writeText('${_htmlEscape(_waPairCode)}').then(()=>{this.textContent='Copied!';setTimeout(()=>{this.textContent='Copy'},2000)})"
                                   class="h-8 px-3 text-xs border border-white/10 rounded-lg text-zinc-400 hover:text-white transition-colors">Copy</button>
                       </div>
                       <p class="text-[10px] text-zinc-500 mt-2">Open WhatsApp → Linked Devices → Link with phone number → enter this code.</p>
                       <p class="text-[10px] text-zinc-600 mt-0.5">Expires in ~60 seconds.</p>
                   </div>` : ''}
                   <span id="wa-pair-result" class="hidden text-xs"></span>
               </div>`
            : '';

        bodyHtml = `
            <div class="flex gap-1 mb-4 p-0.5 bg-white/[0.03] rounded-lg w-fit border border-white/5">
                <button onclick="waSetTab('qr')"
                        class="px-4 py-1.5 text-[11px] rounded-md transition-colors ${isQr ? 'bg-white/10 text-white font-semibold' : 'text-zinc-500 hover:text-zinc-300'}">
                    Scan QR
                </button>
                <button onclick="waSetTab('pair')"
                        class="px-4 py-1.5 text-[11px] rounded-md transition-colors ${isPair ? 'bg-white/10 text-white font-semibold' : 'text-zinc-500 hover:text-zinc-300'}">
                    Pairing Code
                </button>
            </div>
            ${qrContent}${pairContent}
        `;
    }

    return `
        <div class="mb-6 pb-6 border-b border-white/5">
            <div class="flex items-center justify-between mb-1">
                <div class="flex items-center gap-2">
                    <svg class="w-4 h-4 text-zinc-500" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z"/></svg>
                    <p class="text-[11px] font-bold text-zinc-500 uppercase tracking-widest">WhatsApp</p>
                </div>
                ${statusBadge}
            </div>
            <p class="text-[11px] text-zinc-600 mb-4 ml-6">Link W.A.D.E. to your WhatsApp so it can send and receive messages on your behalf.</p>
            ${bodyHtml}
        </div>
    `;
}

function _waRender() {
    const el = document.getElementById('wa-section-container');
    if (!el) return;
    el.innerHTML = _renderWhatsAppSection();
    if (_waStatus?.connected) {
        _waPollStop();
    } else if (!_waPollRef) {
        _waPollStart();
    }
}

function _waPollStart() {
    if (_waPollRef) return;
    _waPollRef = setInterval(_waFetch, 3000);
}

function _waPollStop() {
    clearInterval(_waPollRef);
    _waPollRef = null;
}

async function _waFetch() {
    if (typeof STATE !== 'undefined' && STATE.activeTab !== 'credentials') {
        _waPollStop();
        return;
    }
    try {
        const prevConnected = _waStatus?.connected;
        const statusR = await fetch('/api/v1/whatsapp/status', { headers: _authHeaders() }).catch(() => null);
        _waStatus = statusR?.ok ? await statusR.json() : null;

        if (_waTab === 'qr' && !_waStatus?.connected) {
            const qrR = await fetch('/api/v1/whatsapp/qr', { headers: _authHeaders() }).catch(() => null);
            if (qrR?.ok) {
                const d = await qrR.json();
                _waQr = d.qr || null;
            }
        }

        if (_waStatus?.connected && !prevConnected) _waPollStop();
        _waRender();
    } catch (_) {}
}

async function _waInit() {
    try {
        const [statusR, qrR] = await Promise.all([
            fetch('/api/v1/whatsapp/status', { headers: _authHeaders() }).catch(() => null),
            fetch('/api/v1/whatsapp/qr',     { headers: _authHeaders() }).catch(() => null),
        ]);
        _waStatus = statusR?.ok ? await statusR.json() : null;
        if (qrR?.ok) { const d = await qrR.json(); _waQr = d.qr || null; }
    } catch (_) { _waStatus = null; }
}

function waSetTab(tab) {
    _waTab = tab;
    if (tab !== 'pair') _waPairCode = null;
    if (tab === 'qr' && !_waStatus?.connected) _waFetch();
    else _waRender();
}

async function waRequestPairCode() {
    const input = document.getElementById('wa-phone-input');
    const btn   = document.getElementById('wa-pair-btn');
    const res   = document.getElementById('wa-pair-result');
    const phone = input?.value.trim().replace(/\D/g, '') || '';
    if (phone.length < 7) {
        if (res) { res.textContent = '✗ Enter a valid phone number with country code'; res.className = 'text-xs text-red-400'; res.classList.remove('hidden'); }
        return;
    }
    if (btn) { btn.textContent = 'Requesting…'; btn.disabled = true; }
    try {
        const r = await fetch('/api/v1/whatsapp/pair-code', {
            method: 'POST',
            headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone }),
        });
        const data = await r.json();
        if (data.code) {
            _waPairCode = data.code;
            _waRender();
        } else {
            if (res) { res.textContent = `✗ ${data.error || 'Failed to get code'}`; res.className = 'text-xs text-red-400'; res.classList.remove('hidden'); }
        }
    } catch (_) {
        if (res) { res.textContent = '✗ Request failed'; res.className = 'text-xs text-red-400'; res.classList.remove('hidden'); }
    } finally {
        if (btn) { btn.textContent = 'Get Code'; btn.disabled = false; }
    }
}

async function waDisconnect() {
    try {
        await fetch('/api/v1/whatsapp/disconnect', { method: 'POST', headers: _authHeaders() });
    } catch (_) {}
    _waStatus = { connected: false, botJid: null, hasQr: false };
    _waQr = null;
    _waPairCode = null;
    _waRender();
    _waPollStart();
}

async function initCredentials() {
    _waPollStop();
    const root = document.getElementById('credentials-root');
    if (root) root.innerHTML = `
        <div class="flex items-center gap-3 text-zinc-500 text-sm py-10">
            <div class="animate-spin w-4 h-4 border-2 border-zinc-700 border-t-violet-500 rounded-full flex-shrink-0"></div>
            Loading credentials…
        </div>
    `;

    const oauthServices = Object.entries(_CREDS_META)
        .filter(([, m]) => m.oauth)
        .map(([svc, m]) => ({ svc, url: m.oauth.statusUrl }));

    const twofaServices = Object.entries(_CREDS_META)
        .filter(([, m]) => m.twofa)
        .map(([svc, m]) => ({ svc, url: m.twofa.statusUrl }));

    try {
        const fetches = [
            fetch('/api/credentials', { headers: _authHeaders() }),
            _loadAssistantName(),
            _waInit(),
            ...oauthServices.map(({ url }) => fetch(url, { headers: _authHeaders() }).catch(() => null)),
            ...twofaServices.map(({ url }) => fetch(url, { headers: _authHeaders() }).catch(() => null)),
        ];
        const results = await Promise.all(fetches);
        _credsStatus = await results[0].json();
        const oauthResults = results.slice(3, 3 + oauthServices.length);
        const twofaResults = results.slice(3 + oauthServices.length);

        for (let i = 0; i < oauthServices.length; i++) {
            const r = oauthResults[i];
            if (r && r.ok) {
                try { _credsOAuthStatus[oauthServices[i].svc] = await r.json(); } catch (_) {}
            }
        }
        for (let i = 0; i < twofaServices.length; i++) {
            const r = twofaResults[i];
            if (r && r.ok) {
                try { _credsTwofaStatus[twofaServices[i].svc] = await r.json(); } catch (_) {}
            }
        }
    } catch (e) {
        _credsStatus = {};
    }
    _renderCredentials();
    if (!_waStatus?.connected) _waPollStart();
}

async function _refreshOAuthStatus(svc) {
    const meta = _CREDS_META[svc];
    if (!meta?.oauth) return;
    try {
        const r = await fetch(meta.oauth.statusUrl, { headers: _authHeaders() });
        if (r.ok) _credsOAuthStatus[svc] = await r.json();
    } catch (_) {}
}

function _renderCredentials() {
    const root = document.getElementById('credentials-root');
    if (!root) return;

    const groups = { llm: [], integrations: [] };
    for (const [svc, meta] of Object.entries(_CREDS_META)) {
        groups[meta.group].push(svc);
    }

    root.innerHTML = `
        <div class="mb-8 pb-4 border-b border-white/5">
            <h2 class="font-display text-lg font-bold text-white tracking-wide">Credentials & Integrations</h2>
            <p class="text-sm text-zinc-500 mt-1">Keys are stored locally in ~/.wade/credentials.json with 0600 permissions. Never sent to any server.</p>
        </div>
        ${_renderGeneralSettings()}
        <div id="wa-section-container">${_renderWhatsAppSection()}</div>
        <div class="flex flex-col gap-8">
            ${_renderGroup('LLM Providers', groups.llm)}
            ${_renderGroup('Integrations', groups.integrations)}
        </div>
    `;
}

function _renderGroup(title, services) {
    return `
        <div>
            <p class="text-[11px] font-bold text-zinc-500 uppercase tracking-widest mb-3 pb-3 border-b border-white/5">${title}</p>
            <div class="flex flex-col gap-2">
                ${services.map(_renderRow).join('')}
            </div>
        </div>
    `;
}

function _renderRow(svc) {
    const meta        = _CREDS_META[svc];
    const status      = _credsStatus[svc] || { configured: false };
    const oauthStatus = _credsOAuthStatus[svc] || null;
    const isOpen      = _credsOpen === svc;
    const configured  = status.configured;

    const oauthAuthorized  = meta.oauth  ? (oauthStatus?.authorized === true) : null;
    const twofaConnected   = meta.twofa  ? (_credsTwofaStatus[svc]?.connected === true) : null;
    const fullyReady       = meta.oauth  ? (configured && oauthAuthorized)
                           : meta.twofa  ? (configured && twofaConnected)
                           : configured;

    const dotColor  = fullyReady ? 'bg-green-400' : configured ? 'bg-yellow-400' : 'bg-zinc-600';
    const borderCls = isOpen
        ? 'border border-violet-500/30 bg-[#12121f]'
        : fullyReady
            ? 'border border-white/5 bg-surface'
            : 'border border-dashed border-white/5 bg-surface';

    let badgeHtml;
    if (meta.oauth) {
        if (oauthAuthorized)   badgeHtml = `<span class="text-[10px] text-green-400">connected</span>`;
        else if (configured)   badgeHtml = `<span class="text-[10px] text-yellow-400">keys saved · not connected</span>`;
        else                   badgeHtml = `<span class="text-[10px] text-zinc-600">not set</span>`;
    } else if (meta.twofa) {
        if (twofaConnected)    badgeHtml = `<span class="text-[10px] text-green-400">connected</span>`;
        else if (configured)   badgeHtml = `<span class="text-[10px] text-yellow-400">keys saved · not connected</span>`;
        else                   badgeHtml = `<span class="text-[10px] text-zinc-600">not set</span>`;
    } else {
        badgeHtml = configured
            ? `<span class="text-[10px] text-green-400">configured</span>`
            : `<span class="text-[10px] text-zinc-600">not set</span>`;
    }

    return `
        <div class="rounded-lg overflow-hidden ${borderCls}">
            <button onclick="toggleAccordion('${svc}')"
                    class="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-white/[0.02] transition-colors">
                <div class="flex items-center gap-3">
                    <div class="w-2 h-2 rounded-full ${dotColor} flex-shrink-0"></div>
                    <span class="text-sm font-medium ${configured ? 'text-zinc-100' : 'text-zinc-500'}">${meta.label}</span>
                    <span class="text-xs text-zinc-600">${meta.desc}</span>
                </div>
                <div class="flex items-center gap-3">
                    ${badgeHtml}
                    <span class="text-zinc-600 text-xs">${isOpen ? '▲' : '▼'}</span>
                </div>
            </button>
            ${isOpen ? _renderExpanded(svc, meta, configured, oauthStatus) : ''}
        </div>
    `;
}

function _renderExpanded(svc, meta, configured, oauthStatus) {
    const fieldCount = meta.fields.length;
    const gridCls    = fieldCount > 1 ? 'grid grid-cols-2 gap-3' : 'flex flex-col gap-3';
    const hint       = configured
        ? `<p class="text-[10px] text-zinc-600 mb-3">Currently saved — enter a new value to update.</p>`
        : '';

    const fields = meta.fields.map(f => `
        <div>
            <label class="text-[10px] font-bold text-zinc-500 uppercase tracking-widest block mb-1">${f.label}</label>
            <div class="flex gap-2">
                <input id="cred-${svc}-${f.key}"
                       type="${f.type === 'password' ? 'password' : f.type}"
                       class="flex-1 bg-void border border-white/10 text-white placeholder-zinc-700 px-3 py-2 rounded-lg outline-none focus:border-violet-500/50 font-mono text-xs transition-all"
                       placeholder="${f.type === 'password' ? '••••••••••••' : f.label.toLowerCase()}"
                       oninput="_credsValidateSave('${svc}')">
                ${f.type === 'password' ? `<button onclick="_credsToggleShow('${svc}-${f.key}')" class="px-2 text-zinc-600 hover:text-zinc-300 text-xs border border-white/10 rounded-lg">show</button>` : ''}
            </div>
        </div>
    `).join('');

    const oauthSection  = meta.oauth  ? _renderOAuthSection(svc, meta.oauth, configured, oauthStatus) : '';
    const twofaSection  = meta.twofa  ? _renderTwofaSection(svc, meta.twofa, configured) : '';

    return `
        <div class="px-4 pb-4 border-t border-white/5 pt-4">
            ${hint}
            <div class="${gridCls} mb-4">
                ${fields}
            </div>
            <div class="flex items-center gap-2 flex-wrap">
                <button id="cred-${svc}-save"
                        onclick="saveCredentials('${svc}')"
                        class="btn-execute h-8 px-4 text-xs opacity-50 cursor-not-allowed"
                        disabled>${configured ? 'Update' : 'Save'}</button>
                ${configured ? `
                <button onclick="testCredentials('${svc}')" id="cred-${svc}-test"
                        class="h-8 px-4 text-xs border border-white/10 rounded-lg text-zinc-300 hover:border-violet-500/50 transition-colors">
                    Test Keys
                </button>` : ''}
                ${configured ? `
                <button onclick="clearCredentials('${svc}')" id="cred-${svc}-clear"
                        class="h-8 px-4 text-xs border border-white/10 rounded-lg text-red-400 hover:border-red-500/50 transition-colors">
                    Clear
                </button>` : ''}
                <span id="cred-${svc}-result" class="hidden text-xs ml-1"></span>
            </div>
            ${oauthSection}
            ${twofaSection}
        </div>
    `;
}

function _renderOAuthSection(svc, oauth, keysConfigured, oauthStatus) {
    const authorized   = oauthStatus?.authorized === true;
    const tokenExpired = oauthStatus?.token_expired === true;

    let statusBadge = '';
    if (authorized) {
        statusBadge = `<span class="inline-flex items-center gap-1 text-[10px] text-green-400"><span class="w-1.5 h-1.5 rounded-full bg-green-400 inline-block"></span> Connected</span>`;
    } else if (tokenExpired) {
        statusBadge = `<span class="inline-flex items-center gap-1 text-[10px] text-yellow-400"><span class="w-1.5 h-1.5 rounded-full bg-yellow-400 inline-block"></span> Token expired — reconnect</span>`;
    } else if (oauthStatus?.has_tokens) {
        statusBadge = `<span class="text-[10px] text-yellow-400">Tokens present but status unknown</span>`;
    } else {
        statusBadge = `<span class="text-[10px] text-zinc-600">Not connected</span>`;
    }

    const connectBtn = keysConfigured
        ? `<a href="${oauth.authUrl}" target="_blank"
               class="inline-flex items-center gap-2 h-8 px-4 text-xs font-semibold rounded-lg
                      bg-[#1DB954] hover:bg-[#1ed760] text-black transition-colors">
               <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/></svg>
               ${authorized ? 'Reconnect' : 'Connect Spotify'}
           </a>`
        : `<span class="text-[10px] text-zinc-600 italic">Save your Client ID above first, then connect.</span>`;

    const disconnectBtn = authorized
        ? `<button onclick="disconnectOAuth('${svc}')"
                   class="h-8 px-3 text-xs border border-white/10 rounded-lg text-zinc-500 hover:text-red-400 hover:border-red-500/30 transition-colors">
               Disconnect
           </button>`
        : '';

    const redirectUri = oauthStatus?.redirect_uri || null;
    const redirectUriBlock = redirectUri ? `
        <div class="mt-3">
            <p class="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-1">Redirect URI — paste this into your Spotify app dashboard</p>
            <div class="flex items-center gap-2">
                <code class="flex-1 text-[11px] font-mono text-zinc-300 bg-void border border-white/10 px-3 py-1.5 rounded-lg overflow-x-auto">${_htmlEscape(redirectUri)}</code>
                <button onclick="navigator.clipboard.writeText('${_htmlEscape(redirectUri)}').then(()=>{this.textContent='Copied!';setTimeout(()=>{this.textContent='Copy'},1500)})"
                        class="flex-shrink-0 h-7 px-3 text-xs border border-white/10 rounded-lg text-zinc-400 hover:text-white transition-colors whitespace-nowrap">Copy</button>
            </div>
            <p class="text-[10px] text-zinc-600 mt-1">Spotify will show a "not secure" warning — this is expected and safe. Press <strong class="text-zinc-400">Enter</strong> or click <strong class="text-zinc-400">Add</strong> to save it anyway.</p>
        </div>
    ` : '';

    return `
        <div class="mt-4 pt-4 border-t border-white/5">
            <div class="flex items-center justify-between mb-2">
                <p class="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Account Authorization</p>
                ${statusBadge}
            </div>
            <p class="text-[10px] text-zinc-600 mb-3">${_htmlEscape(oauth.hint)}</p>
            ${redirectUriBlock}
            <div class="flex items-center gap-2 flex-wrap mt-3">
                ${connectBtn}
                ${disconnectBtn}
                <span id="oauth-${svc}-result" class="hidden text-xs ml-1"></span>
            </div>
        </div>
    `;
}

function _renderTwofaSection(svc, twofa, keysConfigured) {
    const status    = _credsTwofaStatus[svc] || {};
    const connected = status.connected === true;
    const pinMode   = _credsPinPending === svc;

    let statusBadge;
    if (connected) {
        statusBadge = `<span class="inline-flex items-center gap-1 text-[10px] text-green-400"><span class="w-1.5 h-1.5 rounded-full bg-green-400 inline-block"></span> Connected</span>`;
    } else if (pinMode) {
        statusBadge = `<span class="text-[10px] text-yellow-400">Enter the code Blink texted you</span>`;
    } else {
        statusBadge = `<span class="text-[10px] text-zinc-600">Not connected</span>`;
    }

    let actionHtml;
    if (pinMode) {
        actionHtml = `
            <div class="flex items-center gap-2">
                <input id="twofa-${svc}-pin"
                       type="text"
                       inputmode="numeric"
                       maxlength="6"
                       placeholder="123456"
                       class="w-28 bg-void border border-white/10 text-white placeholder-zinc-700 px-3 py-1.5 rounded-lg outline-none focus:border-violet-500/50 font-mono text-xs text-center" />
                <button onclick="verifyTwofa('${svc}')"
                        class="h-8 px-4 text-xs font-semibold rounded-lg bg-violet-600 hover:bg-violet-500 text-white transition-colors">
                    Verify
                </button>
                <button onclick="cancelTwofa('${svc}')"
                        class="h-8 px-3 text-xs border border-white/10 rounded-lg text-zinc-500 hover:text-zinc-300 transition-colors">
                    Cancel
                </button>
            </div>`;
    } else if (connected) {
        actionHtml = `
            <button onclick="disconnectTwofa('${svc}')"
                    class="h-8 px-3 text-xs border border-white/10 rounded-lg text-zinc-500 hover:text-red-400 hover:border-red-500/30 transition-colors">
                Disconnect
            </button>`;
    } else if (keysConfigured) {
        actionHtml = `
            <button onclick="loginTwofa('${svc}')" id="twofa-${svc}-login"
                    class="h-8 px-4 text-xs font-semibold rounded-lg border border-white/15 text-zinc-200 hover:border-violet-500/50 hover:text-white transition-colors">
                Login
            </button>`;
    } else {
        actionHtml = `<span class="text-[10px] text-zinc-600 italic">Save your credentials above first.</span>`;
    }

    return `
        <div class="mt-4 pt-4 border-t border-white/5">
            <div class="flex items-center justify-between mb-2">
                <p class="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Account Login</p>
                ${statusBadge}
            </div>
            <p class="text-[10px] text-zinc-600 mb-3">${_htmlEscape(twofa.hint)}</p>
            <div class="flex items-center gap-2 flex-wrap">
                ${actionHtml}
                <span id="twofa-${svc}-result" class="hidden text-xs ml-1"></span>
            </div>
        </div>
    `;
}

async function loginTwofa(svc) {
    const btn = document.getElementById(`twofa-${svc}-login`);
    if (btn) { btn.textContent = 'Sending…'; btn.disabled = true; }
    try {
        const r    = await fetch(_CREDS_META[svc].twofa.loginUrl, { method: 'POST', headers: _authHeaders() });
        const data = await r.json();
        if (!r.ok) {
            _showTwofaResult(svc, false, data.detail || 'Login failed');
            if (btn) { btn.textContent = 'Login'; btn.disabled = false; }
            return;
        }
        if (data.needs_2fa) {
            _credsPinPending = svc;
            _renderCredentials();
        } else {
            _credsTwofaStatus[svc] = { connected: true };
            _credsPinPending = null;
            _renderCredentials();
        }
    } catch (e) {
        _showTwofaResult(svc, false, 'Request failed');
        if (btn) { btn.textContent = 'Login'; btn.disabled = false; }
    }
}

async function verifyTwofa(svc) {
    const input = document.getElementById(`twofa-${svc}-pin`);
    const pin   = input ? input.value.trim() : '';
    if (!pin) { _showTwofaResult(svc, false, 'Enter the 6-digit code'); return; }
    try {
        const r    = await fetch(_CREDS_META[svc].twofa.verifyUrl, {
            method:  'POST',
            headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
            body:    JSON.stringify({ pin }),
        });
        const data = await r.json();
        if (data.ok) {
            _credsTwofaStatus[svc] = { connected: true };
            _credsPinPending = null;
            _renderCredentials();
        } else {
            _showTwofaResult(svc, false, data.message || 'Invalid code');
        }
    } catch (e) {
        _showTwofaResult(svc, false, 'Request failed');
    }
}

function cancelTwofa(svc) {
    _credsPinPending = null;
    _renderCredentials();
}

async function disconnectTwofa(svc) {
    try {
        const r = await fetch(_CREDS_META[svc].twofa.disconnectUrl, { method: 'POST', headers: _authHeaders() });
        if (r.ok) {
            _credsTwofaStatus[svc] = { connected: false };
            _renderCredentials();
        }
    } catch (e) {
        console.error('[credentials] twofa disconnect failed:', e);
    }
}

function _showTwofaResult(svc, ok, message) {
    const el = document.getElementById(`twofa-${svc}-result`);
    if (!el) return;
    el.textContent = ok ? `✓ ${message}` : `✗ ${message}`;
    el.className   = `text-xs ml-1 ${ok ? 'text-green-400' : 'text-red-400'}`;
    el.classList.remove('hidden');
    setTimeout(() => el.classList.add('hidden'), 4000);
}

async function disconnectOAuth(svc) {
    const meta = _CREDS_META[svc];
    if (!meta?.oauth) return;
    try {
        const r = await fetch(meta.oauth.disconnectUrl, {
            method:  'POST',
            headers: _authHeaders(),
        });
        if (r.ok) {
            _credsOAuthStatus[svc] = { authorized: false, has_tokens: false, token_expired: false };
            _renderCredentials();
        }
    } catch (e) {
        console.error('[credentials] oauth disconnect failed:', e);
    }
}

function toggleAccordion(svc) {
    _credsClearPending = null;
    _credsOpen = _credsOpen === svc ? null : svc;
    _renderCredentials();
}

function _credsToggleShow(inputId) {
    const input = document.getElementById(`cred-${inputId}`);
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
}

function _credsValidateSave(svc) {
    const meta = _CREDS_META[svc];
    const btn  = document.getElementById(`cred-${svc}-save`);
    if (!btn) return;
    const allFilled = meta.fields.every(f => {
        const el = document.getElementById(`cred-${svc}-${f.key}`);
        return el && el.value.trim().length > 0;
    });
    btn.disabled = !allFilled;
    btn.classList.toggle('opacity-50',        !allFilled);
    btn.classList.toggle('cursor-not-allowed', !allFilled);
}

async function saveCredentials(svc) {
    const meta = _CREDS_META[svc];
    const data = {};
    meta.fields.forEach(f => {
        const el = document.getElementById(`cred-${svc}-${f.key}`);
        if (el) data[f.key] = el.value.trim();
    });
    try {
        const r = await fetch(`/api/credentials/${svc}`, {
            method:  'POST',
            headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
            body:    JSON.stringify(data),
        });
        if (r.ok) {
            _credsStatus[svc] = { ..._credsStatus[svc], configured: true };
            await _refreshOAuthStatus(svc);
            _renderCredentials();
            _showCredsResult(svc, true, 'Saved');
        } else {
            _showCredsResult(svc, false, 'Failed to save');
        }
    } catch (e) {
        console.error('[credentials] save failed:', e);
        _showCredsResult(svc, false, 'Request failed');
    }
}

async function clearCredentials(svc) {
    if (_credsClearPending === svc) {
        _credsClearPending = null;
        try {
            await fetch(`/api/credentials/${svc}`, {
                method:  'DELETE',
                headers: _authHeaders(),
            });
            _credsStatus[svc] = { ..._credsStatus[svc], configured: false };
            _credsOpen = null;
            _renderCredentials();
        } catch (e) {
            console.error('[credentials] clear failed:', e);
            _showCredsResult(svc, false, 'Clear failed');
        }
        return;
    }

    _credsClearPending = svc;
    const el = document.getElementById(`cred-${svc}-result`);
    if (el) {
        el.innerHTML = `Clear saved credentials? <button onclick="clearCredentials('${svc}')" class="text-red-400 underline ml-1">Confirm</button> · <button onclick="_cancelClear('${svc}')" class="text-zinc-500 underline ml-1">Cancel</button>`;
        el.className = 'text-xs ml-1 text-zinc-400';
        el.classList.remove('hidden');
        setTimeout(() => {
            if (_credsClearPending === svc) _cancelClear(svc);
        }, 6000);
    }
}

function _cancelClear(svc) {
    _credsClearPending = null;
    const el = document.getElementById(`cred-${svc}-result`);
    if (el) el.classList.add('hidden');
}

async function testCredentials(svc) {
    const btn = document.getElementById(`cred-${svc}-test`);
    if (btn) { btn.textContent = 'Testing…'; btn.disabled = true; }
    try {
        const r    = await fetch(`/api/credentials/${svc}/test`, {
            method:  'POST',
            headers: _authHeaders(),
        });
        const data = await r.json();
        _showCredsResult(svc, data.ok, data.message);
    } catch (e) {
        _showCredsResult(svc, false, 'Request failed');
    } finally {
        if (btn) { btn.textContent = 'Test Connection'; btn.disabled = false; }
    }
}

function _showCredsResult(svc, ok, message) {
    const el = document.getElementById(`cred-${svc}-result`);
    if (!el) return;
    el.textContent = ok ? `✓ ${message}` : `✗ ${message}`;
    el.className   = `text-xs ml-1 ${ok ? 'text-green-400' : 'text-red-400'}`;
    el.classList.remove('hidden');
    setTimeout(() => el.classList.add('hidden'), 4000);
}

document.addEventListener('DOMContentLoaded', () => {
    const pane = document.getElementById('tab-credentials');
    if (!pane) return;
    new MutationObserver(() => {
        if (pane.classList.contains('active')) {
            const root = document.getElementById('credentials-root');
            if (root && !root.hasChildNodes()) initCredentials();
        }
    }).observe(pane, { attributes: true, attributeFilter: ['class'] });
});
