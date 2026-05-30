'use strict';

let _csrfToken = '';
let _userName = 'User';
let _proactiveQueue = [];
let _clockIntervalId = null;
let _healthIntervalId = null;
let _healthCheckPending = false;
let _currentStreamAbort = null;
let _proactiveSSE = null;
let _proactiveReconnectTimer = null;

let _godModeOpen       = false;
let _godModeInterval   = null;
let _godModePending    = false;
let _godModeRafId      = null;
let _godModeTaskId     = null;
let _godModeMetrics    = null;
let _godModeTraces     = null;

const _tabLoadedAt = {};
function _isStale(tabId, ttlMs = 30000) {
    return !_tabLoadedAt[tabId] || (Date.now() - _tabLoadedAt[tabId]) > ttlMs;
}
function _markLoaded(tabId) { _tabLoadedAt[tabId] = Date.now(); }

// Lazy-load Leaflet CSS + JS on first map usage
let _leafletLoaded = false;
async function _ensureLeaflet() {
    if (_leafletLoaded || window.L) { _leafletLoaded = true; return; }
    await Promise.all([
        new Promise(resolve => {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
            link.onload = resolve; link.onerror = resolve;
            document.head.appendChild(link);
        }),
        new Promise(resolve => {
            const s = document.createElement('script');
            s.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
            s.onload = resolve; s.onerror = resolve;
            document.body.appendChild(s);
        }),
    ]);
    _leafletLoaded = true;
}

// Lazy-load QRCode.js on first QR modal open
let _qrcodeLoaded = false;
async function _ensureQRCode() {
    if (_qrcodeLoaded || window.QRCode) { _qrcodeLoaded = true; return; }
    await new Promise(resolve => {
        const s = document.createElement('script');
        s.src = 'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';
        s.onload = resolve; s.onerror = resolve;
        document.body.appendChild(s);
    });
    _qrcodeLoaded = true;
}

// Persistent device UUID — identifies this browser across sessions.
// Admin registers this ID in the Users → Device Management panel to grant tier access.
const _deviceId = (() => {
    let id = localStorage.getItem('wade_device_id');
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem('wade_device_id', id);
    }
    return id;
})();

// Shared modal helpers (replaces native confirm/alert)
function _wadeConfirm(message, { title = 'Confirm', confirmLabel = 'Confirm', danger = true } = {}) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm';
        overlay.style.animation = 'fade-up 0.2s var(--ease-smooth) forwards';
        const btnCls = danger
            ? 'h-8 px-4 text-xs bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 hover:bg-red-500/20 transition-colors'
            : 'h-8 px-4 text-xs bg-violet-500/10 border border-violet-500/30 rounded-lg text-violet-300 hover:bg-violet-500/20 transition-colors';
        overlay.innerHTML = `
            <div class="bg-[#0d0d0d] border border-white/10 rounded-xl p-6 max-w-sm w-full mx-4 shadow-2xl" style="animation:fade-up 0.2s var(--ease-smooth) forwards">
                <h3 class="font-display font-bold text-white text-sm tracking-wide mb-3">${esc(title)}</h3>
                <p class="text-zinc-400 text-sm leading-relaxed mb-6">${esc(message).replace(/\n/g, '<br>')}</p>
                <div class="flex gap-3 justify-end">
                    <button id="_wm-cancel" class="h-8 px-4 text-xs border border-white/10 rounded-lg text-zinc-400 hover:border-white/20 transition-colors">Cancel</button>
                    <button id="_wm-confirm" class="${btnCls}">${esc(confirmLabel)}</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const close = r => { overlay.remove(); resolve(r); };
        overlay.querySelector('#_wm-cancel').onclick  = () => close(false);
        overlay.querySelector('#_wm-confirm').onclick = () => close(true);
        overlay.addEventListener('click', e => { if (e.target === overlay) close(false); });
    });
}

function _wadeAlert(message, { title = 'Notice' } = {}) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm';
        overlay.innerHTML = `
            <div class="bg-[#0d0d0d] border border-white/10 rounded-xl p-6 max-w-sm w-full mx-4 shadow-2xl" style="animation:fade-up 0.2s var(--ease-smooth) forwards">
                <h3 class="font-display font-bold text-white text-sm tracking-wide mb-3">${esc(title)}</h3>
                <p class="text-zinc-400 text-sm leading-relaxed mb-6">${esc(message)}</p>
                <div class="flex justify-end">
                    <button id="_wm-ok" class="h-8 px-4 text-xs border border-white/10 rounded-lg text-zinc-300 hover:border-white/20 transition-colors">OK</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const close = () => { overlay.remove(); resolve(); };
        overlay.querySelector('#_wm-ok').onclick = close;
        overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    });
}

// CSRF
async function _initCsrf() {
    try {
        const res = await fetch('/api/csrf-token');
        const data = await res.json();
        _csrfToken = data.token || '';
    } catch (e) {
        console.warn('[W.A.D.E.] Could not fetch CSRF token:', e);
    }
}

function _authHeaders(extra = {}) {
    return { 'Content-Type': 'application/json', 'X-WADE-Token': _csrfToken, 'X-Device-ID': _deviceId, ...extra };
}

function _escHtml(str) {
    return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

const STATE = {
    isSidebarExpanded: false,
    isContextOpen: false,
    isStreaming: false,
    skillsReady: false,
    messageCount: 0,
    healthStatus: 'unknown',
    activeTab: 'chat',
    memoryLoaded: false,
    diagLoaded: false,
};

const UI = {
    chatBox: null,
    input: null,
    welcomeScreen: null,
    exampleCards: null,
    sidebar: null,
    logoIcon: null,
    toggleBtn: null,
    streamingIndicator: null,
    workspaceContent: null,
    lineNumbers: null,
    workspacePane: null,
    contextDrawer: null,
    systemStatus: null,
    systemStatusText: null,
    chatHeader: null,
    userInitial: null,
    userNameLabel: null,
    userNameTooltip: null
};

document.addEventListener('DOMContentLoaded', async () => {
    if (window.location.hash === '#quickchat') {
        document.body.classList.add('quickchat-mode');
    }

    UI.chatBox = document.getElementById('chat-box');
    UI.input = document.getElementById('chat-input');
    UI.welcomeScreen = document.getElementById('welcome-screen');
    UI.exampleCards = document.getElementById('example-cards');
    UI.sidebar = document.getElementById('sidebar');
    UI.logoIcon = document.getElementById('logo-icon');
    UI.toggleBtn = document.getElementById('sidebar-toggle');
    UI.streamingIndicator = document.getElementById('streaming-indicator');
    UI.workspaceContent = document.getElementById('workspace-content');
    UI.lineNumbers = document.getElementById('line-numbers');
    UI.workspacePane = document.getElementById('workspace-pane');
    UI.contextDrawer = document.getElementById('context-drawer');
    UI.systemStatus = document.getElementById('system-status');
    UI.systemStatusText = document.getElementById('system-status-text');
    UI.chatHeader = document.getElementById('chat-header');
    UI.userInitial = document.getElementById('user-initial');
    UI.userNameLabel = document.getElementById('user-name-label');
    UI.userNameTooltip = document.getElementById('user-name-tooltip');
    UI.tabPanes = document.querySelectorAll('.tab-content');
    UI.navBtns  = document.querySelectorAll('[id^="nav-"]');

    await _initCsrf();
    _loadUserProfile();
    _loadVersionTag();
    _setupEventListeners();
    _setupWorkspaceObserver();
    _startHealthCheck();
    _startClock();
    requestIdleCallback ? requestIdleCallback(loadMemory) : setTimeout(loadMemory, 200);
    _startProactiveSSE();
    _bootSequence();
    _pollReadiness();
});

function _appendBootLine(text, statusColor = 'text-[#00ff66]', label = '[ OK ]') {
    const logContainer = document.getElementById('boot-log');
    if (!logContainer || !UI.welcomeScreen || UI.welcomeScreen.classList.contains('hidden')) return;
    const line = document.createElement('p');
    line.className = 'font-mono-custom text-[10px] text-zinc-500 tracking-widest leading-relaxed anim-fade-up';
    line.innerHTML = `<span class="${statusColor}">${_escHtml(label)}</span>  ${_escHtml(text)}`;
    logContainer.appendChild(line);
    logContainer.scrollTop = logContainer.scrollHeight;
}

function _onSkillsReady() {
    STATE.skillsReady = true;
    if (UI.input) {
        UI.input.disabled = false;
        UI.input.placeholder = 'Message W.A.D.E.';
    }
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    }
    _appendBootLine('SKILLS LOADED');
    _appendBootLine('CHROMADB VECTOR STORE  //  MAPPED');
    _appendBootLine('ALL SYSTEMS NOMINAL');
}

function _onSkillsError(errMsg) {
    STATE.skillsReady = true;
    if (UI.input) {
        UI.input.disabled = false;
        UI.input.placeholder = 'Message W.A.D.E.';
    }
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    }
    _appendBootLine('SKILLS PRELOAD FAILED  //  DEGRADED', 'text-yellow-400', '[WARN]');
    _appendBootLine(errMsg, 'text-yellow-400', '[WARN]');
    _appendBootLine('ALL SYSTEMS DEGRADED  //  LAZY FALLBACK ACTIVE', 'text-yellow-400', '[WARN]');
}

function _pollReadiness() {
    function poll() {
        fetch('/api/ready')
            .then(r => r.json())
            .then(data => {
                if (data.ready) {
                    _onSkillsReady();
                } else if (data.error) {
                    _onSkillsError(data.error);
                } else {
                    setTimeout(poll, 500);
                }
            })
            .catch(() => {
                setTimeout(poll, 500);
            });
    }
    poll();
}

function _bootSequence() {
    if (UI.input) {
        UI.input.disabled = true;
        UI.input.placeholder = 'W.A.D.E. warming up…';
    }
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }

    const staticLines = [
        { text: 'MEMORY ONLINE',                delay: 100 },
        { text: 'INFERENCE ENGINE  //  READY',  delay: 750 },
        { text: 'UPLINK SECURE',                delay: 1350 },
    ];

    const logContainer = document.getElementById('boot-log');
    if (!logContainer) return;

    staticLines.forEach(({ text, delay }) => {
        setTimeout(() => {
            if (!logContainer || !UI.welcomeScreen || UI.welcomeScreen.classList.contains('hidden')) return;
            const line = document.createElement('p');
            line.className = 'font-mono-custom text-[10px] text-zinc-500 tracking-widest leading-relaxed anim-fade-up';
            line.innerHTML = `<span class="text-[#00ff66]">[ OK ]</span>  ${_escHtml(text)}`;
            logContainer.appendChild(line);
            logContainer.scrollTop = logContainer.scrollHeight;
        }, delay);
    });
}

function _startProactiveSSE() {
    function connect() {
        if (_proactiveSSE) { _proactiveSSE.close(); _proactiveSSE = null; }
        if (document.hidden) return;
        _proactiveSSE = new EventSource('/api/events');
        _proactiveSSE.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'proactive_message' && data.content) {
                    _renderProactiveMessage(data.content);
                }
            } catch (err) { console.warn('[W.A.D.E.] SSE parse/render error:', err); }
        };
        _proactiveSSE.onerror = () => {
            if (_proactiveSSE) { _proactiveSSE.close(); _proactiveSSE = null; }
            if (!document.hidden) {
                clearTimeout(_proactiveReconnectTimer);
                _proactiveReconnectTimer = setTimeout(connect, 5000);
            }
        };
    }

    setTimeout(connect, 3000);

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            clearTimeout(_proactiveReconnectTimer);
            if (_proactiveSSE) { _proactiveSSE.close(); _proactiveSSE = null; }
        } else {
            connect();
        }
    });
}

function _renderProactiveMessage(text) {
    if (STATE.isStreaming) {
        if (_proactiveQueue.length < 20) _proactiveQueue.push(text);
        return;
    }
    _transitionToChatUI();
    appendMessageToChat(text, false, false, { proactive: true });
}

window.addEventListener('wade:chat:end', async () => {
    while (_proactiveQueue.length > 0) {
        const text = _proactiveQueue.shift();
        _renderProactiveMessage(text);
    }
    if (_godModeOpen) {
        try {
            const r = await fetch('/api/tasks?limit=1', { headers: _authHeaders() });
            if (r.ok) {
                const d = await r.json();
                const tasks = d.tasks || [];
                if (tasks.length > 0) {
                    _godModeTaskId = tasks[0].id;
                    _fetchGodModeData();
                }
            }
        } catch (_) {}
    }
});

function _setupEventListeners() {
    if (UI.input) {
        UI.input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        UI.input.addEventListener('input', () => {
            UI.input.style.height = 'auto';
            UI.input.style.height = Math.min(UI.input.scrollHeight, 160) + 'px';
        });
    }

    document.addEventListener('click', (e) => {
        if (
            STATE.isContextOpen &&
            UI.contextDrawer &&
            !UI.contextDrawer.contains(e.target) &&
            !e.target.closest('[onclick="toggleContext()"]')
        ) {
            toggleContext();
        }
    });
}

function _startClock() {
    function tick() {
        const el = document.getElementById('sys-time');
        if (el) {
            const now  = new Date();
            const hh   = String(now.getHours()).padStart(2, '0');
            const mm   = String(now.getMinutes()).padStart(2, '0');
            const ss   = String(now.getSeconds()).padStart(2, '0');
            el.textContent = `${hh}:${mm}:${ss}`;
        }
    }
    tick();
    _clockIntervalId = setInterval(tick, 1000);
}

function _startHealthCheck() {
    async function check() {
        if (_healthCheckPending) return;
        _healthCheckPending = true;
        try {
            const res = await fetch('/health', { method: 'GET', cache: 'no-store' });
            _setHealthStatus(res.ok ? 'online' : 'offline');
        } catch {
            _setHealthStatus('offline');
        } finally {
            _healthCheckPending = false;
        }
    }
    check();
    _healthIntervalId = setInterval(check, 15000);

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            clearInterval(_clockIntervalId);
            clearInterval(_healthIntervalId);
            _clockIntervalId = null;
            _healthIntervalId = null;
        } else {
            if (!_clockIntervalId) _startClock();
            if (!_healthIntervalId) {
                check();
                _healthIntervalId = setInterval(check, 15000);
            }
        }
    });
}

function _setHealthStatus(status) {
    if (STATE.healthStatus === status) return;
    STATE.healthStatus = status;

    const dot   = document.querySelector('.top-bar .status-pulse');
    const label = document.getElementById('status-label');

    if (status === 'offline') {
        if (dot)   { dot.classList.remove('bg-zinc-300'); dot.classList.add('bg-zinc-700'); }
        if (label) label.textContent = 'UPLINK_LOST';
        _pushSystemNotice('Gateway uplink lost — attempting reconnect…');
    } else if (status === 'online') {
        if (dot)   { dot.classList.add('bg-zinc-300'); dot.classList.remove('bg-zinc-700'); }
        if (label) label.textContent = 'UPLINK_SECURE';
    }
}

function _setupWorkspaceObserver() {
    if (!UI.workspaceContent || !UI.lineNumbers) return;
    let _lastLineCount = 0;
    let _lineRafId = null;
    function updateLineNumbers() {
        _lineRafId = null;
        const lines = (UI.workspaceContent.textContent || '').split('\n');
        const count = Math.max(lines.length, 1);
        if (count === _lastLineCount) return;
        _lastLineCount = count;
        UI.lineNumbers.innerHTML = Array.from(
            { length: count },
            (_, i) => `<span>${i + 1}</span>`
        ).join('');
    }
    const observer = new MutationObserver(() => {
        if (_lineRafId === null) _lineRafId = requestAnimationFrame(updateLineNumbers);
    });
    observer.observe(UI.workspaceContent, { childList: true, characterData: true, subtree: true });
    updateLineNumbers();
}

function _setStreaming(active) {
    STATE.isStreaming = active;
    if (!UI.streamingIndicator) return;
    if (active) {
        UI.streamingIndicator.classList.remove('hidden');
        UI.streamingIndicator.classList.add('flex');
    } else {
        UI.streamingIndicator.classList.add('hidden');
        UI.streamingIndicator.classList.remove('flex');
    }
}

function showStatus(elementId, message, type = 'success', duration = 3000) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const isSuccess = type === 'success';
    el.textContent  = message;
    el.className    = `font-mono-custom text-[11px] tracking-wider ${isSuccess ? 'text-zinc-300' : 'text-zinc-500'}`;
    el.classList.remove('hidden');
    setTimeout(() => el.classList.add('hidden'), duration);
}

function _pushSystemNotice(message) {
    if (!UI.chatBox) return;
    const el = document.createElement('div');
    el.className = 'flex justify-center mb-6 w-full anim-fade-up';
    el.innerHTML = `<span class="font-mono-custom text-[10px] tracking-wider text-zinc-500 border border-white/10 bg-white/[0.02] px-4 py-2 rounded-full">${_escHtml(message)}</span>`;
    UI.chatBox.appendChild(el);
    UI.chatBox.scrollTo({ top: UI.chatBox.scrollHeight, behavior: 'smooth' });
}

function switchTab(tabId) {
    if (window.innerWidth < 768) {
        document.getElementById('sidebar').classList.add('-translate-x-full');
    }

    UI.tabPanes.forEach(t => t.classList.remove('active'));
    UI.navBtns.forEach(btn => {
        btn.classList.remove('nav-btn-active', 'text-zinc-100');
        btn.classList.add('text-zinc-500');
    });

    const pane = document.getElementById(`tab-${tabId}`);
    if (pane) pane.classList.add('active');

    const nav = document.getElementById(`nav-${tabId}`);
    if (nav) {
        nav.classList.add('nav-btn-active');
        nav.classList.remove('text-zinc-500');
    }

    STATE.activeTab = tabId;
    if (UI.chatHeader) UI.chatHeader.classList.toggle('hidden', tabId !== 'chat');
    if (tabId === 'memory'   && _isStale('memory', 15000))   { loadMemoryTab();   _markLoaded('memory'); }
    if (tabId === 'monitors' && _isStale('monitors', 10000))  { loadMonitors();    _markLoaded('monitors'); }
    if (tabId === 'settings' && _isStale('settings', 30000))  { loadSettings(); loadModelRouting(); loadSkillPermissions(); loadProjects(); loadWorkspaceFile(); _markLoaded('settings'); }
    if (tabId === 'users'    && _isStale('users', 30000))     { loadUsers(); loadDevices(); _markLoaded('users'); }
    if (tabId === 'credentials' && _isStale('credentials', 30000)) { initCredentials(); _markLoaded('credentials'); }

    if (tabId === 'tasks') {
        loadTasks();
        if (!_tasksInterval) _tasksInterval = setInterval(loadTasks, 3000);
    } else {
        clearInterval(_tasksInterval);
        _tasksInterval = null;
    }

    if (tabId === 'security') {
        loadSecurity();
        if (!_securityInterval) _securityInterval = setInterval(loadSecurity, 60000);
    } else {
        clearInterval(_securityInterval);
        _securityInterval = null;
    }

    if (tabId === 'recon') {
        loadRecon();
        if (!_reconInterval) _reconInterval = setInterval(loadRecon, 300000);
        setTimeout(() => { if (_reconMap) _reconMap.invalidateSize(); }, 100);
    } else {
        clearInterval(_reconInterval);
        _reconInterval = null;
    }

    if (tabId === 'aero') {
        loadAero();
        if (!_aeroInterval) _aeroInterval = setInterval(loadAero, 30000);
        setTimeout(() => { if (_aeroMap) _aeroMap.invalidateSize(); }, 100);
    } else {
        clearInterval(_aeroInterval);
        _aeroInterval = null;
    }
}

function toggleSidebar() {
    if (!UI.sidebar || !UI.logoIcon || !UI.toggleBtn) return;
    if (window.innerWidth < 768) return; 

    STATE.isSidebarExpanded = !STATE.isSidebarExpanded;
    const expanded = STATE.isSidebarExpanded;
    const navTexts = document.querySelectorAll('.nav-text');

    if (expanded) {
        UI.sidebar.classList.replace('md:w-16', 'md:w-60');
        UI.logoIcon.classList.remove('opacity-0', 'scale-50');
        UI.logoIcon.classList.add('opacity-100', 'scale-100');

        navTexts.forEach(text => {
            text.classList.remove('hidden');
            requestAnimationFrame(() => {
                setTimeout(() => text.classList.replace('opacity-0', 'opacity-100'), 80);
            });
        });
    } else {
        UI.sidebar.classList.replace('md:w-60', 'md:w-16');
        UI.logoIcon.classList.add('opacity-0', 'scale-50');
        UI.logoIcon.classList.remove('opacity-100', 'scale-100');

        navTexts.forEach(text => {
            text.classList.replace('opacity-100', 'opacity-0');
            setTimeout(() => text.classList.add('hidden'), 200);
        });
    }
}

function openMobileSidebar() {
    document.getElementById('sidebar').classList.remove('-translate-x-full');
    document.getElementById('sidebar-overlay').classList.remove('hidden');
}

function closeMobileSidebar() {
    document.getElementById('sidebar').classList.add('-translate-x-full');
    document.getElementById('sidebar-overlay').classList.add('hidden');
}

function toggleContext() {
    STATE.isContextOpen = !STATE.isContextOpen;
    if (!UI.contextDrawer) return;

    if (STATE.isContextOpen) {
        UI.contextDrawer.classList.remove('-translate-x-full');
    } else {
        UI.contextDrawer.classList.add('-translate-x-full');
    }
}

function openWorkspace() {
    document.body.classList.add('workspace-active');
    if (UI.workspaceContent) UI.workspaceContent.textContent = '';
    if (UI.lineNumbers)      UI.lineNumbers.innerHTML = '<span>1</span>';
}

function closeWorkspace() {
    document.body.classList.remove('workspace-active');
}

function usePrompt(text) {
    if (!UI.input) return;
    UI.input.value = text;
    UI.input.focus();
    UI.input.style.height = 'auto';
    UI.input.style.height = Math.min(UI.input.scrollHeight, 160) + 'px';
}

function _transitionToChatUI() {
    if (UI.welcomeScreen) UI.welcomeScreen.classList.add('hidden');
    if (UI.exampleCards)  UI.exampleCards.classList.add('hidden');
    if (UI.chatBox) {
        UI.chatBox.classList.remove('max-w-4xl');
        UI.chatBox.classList.add('w-full', 'px-6', 'sm:px-12');
    }
    const wrapper = _getInputWrapper();
    if (wrapper) {
        wrapper.classList.remove('max-w-3xl');
        wrapper.classList.add('w-full', 'max-w-none', 'px-6', 'sm:px-12');
    }
}

function _resetChatUI() {
    if (UI.welcomeScreen) UI.welcomeScreen.classList.remove('hidden');
    if (UI.exampleCards)  UI.exampleCards.classList.remove('hidden');
    if (UI.chatBox) {
        UI.chatBox.classList.remove('w-full', 'px-6', 'sm:px-12');
        UI.chatBox.classList.add('max-w-4xl');
        Array.from(UI.chatBox.children).forEach(child => {
            if (child.id !== 'welcome-screen') child.remove();
        });
    }
    const wrapper = _getInputWrapper();
    if (wrapper) {
        wrapper.classList.remove('w-full', 'max-w-none', 'px-6', 'sm:px-12');
        wrapper.classList.add('max-w-3xl');
    }
    STATE.messageCount = 0;
}

function _getInputWrapper() {
    if (!UI.input) return null;
    return (
        UI.input.closest('.max-w-3xl') ||
        UI.input.closest('.max-w-none') ||
        UI.input.closest('.w-full')
    );
}

function _makeWadeAvatar() {
    const el = document.createElement('div');
    el.className = 'w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-1 font-display font-extrabold text-[9px] text-white mr-2';
    el.style.background = 'var(--chrome-grad, linear-gradient(135deg,#1a1a1a,#333))';
    el.textContent = 'W';
    return el;
}

function _makeUserAvatar() {
    const el = document.createElement('div');
    el.className = 'w-6 h-6 rounded-full bg-white/5 border border-white/10 flex items-center justify-center shrink-0 mt-1 font-display font-bold text-[9px] text-zinc-400 ml-2';
    el.textContent = (_userName || 'U')[0].toUpperCase();
    return el;
}

function _renderMarkdown(text) {
    text = text.replace(/\n*<tool_result name='[^']*'>[\s\S]*?<\/tool_result>\n*/g, '');
    text = text.replace(/\n*<tool_exec[^>]*\/>\n*/g, '');
    text = text.replace(/\n*<wade_status[^>]*\/>\n*/g, '');
    text = text.replace(/\n*<loop_detected[^>]*\/>\n*/g, '');
    text = text.replace(/\n*<critic_note>[\s\S]*?<\/critic_note>\n*/g, '');
    text = text.replace(/\n*<critic_blocked>[\s\S]*?<\/critic_blocked>\n*/g, '');
    text = text.replace(/<shell_stdout>([\s\S]*?)<\/shell_stdout>/g, '$1');
    text = text.replace(/<shell_stderr>([\s\S]*?)<\/shell_stderr>/g, '$1');

    const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const inline = s => {
        s = esc(s);
        s = s.replace(/`([^`]+)`/g, '<code class="md-code-inline">$1</code>');
        s = s.replace(/\*\*([^*]+)\*\*/g, '<strong class="md-bold">$1</strong>');
        s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        return s;
    };

    text = text.replace(/```([a-z0-9#+.-]*)\n?([\s\S]*?)```/gi, (_, lang, code) => {
        const lbl = lang ? lang.trim().toUpperCase() : 'CODE';
        return `<div class="md-pre"><div class="md-pre-header"><span class="md-pre-lang">${esc(lbl)}</span><button class="md-copy-btn" onclick="copyCode(this)">copy</button></div><pre><code>${esc(code.trimEnd())}</code></pre></div>`;
    });

    const lines = text.split('\n');
    let html = '';
    let inList = false;
    let listType = null; // 'ul' or 'ol'

    function closeList() {
        if (inList) {
            html += `</${listType}>`;
            inList = false;
            listType = null;
        }
    }

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        
        // H3
        if (/^### /.test(line)) {
            closeList();
            html += line.replace(/^### (.+)$/, (_, h) => `<h3 class="md-h3">${esc(h)}</h3>`);
            continue;
        }

        // Bullet list
        const ulMatch = line.match(/^[ \t]*- (.*)$/);
        if (ulMatch) {
            if (!inList || listType !== 'ul') {
                closeList();
                html += '<ul class="md-ul">';
                inList = true;
                listType = 'ul';
            }
            html += `<li>${inline(ulMatch[1])}</li>`;
            continue;
        }

        // Numbered list
        const olMatch = line.match(/^[ \t]*\d+\. (.*)$/);
        if (olMatch) {
            if (!inList || listType !== 'ol') {
                closeList();
                html += '<ol class="md-ul">';
                inList = true;
                listType = 'ol';
            }
            html += `<li>${inline(olMatch[1])}</li>`;
            continue;
        }

        if (line.trim() === '') {
            closeList();
            html += '<div class="md-spacer"></div>';
            continue;
        }

        if (line.startsWith('<div class="md-pre"')) {
            closeList();
            html += line;
            continue;
        }

        // Default to paragraph line
        closeList();
        html += `<p class="md-p">${inline(line)}</p>`;
    }
    
    closeList();
    return html;
}

function _renderMarkdownStreaming(text) {
    const fences = (text.match(/```/g) || []).length;
    if (fences % 2 === 1) text = text + '\n```';
    return _renderMarkdown(text);
}

function copyCode(btn) {
    const code = btn.closest('.md-pre').querySelector('code');
    if (!code) return;
    navigator.clipboard.writeText(code.textContent).then(() => {
        btn.textContent = 'copied';
        setTimeout(() => { btn.textContent = 'copy'; }, 1500);
    });
}

// HITL Approval Modal
let _hitlPendingUUID = null;

function _showApprovalModal(toolName, uuid, argsJson) {
    _hitlPendingUUID = uuid;

    const modal      = document.getElementById('hitl-modal');
    const toolEl     = document.getElementById('hitl-tool-name');
    const argsEl     = document.getElementById('hitl-args');
    const statusEl   = document.getElementById('hitl-status');
    const actionsEl  = document.getElementById('hitl-actions');

    if (!modal) return;

    toolEl.textContent = toolName;

    try {
        argsEl.textContent = JSON.stringify(JSON.parse(argsJson), null, 2);
    } catch {
        argsEl.textContent = argsJson || '{}';
    }

    statusEl.className = 'hidden font-mono-custom text-[12px] text-center py-2 rounded-sm';
    actionsEl.classList.remove('hidden');
    modal.classList.remove('hidden');
}

async function _hitlDecide(approved) {
    if (!_hitlPendingUUID) return;

    const uuid      = _hitlPendingUUID;
    const statusEl  = document.getElementById('hitl-status');
    const actionsEl = document.getElementById('hitl-actions');
    const authBtn   = document.getElementById('hitl-authorize-btn');
    const rejectBtn = document.getElementById('hitl-reject-btn');

    if (authBtn)   authBtn.disabled   = true;
    if (rejectBtn) rejectBtn.disabled = true;

    try {
        const res = await fetch(`/api/tasks/${uuid}/approve`, {
            method:  'POST',
            headers: _authHeaders(),
            body:    JSON.stringify({ approved }),
        });

        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try { const d = await res.json(); if (d.detail) detail = d.detail; } catch (_) {}
            throw new Error(detail);
        }

        actionsEl.classList.add('hidden');
        statusEl.textContent  = approved ? '✓ Authorization granted.' : '✗ Action rejected.';
        statusEl.className    = `font-mono-custom text-[12px] text-center py-2 rounded-sm ${
            approved ? 'text-[#00ff66] bg-[#00ff66]/5' : 'text-red-400 bg-red-500/5'
        }`;

        setTimeout(() => {
            document.getElementById('hitl-modal')?.classList.add('hidden');
            _hitlPendingUUID = null;
        }, 1800);

    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
        statusEl.className   = 'font-mono-custom text-[12px] text-center py-2 rounded-sm text-red-400';
        if (authBtn)   authBtn.disabled   = false;
        if (rejectBtn) rejectBtn.disabled = false;
    }
}

function appendMessageToChat(text, isUser = false, isError = false, options = {}) {
    if (!UI.chatBox) return null;
    STATE.messageCount++;

    const wrapper = document.createElement('div');
    wrapper.className = `flex ${isUser ? 'justify-end' : 'justify-start'} mb-8 w-full anim-fade-up`;

    const bubble = document.createElement('div');

    if (isError) {
        bubble.className = 'bubble-error p-5 inline-block max-w-[95%] sm:max-w-[80%] font-mono-custom text-[13px] text-zinc-400 tracking-wide leading-relaxed';
    } else {
        bubble.className = 'bubble p-5 inline-block max-w-[95%] sm:max-w-[75%] font-sans text-[14px] text-white leading-relaxed font-light';
    }

    if (options && options.proactive) {
        bubble.dataset.proactive = 'true';
    }

    const label = document.createElement('div');
    label.className = `font-display text-[9px] font-bold tracking-[0.1em] mb-3 uppercase ${isUser ? 'text-zinc-500 text-right' : 'text-zinc-600'}`;
    label.textContent = isUser ? _userName : isError ? 'System Notice' : 'W.A.D.E.';
    bubble.appendChild(label);

    const content = document.createElement('div');
    if (!isUser && !isError) {
        content.innerHTML = _renderMarkdown(text);
    } else {
        content.textContent = text;
    }
    bubble.appendChild(content);

    if (isUser) {
        wrapper.appendChild(bubble);
        wrapper.appendChild(_makeUserAvatar());
    } else {
        wrapper.appendChild(_makeWadeAvatar());
        wrapper.appendChild(bubble);
    }
    UI.chatBox.appendChild(wrapper);
    UI.chatBox.scrollTo({ top: UI.chatBox.scrollHeight, behavior: 'smooth' });

    return { wrapper, bubble, content };
}

function _createProcessingBubble() {
    if (!UI.chatBox) return null;

    const wrapper = document.createElement('div');
    wrapper.className = 'flex justify-start mb-8 w-full anim-fade-up';

    const bubble = document.createElement('div');
    bubble.className = 'bubble p-5 inline-block max-w-[95%] sm:max-w-[80%]';

    const label = document.createElement('div');
    label.className = 'font-display text-[9px] font-bold tracking-[0.1em] mb-3 uppercase text-zinc-600';
    label.textContent = 'W.A.D.E.';
    bubble.appendChild(label);

    const dots = document.createElement('div');
    dots.className = 'flex items-center gap-3';
    dots.innerHTML = `
        <span class="flex h-2 w-2 relative">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75"></span>
          <span class="relative inline-flex rounded-full h-2 w-2 bg-white"></span>
        </span>
        <span class="font-mono-custom text-[11px] text-zinc-500 tracking-wider">Compiling response...</span>
    `;
    bubble.appendChild(dots);

    wrapper.appendChild(_makeWadeAvatar());
    wrapper.appendChild(bubble);
    UI.chatBox.appendChild(wrapper);
    UI.chatBox.scrollTo({ top: UI.chatBox.scrollHeight, behavior: 'smooth' });

    return { wrapper, bubble, dots };
}

async function sendMessage() {
    if (!UI.input || STATE.isStreaming || !STATE.skillsReady) return;

    const text = UI.input.value.trim();
    if (!text) return;

    _transitionToChatUI();
    UI.input.value = '';
    UI.input.style.height = 'auto';

    appendMessageToChat(text, true);

    if (_currentStreamAbort) _currentStreamAbort.abort();
    _currentStreamAbort = new AbortController();

    let contentEl     = null;
    let reader        = null;
    let displayStream = '';
    let fullStream    = '';
    const processingBubble = _createProcessingBubble();
    _setStreaming(true);
    window.dispatchEvent(new CustomEvent('wade:chat:start'));

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify({ prompt: text }),
            signal: _currentStreamAbort.signal,
        });

        if (!response.ok) throw new Error('Gateway error');

        reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');

        if (processingBubble) processingBubble.wrapper.remove();

        const wrapper = document.createElement('div');
        wrapper.className = 'flex justify-start mb-8 w-full anim-fade-up';

        const bubble = document.createElement('div');
        bubble.className = 'bubble p-5 inline-block max-w-[95%] sm:max-w-[75%] font-sans text-[14px] text-white leading-relaxed font-light';

        const label = document.createElement('div');
        label.className = 'font-display text-[9px] font-bold tracking-[0.1em] mb-3 uppercase text-zinc-600';
        label.textContent = 'W.A.D.E.';
        bubble.appendChild(label);

        contentEl = document.createElement('div');
        bubble.appendChild(contentEl);
        wrapper.appendChild(_makeWadeAvatar());
        wrapper.appendChild(bubble);
        UI.chatBox.appendChild(wrapper);

        let _scrollRafPending = false;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            fullStream += chunk;

            let cleanChunk = chunk;
            cleanChunk = cleanChunk.replace(/\n\n⚙️ \[Task: .*?\]\n\n/g, '');
            cleanChunk = cleanChunk.replace(/\n\n🧠 \[Thinking: .*?\]\n\n/g, '');
            cleanChunk = cleanChunk.replace(/\n\n✅ \[Result: .*?\]\n\n/g, '');
            cleanChunk = cleanChunk.replace(/\n\n⚠️ \[Loop Detected: .*?\]\n\n/g, '');
            cleanChunk = cleanChunk.replace(/\n\n<tool_exec name='.*?' \/>/g, '');
            cleanChunk = cleanChunk.replace(/\n\n<loop_detected name='.*?' \/>/g, '');
            cleanChunk = cleanChunk.replace(/\n\n<wade_status[^>]*\/>\n\n/g, '');
            cleanChunk = cleanChunk.replace(/\n\n\[Task failed: .*?\]/g, '');
            displayStream += cleanChunk;
            if (displayStream.includes('</tool_result>')) {
                displayStream = displayStream.replace(/\n*<tool_result name='[^']*'>[\s\S]*?<\/tool_result>\n*/g, '');
            }
            if (displayStream.includes('</critic_note>')) {
                displayStream = displayStream.replace(/\n*<critic_note>[\s\S]*?<\/critic_note>\n*/g, '');
            }
            if (displayStream.includes('</critic_blocked>')) {
                displayStream = displayStream.replace(/\n*<critic_blocked>[\s\S]*?<\/critic_blocked>\n*/g, '');
            }
            if (displayStream.includes('</wade_approval_required>')) {
                const _hitlMatch = displayStream.match(/<wade_approval_required tool='([^']+)' uuid='([^']+)'>([\s\S]*?)<\/wade_approval_required>/);
                if (_hitlMatch) {
                    _showApprovalModal(_hitlMatch[1], _hitlMatch[2], _hitlMatch[3].trim());
                    displayStream = displayStream.replace(/<wade_approval_required[\s\S]*?<\/wade_approval_required>/g, '');
                }
            }

            contentEl.innerHTML = _renderMarkdownStreaming(displayStream);
            if (displayStream.trim() && UI.systemStatus && !UI.systemStatus.classList.contains('hidden')) {
                UI.systemStatus.classList.add('hidden');
                UI.systemStatus.classList.remove('flex');
            }
            let cur = contentEl.querySelector('.stream-cursor');
            if (!cur) {
                cur = document.createElement('span');
                cur.className = 'stream-cursor';
                contentEl.appendChild(cur);
            } else {
                contentEl.appendChild(cur);
            }

            const taskMatch = chunk.match(/\n\n⚙️ \[Task: (.*?)\]\n\n/);
            if (taskMatch && UI.systemStatus && UI.systemStatusText) {
                UI.systemStatusText.textContent = taskMatch[1];
                UI.systemStatus.classList.remove('hidden');
                UI.systemStatus.classList.add('flex');
            }
            const thoughtMatch = chunk.match(/\n\n🧠 \[Thinking: (.*?)\]\n\n/);
            if (thoughtMatch && UI.systemStatus && UI.systemStatusText) {
                UI.systemStatusText.textContent = 'Reasoning...';
                UI.systemStatus.classList.remove('hidden');
                UI.systemStatus.classList.add('flex');
            }
            if (chunk.includes("<wade_status type='planning'") && UI.systemStatus && UI.systemStatusText) {
                UI.systemStatusText.textContent = 'Planning...';
                UI.systemStatus.classList.remove('hidden');
                UI.systemStatus.classList.add('flex');
            }

            if (!_scrollRafPending) {
                _scrollRafPending = true;
                requestAnimationFrame(() => {
                    UI.chatBox.scrollTo({ top: UI.chatBox.scrollHeight, behavior: 'smooth' });
                    _scrollRafPending = false;
                });
            }
        }

    } catch (error) {
        if (error.name !== 'AbortError') {
            console.error('[W.A.D.E.] Stream error:', error);
            if (processingBubble) processingBubble.wrapper.remove();
            appendMessageToChat('Notice: Connection interrupted or gateway failed.', false, true);
        }
    } finally {
        if (reader) { try { await reader.cancel(); } catch {} }
        _currentStreamAbort = null;
        _setStreaming(false);
        if (UI.systemStatus) {
            UI.systemStatus.classList.add('hidden');
            UI.systemStatus.classList.remove('flex');
        }
        if (contentEl) {
            const cur = contentEl.querySelector('.stream-cursor');
            if (cur) cur.remove();
            let finalText = displayStream
                .replace(/\n*<tool_result name='[^']*'>[\s\S]*?<\/tool_result>\n*/g, '')
                .replace(/<wade_approval_required[\s\S]*?<\/wade_approval_required>/g, '');

            const _criticNotes = [...fullStream.matchAll(/<critic_note>([\s\S]*?)<\/critic_note>/g)].map(m => m[1].trim());
            finalText = finalText
                .replace(/\n*<critic_note>[\s\S]*?<\/critic_note>\n*/g, '')
                .replace(/\n*<critic_blocked>[\s\S]*?<\/critic_blocked>\n*/g, '')
                .trimStart();

            contentEl.innerHTML = finalText ? _renderMarkdown(finalText) : '';

            _criticNotes.forEach(note => {
                if (!note) return;
                const noteEl = document.createElement('div');
                noteEl.className = 'critic-note';
                noteEl.textContent = note;
                contentEl.appendChild(noteEl);
            });
        }
        window.dispatchEvent(new CustomEvent('wade:chat:end'));
    }
}

async function loadMemory() {
    try {
        const res = await fetch('/api/memory');
        if (!res.ok) return;

        const data = await res.json();
        if (data.status !== 'success' || !data.history) return;

        const allBlocks = data.history.split('\n\n---\n\n');
        const blocks = allBlocks.slice(-50);
        let hasMessages = false;

        blocks.forEach(block => {
            const trimmed = block.trim();
            if (!trimmed || trimmed.includes('*No previous conversation history')) return;

            hasMessages = true;
            const lower   = trimmed.toLowerCase();
            const isUser  = lower.startsWith('### user');
            const isError = lower.startsWith('### system');
            let text = trimmed.replace(/^###\s*(User|Wade|W\.A\.D\.E\.|System|Assistant)\n/i, '').trim();
            text = text.replace(/\n*<tool_result name='[^']*'>[\s\S]*?<\/tool_result>\n*/g, '');
            text = text.replace(/\n*<tool_exec[^>]*\/>\n*/g, '');
            text = text.replace(/\n*<wade_status[^>]*\/>\n*/g, '');
            text = text.replace(/\n*<loop_detected[^>]*\/>\n*/g, '');
            text = text.replace(/\n*<critic_note>[\s\S]*?<\/critic_note>\n*/g, '');
            text = text.replace(/\n*<critic_blocked>[\s\S]*?<\/critic_blocked>\n*/g, '').trim();

            if (text) appendMessageToChat(text, isUser, isError);
        });

        if (hasMessages) _transitionToChatUI();
    } catch (err) {}
}

async function clearMemory() {
    if (!await _wadeConfirm('Session memory will be permanently deleted. This cannot be undone.', { title: 'Confirm archival purge' })) return;

    try {
        const res  = await fetch('/api/memory', { method: 'DELETE', headers: _authHeaders() });
        const data = await res.json();

        if (data.status === 'success') {
            _resetChatUI();
            _pushSystemNotice('Session memory successfully cleared.');
            closeWorkspace();

            const mEntries = document.getElementById('memory-entries');
            const mStats   = document.getElementById('memory-stats');
            if (mEntries) mEntries.innerHTML = `<div class="font-sans text-[13px] text-zinc-600 text-center py-12">Archives are currently empty.</div>`;
            if (mStats)   mStats.textContent = '0 facts';

            if (STATE.activeTab === 'memory') loadMemoryTab();
        } else {
            throw new Error(data.message);
        }
    } catch (err) {
        _pushSystemNotice('Purge failed. Please try again.');
    }
}

function loadMemoryTab() {
    STATE.memoryLoaded = true;
    _loadMemoryFacts();
    _loadConversationTimeline();
}

function _loadMemoryFacts() {
    const factsEl = document.getElementById('memory-facts');
    const statsEl = document.getElementById('memory-stats');
    if (!factsEl) return;
    fetch('/api/memory/facts?limit=100', { headers: { 'X-WADE-Token': _csrfToken, 'X-Device-ID': _deviceId } })
        .then(r => r.json())
        .then(data => {
            const facts = data.facts || [];
            if (statsEl) statsEl.textContent = `${facts.length} fact${facts.length !== 1 ? 's' : ''}`;
            if (!facts.length) {
                factsEl.innerHTML = '<div class="font-sans text-[13px] text-zinc-600 py-4">No facts extracted yet.</div>';
                return;
            }
            factsEl.innerHTML = facts.map(f => {
                const val = Array.isArray(f.fact) ? f.fact.join(', ') : (f.fact ?? '');
                const ts  = f.timestamp ?? '';
                return `<div class="module-card px-4 py-2.5 flex items-center gap-3 group" data-fact-id="${_escHtml(f.id)}">
                    <span class="font-mono-custom text-[10px] text-zinc-500 shrink-0 w-32 truncate" title="${_escHtml(f.topic)}">${_escHtml(f.topic)}</span>
                    <span class="flex-1 font-sans text-[13px] text-zinc-300 leading-relaxed cursor-text select-text fact-value"
                          ondblclick="_startFactEdit(this, '${_escHtml(f.id)}')"
                          title="Double-click to edit">${_escHtml(val)}</span>
                    <span class="font-mono-custom text-[10px] text-zinc-700 shrink-0 hidden sm:block">${_escHtml(ts.slice(0, 16))}</span>
                    <button onclick="_deleteFact('${_escHtml(f.id)}', this)" class="opacity-0 group-hover:opacity-100 transition-opacity text-zinc-600 hover:text-red-400 p-1 shrink-0" title="Delete fact">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                    </button>
                </div>`;
            }).join('');
        })
        .catch(() => {
            if (factsEl) factsEl.innerHTML = '<div class="font-sans text-[13px] text-zinc-600 py-4">Failed to load facts.</div>';
        });
}

function _loadConversationTimeline() {
    fetch('/api/episodes?episode_type=conversation&limit=100')
        .then(r => r.json())
        .then(data => {
            const el = document.getElementById('memory-entries');
            if (!el) return;
            const episodes = data.episodes || [];
            if (!episodes.length) {
                el.innerHTML = '<div class="font-sans text-[13px] text-zinc-600 text-center py-12">No conversation history yet.</div>';
                return;
            }
            el.innerHTML = episodes.map(ep => {
                const ts = new Date(ep.timestamp).toLocaleString();
                const text    = ep.content ?? '';
                const preview = text.length > 200 ? text.slice(0, 197) + '\u2026' : text;
                return `<div class="module-card p-4 flex items-start gap-4 group">
                    <div class="w-2 h-2 mt-1 rounded-full bg-zinc-600 shrink-0"></div>
                    <div class="flex-1 min-w-0">
                        <p class="font-sans text-[12px] text-zinc-400 leading-relaxed">${_escHtml(preview)}</p>
                        <span class="font-mono-custom text-[10px] text-zinc-700 mt-1 block">${ts}</span>
                    </div>
                    <button onclick="deleteEpisode('${_escHtml(ep.id)}')" class="opacity-0 group-hover:opacity-100 transition-opacity text-zinc-600 hover:text-red-400 p-1 shrink-0" title="Delete">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                    </button>
                </div>`;
            }).join('');
        })
        .catch(() => {
            const el = document.getElementById('memory-entries');
            if (el) el.innerHTML = '<div class="font-sans text-[13px] text-zinc-600 text-center py-12">Failed to load conversation history.</div>';
        });
}

let _memoryReloading = false;
function deleteEpisode(id) {
    if (_memoryReloading) return;
    _memoryReloading = true;
    fetch(`/api/episodes/${id}`, { method: 'DELETE', headers: _authHeaders() })
        .then(r => { if (r.ok) _loadConversationTimeline(); })
        .catch(() => {})
        .finally(() => { _memoryReloading = false; });
}

function _deleteFact(factId, btn) {
    const row = btn.closest('[data-fact-id]');
    fetch(`/api/memory/facts/${encodeURIComponent(factId)}`, { method: 'DELETE', headers: _authHeaders() })
        .then(r => {
            if (!r.ok) return;
            if (row) row.remove();
            const statsEl = document.getElementById('memory-stats');
            if (statsEl) {
                const n = Math.max(0, parseInt(statsEl.textContent) - 1);
                statsEl.textContent = `${n} fact${n !== 1 ? 's' : ''}`;
            }
        })
        .catch(() => {});
}

function _startFactEdit(el, factId) {
    if (el.querySelector('input')) return;
    const current = el.textContent.trim();
    const input = document.createElement('input');
    input.type = 'text';
    input.value = current;
    input.className = 'w-full bg-transparent border-b border-white/20 font-sans text-[13px] text-zinc-200 focus:outline-none focus:border-white/40 pb-0.5';

    let cancelled = false;

    const save = () => {
        if (cancelled) return;
        const newVal = input.value.trim();
        if (!newVal || newVal === current) { el.textContent = current; return; }
        fetch(`/api/memory/facts/${encodeURIComponent(factId)}`, {
            method: 'PUT',
            headers: _authHeaders(),
            body: JSON.stringify({ fact: newVal }),
        })
            .then(r => r.json())
            .then(d => { el.textContent = d.fact ?? newVal; })
            .catch(() => { el.textContent = current; });
    };

    input.addEventListener('blur', save);
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') { cancelled = true; el.textContent = current; }
    });

    el.textContent = '';
    el.appendChild(input);
    input.focus();
    input.select();
}

async function forgetContext() {
    const qEl = document.getElementById('forget-query');
    const statusEl = document.getElementById('forget-status');
    if (!qEl || !qEl.value.trim()) return;

    const query = qEl.value.trim();
    if (statusEl) { statusEl.textContent = 'Searching\u2026'; statusEl.classList.remove('hidden'); }

    try {
        const res = await fetch('/api/memory/forget', {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify({ query, top_k: 5 }),
        });
        const data = await res.json();
        const total = (data.chromadb_deleted || 0) + (data.episodes_deleted || 0);
        if (statusEl) {
            statusEl.textContent = total
                ? `Removed ${total} record${total !== 1 ? 's' : ''} (${data.chromadb_deleted} semantic + ${data.episodes_deleted} episodes).`
                : 'No matching records found.';
        }
        qEl.value = '';
        _loadMemoryFacts();
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Forget failed.';
    }
}

async function loadDiagnostics() {
    const dot        = document.getElementById('diag-status-dot');
    const statusText = document.getElementById('diag-status-text');
    const lastCheck  = document.getElementById('diag-last-checked');
    const now = new Date();
    const ts  = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;
    
    if (lastCheck) lastCheck.textContent = ts;

    try {
        const res = await fetch('/health', { cache: 'no-store' });
        if (res.ok) {
            if (dot)        { dot.className = 'w-2.5 h-2.5 rounded-full bg-zinc-300 status-pulse shrink-0'; }
            if (statusText) { statusText.textContent = 'Online & Secured'; }
        } else throw new Error();
    } catch {
        if (dot)        { dot.className = 'w-2.5 h-2.5 rounded-full bg-zinc-700 shrink-0'; }
        if (statusText) { statusText.textContent = 'Offline'; }
    }

    try {
        const res  = await fetch('/api/settings');
        if (!res.ok) return;
        const data = await res.json();
        const suite = (data.full_config || {}).active_suite || {};

        const modelsEl = document.getElementById('diag-models');
        if (modelsEl) {
            const roles = ['chat', 'coding', 'reasoning', 'embedding'];
            modelsEl.innerHTML = roles.map(role => {
                const m    = suite[role];
                const name = m?.filename ? m.filename.replace('.gguf','').substring(0, 26) : '\u2014';
                return `<div class="flex items-center justify-between py-1.5 border-b border-white/5 last:border-0">
                    <span class="font-display text-[10px] font-bold text-zinc-500 tracking-wider uppercase w-20 shrink-0">${role}</span>
                    <span class="font-mono-custom text-[11px] text-zinc-300 truncate">${name}</span>
                </div>`;
            }).join('');
        }
    } catch {}
}

async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        if (!res.ok) return;

        const data = await res.json();
        if (data.status !== 'success') return;

        const nameEl     = document.getElementById('setting-user-name');
        const providerEl = document.getElementById('setting-provider');
        const cpuEl      = document.getElementById('setting-cpu-threshold');
        const ramEl      = document.getElementById('setting-ram-threshold');
        const diskEl     = document.getElementById('setting-disk-threshold');

        if (nameEl)     nameEl.value = data.user_name || '';
        if (providerEl) providerEl.value = data.provider || 'ollama';

        if (data.monitors && data.monitors.system) {
            const sys = data.monitors.system;
            if (cpuEl)  cpuEl.value = sys.cpu_threshold || 85;
            if (ramEl)  ramEl.value = sys.ram_threshold || 90;
            if (diskEl) diskEl.value = sys.disk_threshold || 95;
        }

        const idx = data.indexer || {};
        const zones = idx.enabled_zones || ["core", "system", "projects"];
        const zoneCore = document.getElementById('indexer-zone-core');
        const zoneSystem = document.getElementById('indexer-zone-system');
        const zoneProjects = document.getElementById('indexer-zone-projects');

        if (zoneCore) zoneCore.checked = zones.includes('core');
        if (zoneSystem) zoneSystem.checked = zones.includes('system');
        if (zoneProjects) zoneProjects.checked = zones.includes('projects');

        _indexerCustomDirs = idx.custom_dirs || [];
        _renderIndexerCustomList();

    } catch (err) {}
}

let _indexerCustomDirs = [];

function _renderIndexerCustomList() {
    const listEl = document.getElementById('indexer-custom-list');
    if (!listEl) return;

    if (_indexerCustomDirs.length === 0) {
        listEl.innerHTML = '<div class="text-[11px] text-zinc-700 italic">No custom directories added.</div>';
        return;
    }

    listEl.innerHTML = _indexerCustomDirs.map((dir, idx) => `
        <div class="flex items-center justify-between bg-white/[0.02] border border-white/5 px-3 py-2 rounded">
            <span class="font-mono-custom text-[11px] text-zinc-400 truncate flex-1 mr-4">${_escHtml(dir)}</span>
            <button onclick="removeIndexerDir(${idx})" class="text-zinc-600 hover:text-red-400 transition-colors">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
        </div>
    `).join('');
}

function addIndexerDir() {
    const input = document.getElementById('indexer-custom-input');
    const path = input.value.trim();
    if (!path) return;
    if (!_indexerCustomDirs.includes(path)) {
        _indexerCustomDirs.push(path);
        _renderIndexerCustomList();
    }
    input.value = '';
}

function removeIndexerDir(idx) {
    _indexerCustomDirs.splice(idx, 1);
    _renderIndexerCustomList();
}

async function saveIndexerSettings() {
    const statusEl = document.getElementById('indexer-status');
    const zones = [];
    if (document.getElementById('indexer-zone-core').checked) zones.push('core');
    if (document.getElementById('indexer-zone-system').checked) zones.push('system');
    if (document.getElementById('indexer-zone-projects').checked) zones.push('projects');

    const indexerData = {
        enabled_zones: zones,
        custom_dirs: _indexerCustomDirs
    };

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify({ indexer: indexerData })
        });
        const data = await res.json();
        if (data.status === 'success') {
            statusEl.textContent = 'Indexer settings saved.';
            statusEl.classList.remove('hidden', 'text-red-400');
            statusEl.classList.add('text-zinc-400');
            setTimeout(() => statusEl.classList.add('hidden'), 3000);
        }
    } catch (err) {
        statusEl.textContent = 'Save failed.';
        statusEl.classList.remove('hidden', 'text-zinc-400');
        statusEl.classList.add('text-red-400');
    }
}

async function rebuildIndex() {
    if (!await _wadeConfirm("This will wipe W.A.D.E.'s cognitive index and rebuild it from scratch. This may take a while depending on the number of files.", { title: 'Rebuild index?' })) return;

    const statusEl = document.getElementById('indexer-status');
    statusEl.textContent = 'Rebuilding index...';
    statusEl.classList.remove('hidden');

    try {
        const res = await fetch('/api/indexer/rebuild', {
            method: 'POST',
            headers: _authHeaders()
        });
        const data = await res.json();
        if (data.status === 'success') {
            statusEl.textContent = 'Rebuild initiated in background.';
            setTimeout(() => statusEl.classList.add('hidden'), 5000);
        }
    } catch (err) {
        statusEl.textContent = 'Rebuild failed to start.';
        statusEl.classList.add('text-red-400');
    }
}
async function saveSettings() {
    const userName = document.getElementById('setting-user-name')?.value;
    const provider = document.getElementById('setting-provider')?.value;
    const cpu      = parseFloat(document.getElementById('sys-cpu-thresh')?.value || document.getElementById('setting-cpu-threshold')?.value || 85);
    const ram      = parseFloat(document.getElementById('sys-mem-thresh')?.value || document.getElementById('setting-ram-threshold')?.value || 90);
    const disk     = parseFloat(document.getElementById('sys-disk-thresh')?.value || document.getElementById('setting-disk-threshold')?.value || 95);

    const payload = {
        user_name: userName,
        provider: provider,
        monitors: {
            system: {
                cpu_threshold: cpu,
                ram_threshold: ram,
                disk_threshold: disk
            }
        }
    };

    try {
        const res = await fetch('/api/settings', {
            method:  'POST',
            headers: _authHeaders(),
            body:    JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.status === 'success') {
            showStatus('settings-status', 'Configuration locked.', 'success');
            showStatus('monitor-sys-status', 'Saved.', 'success');
            _loadUserProfile();
        } else {
            throw new Error(data.message);
        }
    } catch (err) {
        showStatus('settings-status', 'Sync failed.', 'error');
        showStatus('monitor-sys-status', 'Failed.', 'error');
    }
}

function loadModelRouting() {
    fetch('/api/settings/models')
        .then(r => r.json())
        .then(data => {
            const el = document.getElementById('routing-table');
            if (!el) return;
            const routing = data.routing || {};
            if (!Object.keys(routing).length) {
                el.innerHTML = '<div class="font-mono-custom text-[11px] text-zinc-600">No routing table configured.</div>';
                return;
            }
            el.innerHTML = Object.entries(routing).map(([role, model]) => `
                <div class="flex items-center gap-4">
                    <span class="font-mono-custom text-[11px] text-zinc-400 w-24 shrink-0">${_escHtml(role)}</span>
                    <input type="text" id="routing-${_escHtml(role)}"
                           value="${_escHtml(model)}"
                           class="flex-1 bg-void border border-white/10 text-white placeholder-zinc-700 px-3 py-2 rounded-lg outline-none focus:border-white/30 font-mono-custom text-[12px] transition-all">
                </div>
            `).join('');
            _checkModelStatus();
        })
        .catch(() => {
            const el = document.getElementById('routing-table');
            if (el) el.innerHTML = '<div class="font-mono-custom text-[11px] text-red-500">Failed to load routing table.</div>';
        });
}

function saveModelRouting() {
    const rows = document.querySelectorAll('#routing-table input[id^="routing-"]');
    const routing = {};
    rows.forEach(input => {
        const role = input.id.replace('routing-', '');
        routing[role] = input.value.trim();
    });
    fetch('/api/settings/models', {
        method:  'POST',
        headers: _authHeaders(),
        body:    JSON.stringify(routing),
    })
    .then(r => r.json())
    .then(data => {
        const statusEl = document.getElementById('routing-status');
        if (statusEl) {
            statusEl.textContent = data.status === 'success' ? 'Saved.' : 'Error saving.';
            statusEl.classList.remove('hidden');
            setTimeout(() => statusEl.classList.add('hidden'), 2000);
        }
    })
    .catch(() => {
        const statusEl = document.getElementById('routing-status');
        if (statusEl) {
            statusEl.textContent = 'Network error.';
            statusEl.classList.remove('hidden');
            setTimeout(() => statusEl.classList.add('hidden'), 2000);
        }
    });
}

async function _checkModelStatus() {
    try {
        const res  = await fetch('/api/models/status');
        if (!res.ok) return;
        const data = await res.json();
        const models = data.models || {};
        document.querySelectorAll('#routing-table [id^="routing-"]').forEach(input => {
            const model  = input.value.trim().toLowerCase();
            const pulled = Object.entries(models).some(([name, isPulled]) => name.toLowerCase() === model && isPulled);
            const existing = input.parentElement.querySelector('.model-status-dot');
            if (existing) existing.remove();
            const dot = document.createElement('div');
            dot.className = 'model-status-dot';
            dot.innerHTML = `<div class="mdot ${pulled ? 'pulled' : 'missing'}"></div><span class="mstatus-lbl ${pulled ? 'pulled' : 'missing'}">${pulled ? 'ready' : 'not pulled'}</span>`;
            input.parentElement.appendChild(dot);
        });
    } catch {}
}

let _tasksInterval = null;

function esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const COLOR_MAP = {
    alertred:    '#ef4444',
    cautiongold: '#fbbf24',
    sonargreen:  '#00ff66',
};

let _reconMap = null;
let _aeroMap  = null;
let _aeroMarkers  = [];
let _reconMarkers = [];
let _securityInterval = null;
let _reconInterval    = null;
let _aeroInterval     = null;

function loadTasks() {
    fetch('/api/tasks?limit=50')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') return;
            const tasks = data.tasks || [];

            const active   = tasks.filter(t => ['pending','planning','in_progress','awaiting_approval'].includes(t.status));
            const recent   = tasks.filter(t => ['completed','failed','cancelled'].includes(t.status));

            const statsEl = document.getElementById('tasks-stats');
            if (statsEl) statsEl.textContent = `${tasks.length} tasks`;

            _renderTaskList('tasks-active-list', active, 'No active tasks.');
            _renderTaskList('tasks-recent-list', recent.slice(0, 20), 'No recent tasks.');
        })
        .catch(() => {});
}

function _renderTaskList(containerId, tasks, emptyMsg) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (!tasks.length) {
        el.innerHTML = `<div class="font-sans text-[13px] text-zinc-600 text-center py-6">${_escHtml(emptyMsg)}</div>`;
        return;
    }

    const _badgeClass = {
        pending:'task-badge-pending', queued:'task-badge-pending',
        planning:'task-badge-running', in_progress:'task-badge-running', awaiting_approval:'task-badge-pending',
        completed:'task-badge-done', done:'task-badge-done',
        failed:'task-badge-failed', error:'task-badge-failed',
        cancelled:'task-badge-cancelled',
        invalid_plan:'task-badge-invalid', goal_not_satisfied:'task-badge-invalid', tool_mismatch:'task-badge-invalid',
    };
    const _badgeLabel = {
        pending:'Pending', queued:'Pending',
        planning:'\u25cf Running', in_progress:'\u25cf Running', awaiting_approval:'Pending',
        completed:'Done', done:'Done',
        failed:'Failed', error:'Failed',
        cancelled:'Cancelled',
        invalid_plan:'Goal Not Satisfied', goal_not_satisfied:'Goal Not Satisfied', tool_mismatch:'Tool Mismatch',
    };

    el.innerHTML = tasks.map((t, idx) => {
        const badgeClass = _badgeClass[t.status] || 'task-badge-pending';
        const badgeLabel = _badgeLabel[t.status] || t.status.replace(/_/g,' ');
        const isRunning  = ['planning','in_progress'].includes(t.status);
        const isDone     = ['completed','done'].includes(t.status);
        const showBar    = isRunning || isDone;

        const stepIdx   = t.step_index   ?? 0;
        const stepTotal = t.total_steps  ?? 0;
        const pct = stepTotal > 0 ? Math.round((stepIdx / stepTotal) * 100) : (isDone ? 100 : 0);

        const duration = (t.started_at && t.completed_at)
            ? (() => {
                const s = Math.round((new Date(t.completed_at) - new Date(t.started_at)) / 1000);
                return s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`;
              })()
            : null;

        const rawGoal = t.goal ?? '';
        const goal = rawGoal.length > 160 ? rawGoal.slice(0,157) + '\u2026' : rawGoal;
        const goalColor = isRunning ? 'color:#d4d4d4' : (isDone ? 'color:#666' : 'color:#555');

        const metaParts = [];
        if (stepTotal > 0) metaParts.push(`${stepIdx} of ${stepTotal} steps`);
        if (duration)      metaParts.push(duration);
        metaParts.push(_timeAgo(t.completed_at || t.created_at));

        const subtasks = t.subtasks || [];
        const subtaskHtml = (isRunning && subtasks.length) ? `
            <div class="task-subtasks" id="subtasks-${idx}">
                ${subtasks.map(s => {
                    const dotColor = s.status === 'done' ? '#00ff66' : s.status === 'running' ? '#e5e5e5' : '#333';
                    const cls = s.status === 'done'
                        ? 'font-mono-custom text-[.65rem] text-zinc-600 line-through'
                        : s.status === 'running'
                        ? 'font-mono-custom text-[.65rem] text-zinc-400'
                        : 'font-mono-custom text-[.65rem] text-zinc-600';
                    return `<div class="subtask-row">
                        <div class="subtask-dot" style="background:${dotColor}"></div>
                        <span class="${cls}">${_escHtml(s.goal || s.description || '')}</span>
                    </div>`;
                }).join('')}
            </div>` : '';

        const taskId = t.id || '';
        const subtaskToggle = isRunning && subtasks.length
            ? `this.querySelector('.task-subtasks')?.classList.toggle('hidden');`
            : '';
        const godModeSelect = `if(_godModeOpen){_godModeTaskId=${JSON.stringify(taskId)};_fetchGodModeData();}`;
        return `<div class="module-card p-4 cursor-pointer"
                     onclick="${subtaskToggle}${godModeSelect}">
            <div class="flex items-start justify-between gap-3 mb-1">
                <p class="font-sans text-[13px] leading-snug flex-1" style="${goalColor}">${_escHtml(goal)}</p>
                <span class="task-badge ${badgeClass}">${_escHtml(badgeLabel)}</span>
            </div>
            ${showBar ? `<div class="task-prog-wrap"><div class="task-prog-fill ${isDone ? 'done' : ''}" style="width:${pct}%"></div></div>` : ''}
            <div class="flex items-center justify-between mt-1">
                <span class="font-mono-custom text-[.6rem] text-zinc-600">${_escHtml(metaParts.join(' \u00b7 '))}</span>
                <span class="font-mono-custom text-[.6rem] text-zinc-700">${_escHtml(t.created_by || '')}</span>
            </div>
            ${subtaskHtml}
        </div>`;
    }).join('');
}

function _timeAgo(isoStr) {
    if (!isoStr) return '';
    const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
    if (diff < 60)   return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
    return `${Math.floor(diff/3600)}h ago`;
}

function _monitorCfgPanel(name, extra) {
    if (name === 'system') {
        const cpu  = extra?.cpu_threshold  ?? 85;
        const ram  = extra?.ram_threshold  ?? 90;
        const disk = extra?.disk_threshold ?? 95;
        return `<div class="monitor-cfg-label">Resource Thresholds</div>
            <div class="grid grid-cols-3 gap-3 mb-3">
                <div><label class="font-mono-custom text-[9px] text-zinc-600 uppercase block mb-1">CPU %</label>
                     <input type="number" id="sys-cpu-thresh" value="${cpu}" class="w-full bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]"></div>
                <div><label class="font-mono-custom text-[9px] text-zinc-600 uppercase block mb-1">RAM %</label>
                     <input type="number" id="sys-mem-thresh" value="${ram}" class="w-full bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]"></div>
                <div><label class="font-mono-custom text-[9px] text-zinc-600 uppercase block mb-1">Disk %</label>
                     <input type="number" id="sys-disk-thresh" value="${disk}" class="w-full bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]"></div>
            </div>
            <button onclick="saveSettings()" class="btn-execute h-8 px-4 text-[11px]">Save Thresholds</button>
            <span id="monitor-sys-status" class="font-mono-custom text-[10px] text-zinc-400 hidden ml-3"></span>`;
    }
    if (name === 'schedule') {
        return `<div class="monitor-cfg-label">Scheduled Jobs</div>
            <div id="sched-jobs-list" class="space-y-2 mb-3"><span class="font-mono-custom text-[10px] text-zinc-600">Loading...</span></div>
            <div class="grid grid-cols-3 gap-2 mb-2">
                <input type="text" id="sched-goal" placeholder="Goal..." class="col-span-3 bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]">
                <select id="sched-trigger" class="bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px] cursor-pointer">
                    <option value="cron">Cron (HH:MM)</option>
                    <option value="interval">Interval (4h / 30m)</option>
                </select>
                <input type="text" id="sched-value" placeholder="08:00" class="bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]">
                <button onclick="addScheduleJob()" class="btn-execute h-8 px-3 text-[11px]">Add</button>
            </div>
            <span id="sched-status" class="font-mono-custom text-[10px] text-zinc-400 hidden"></span>`;
    }
    if (name === 'filesystem') {
        const path = extra?.watch_path || '~/.wade/workspace';
        return `<div class="monitor-cfg-label">Watch Directory</div>
            <div class="font-mono-custom text-[10px] text-zinc-500 mb-2">${_escHtml(path)}</div>
            <div class="flex gap-2">
                <input type="text" id="fs-watch-path" placeholder="New path..." class="flex-1 bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]">
                <button onclick="updateFilesystemPath()" class="btn-execute h-8 px-3 text-[11px]">Save</button>
            </div>
            <span id="fs-status" class="font-mono-custom text-[10px] text-zinc-400 hidden mt-1 block"></span>`;
    }
    if (name === 'proactive') {
        const cooldown = extra?.cooldown_minutes   ?? 15;
        const idle     = extra?.idle_check_minutes ?? 20;
        const maxHr    = extra?.max_per_hour       ?? 4;
        return `<div class="monitor-cfg-label">Proactive Settings</div>
            <div class="grid grid-cols-3 gap-3 mb-3">
                <div><label class="font-mono-custom text-[9px] text-zinc-600 uppercase block mb-1">Cooldown (min)</label>
                     <input type="number" id="proactive-cooldown" value="${cooldown}" class="w-full bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]"></div>
                <div><label class="font-mono-custom text-[9px] text-zinc-600 uppercase block mb-1">Idle Check (min)</label>
                     <input type="number" id="proactive-idle" value="${idle}" class="w-full bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]"></div>
                <div><label class="font-mono-custom text-[9px] text-zinc-600 uppercase block mb-1">Max / Hour</label>
                     <input type="number" id="proactive-maxhr" value="${maxHr}" class="w-full bg-void border border-white/10 text-white p-2 rounded-lg outline-none font-mono-custom text-[11px]"></div>
            </div>
            <button onclick="saveProactiveSettings()" class="btn-execute h-8 px-4 text-[11px]">Save</button>
            <span id="proactive-status" class="font-mono-custom text-[10px] text-zinc-400 hidden ml-3"></span>`;
    }
    return '';
}

function _renderMonitorCards(monitors) {
    const el = document.getElementById('monitors-list');
    if (!el) return;

            const _icons = {
                proactive:  '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.86 9.86 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>',
                schedule:   '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>',
                system:     '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2v-4M9 21H5a2 2 0 01-2-2v-4m0 0h18"/>',
                filesystem: '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/>',
            };

            const cards = monitors.map(m => {
                const icon  = _icons[m.name] || _icons.system;
                const trig  = m.last_trigger ? _timeAgo(m.last_trigger) : 'Never';
                const label = m.name.charAt(0).toUpperCase() + m.name.slice(1);

                const running = !!m.running;
                const dotColor    = running ? 'bg-[#00ff66]' : 'bg-red-500';
                const statusLabel = running ? 'RUNNING'      : 'STOPPED';

                let extraHtml = '';
                if (m.name === 'system' && m.extra) {
                    extraHtml = `<div class="flex items-center gap-4 mt-3 pt-3 border-t border-white/5">
                        <div class="flex flex-col"><span class="font-mono-custom text-[9px] text-zinc-600 uppercase">CPU</span><span class="font-mono-custom text-[11px] text-zinc-300">${_escHtml(m.extra.cpu || '0%')}</span></div>
                        <div class="flex flex-col"><span class="font-mono-custom text-[9px] text-zinc-600 uppercase">RAM</span><span class="font-mono-custom text-[11px] text-zinc-300">${_escHtml(m.extra.ram || '0%')}</span></div>
                        <div class="flex flex-col"><span class="font-mono-custom text-[9px] text-zinc-600 uppercase">Disk</span><span class="font-mono-custom text-[11px] text-zinc-300">${_escHtml(m.extra.disk || '0%')}</span></div>
                    </div>`;
                } else if (m.name === 'filesystem' && m.extra) {
                    extraHtml = `<div class="mt-2"><span class="font-mono-custom text-[10px] text-zinc-600">Watching: </span><span class="font-mono-custom text-[10px] text-zinc-400">${_escHtml(m.extra.watch_path || '')}</span></div>`;
                } else if (m.name === 'schedule' && m.extra) {
                    extraHtml = `<div class="mt-2 flex gap-4"><span class="font-mono-custom text-[10px] text-zinc-600">${_escHtml(String(m.extra.job_count))} job${m.extra.job_count !== 1 ? 's' : ''}</span>${m.extra.next_run ? `<span class="font-mono-custom text-[10px] text-zinc-500">Next: ${_escHtml(String(m.extra.next_run))}</span>` : ''}</div>`;
                } else if (m.name === 'proactive' && m.extra) {
                    extraHtml = `<div class="mt-2 flex gap-4"><span class="font-mono-custom text-[10px] text-zinc-600">Cooldown ${_escHtml(String(m.extra.cooldown_minutes))}m</span><span class="font-mono-custom text-[10px] text-zinc-600">Max ${_escHtml(String(m.extra.max_per_hour))}/hr</span></div>`;
                }

                const cfgHtml = _monitorCfgPanel(m.name, m.extra);

                return `<div class="module-card p-5 monitor-card flex flex-col gap-2" id="mon-card-${_escHtml(m.name)}">
                    <div class="flex items-start gap-4">
                        <div class="w-8 h-8 rounded-lg bg-zinc-800 flex items-center justify-center shrink-0">
                            <svg class="w-4 h-4 text-zinc-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">${icon}</svg>
                        </div>
                        <div class="flex-1">
                            <div class="flex items-center justify-between mb-1">
                                <span class="font-mono-custom text-[12px] font-medium text-white">${_escHtml(label)} Monitor</span>
                                <div class="flex items-center gap-2">
                                    <div class="flex items-center gap-1.5">
                                        <div class="w-1.5 h-1.5 rounded-full ${dotColor} ${statusLabel === 'RUNNING' ? 'status-pulse' : ''}"></div>
                                        <span class="font-mono-custom text-[10px] text-zinc-400">${statusLabel}</span>
                                    </div>
                                    ${cfgHtml ? `<button class="btn-ghost h-6 px-3 text-[10px]" onclick="toggleMonitorConfig(this.closest('.monitor-card'))">Configure</button>` : ''}
                                </div>
                            </div>
                            <div class="flex items-center gap-3 mt-1">
                                <span class="font-mono-custom text-[10px] text-zinc-600">Last trigger:</span>
                                <span class="font-mono-custom text-[10px] text-zinc-500">${_escHtml(trig)}</span>
                            </div>
                        </div>
                    </div>
                    ${extraHtml}
                    ${cfgHtml ? `<div class="monitor-cfg">${cfgHtml}</div>` : ''}
                </div>`;
            }).join('');

            el.innerHTML = (monitors.length ? cards : '<div class="font-sans text-[13px] text-zinc-600 text-center py-12 sm:col-span-2">No monitors registered.</div>');
}

function loadMonitors() {
    fetch('/api/monitors')
        .then(r => r.json())
        .then(data => _renderMonitorCards(data.monitors || []))
        .catch(() => _renderMonitorCards([
            { name: 'system',     running: false },
            { name: 'schedule',   running: false },
            { name: 'filesystem', running: false },
            { name: 'proactive',  running: false },
        ]));
}

function toggleMonitorConfig(card) {
    const isOpen = card.classList.contains('expanded');
    document.querySelectorAll('.monitor-card.expanded').forEach(c => c.classList.remove('expanded'));
    if (!isOpen) {
        card.classList.add('expanded');
        if (card.id === 'mon-card-schedule') _loadScheduleJobs();
    }
}

async function _loadScheduleJobs() {
    const el = document.getElementById('sched-jobs-list');
    if (!el) return;
    try {
        const r    = await fetch('/api/monitors/schedule/jobs');
        const data = await r.json();
        const jobs = data.jobs || [];
        el.innerHTML = jobs.length
            ? jobs.map(j => `<div class="flex items-center gap-3 py-1 border-b border-white/5">
                <span class="font-mono-custom text-[10px] text-zinc-300 flex-1">${_escHtml(j.goal || '')}</span>
                <span class="font-mono-custom text-[9px] text-zinc-600">${_escHtml(j.next_run || 'pending')}</span>
                <button class="del-job-btn text-zinc-600 hover:text-red-400 font-mono-custom text-[11px] px-1" data-job-id="${_escHtml(j.id)}">✕</button>
            </div>`).join('')
            : '<span class="font-mono-custom text-[10px] text-zinc-600">No jobs scheduled.</span>';
        el.querySelectorAll('.del-job-btn').forEach(btn =>
            btn.addEventListener('click', () => deleteScheduleJob(btn.dataset.jobId))
        );
    } catch {
        if (el) el.innerHTML = '<span class="font-mono-custom text-[10px] text-red-500/60">Failed to load jobs.</span>';
    }
}

async function addScheduleJob() {
    const goal     = document.getElementById('sched-goal')?.value.trim();
    const trigger  = document.getElementById('sched-trigger')?.value;
    const value    = document.getElementById('sched-value')?.value.trim();
    const statusEl = document.getElementById('sched-status');
    if (!goal || !value) return;
    try {
        const r    = await fetch('/api/monitors/schedule/jobs', {
            method: 'POST', headers: _authHeaders(),
            body: JSON.stringify({ goal, trigger, value }),
        });
        const data = await r.json();
        if (statusEl) {
            statusEl.textContent = data.status === 'success' ? 'Job added.' : (data.detail || 'Error.');
            statusEl.classList.remove('hidden');
            setTimeout(() => statusEl.classList.add('hidden'), 2000);
        }
        if (data.status === 'success') _loadScheduleJobs();
    } catch {
        if (statusEl) { statusEl.textContent = 'Network error.'; statusEl.classList.remove('hidden'); }
    }
}

async function deleteScheduleJob(jobId) {
    try {
        await fetch(`/api/monitors/schedule/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE', headers: _authHeaders() });
        _loadScheduleJobs();
    } catch {}
}

async function updateFilesystemPath() {
    const path     = document.getElementById('fs-watch-path')?.value.trim();
    const statusEl = document.getElementById('fs-status');
    if (!path) return;
    try {
        const r    = await fetch('/api/monitors/filesystem/path', {
            method: 'PATCH', headers: _authHeaders(),
            body: JSON.stringify({ path }),
        });
        const data = await r.json();
        if (statusEl) {
            statusEl.textContent = data.status === 'success' ? `Updated to ${data.watch_path}` : (data.detail || 'Error.');
            statusEl.classList.remove('hidden');
            setTimeout(() => statusEl.classList.add('hidden'), 3000);
        }
    } catch {
        if (statusEl) { statusEl.textContent = 'Network error.'; statusEl.classList.remove('hidden'); }
    }
}

async function saveProactiveSettings() {
    const cooldown = parseInt(document.getElementById('proactive-cooldown')?.value, 10) || 15;
    const idle     = parseInt(document.getElementById('proactive-idle')?.value,     10) || 20;
    const maxHr    = parseInt(document.getElementById('proactive-maxhr')?.value,    10) || 4;
    const statusEl = document.getElementById('proactive-status');
    try {
        const r    = await fetch('/api/monitors/proactive/settings', {
            method: 'PATCH', headers: _authHeaders(),
            body: JSON.stringify({ cooldown_minutes: cooldown, idle_check_minutes: idle, max_per_hour: maxHr }),
        });
        const data = await r.json();
        if (statusEl) {
            statusEl.textContent = data.status === 'success' ? 'Saved.' : 'Error.';
            statusEl.classList.remove('hidden');
            setTimeout(() => statusEl.classList.add('hidden'), 2000);
        }
    } catch {
        if (statusEl) { statusEl.textContent = 'Network error.'; statusEl.classList.remove('hidden'); }
    }
}

async function loadSecurity() {
    try {
        const r = await fetch('/api/security');
        if (!r.ok) throw new Error(`/api/security ${r.status}`);
        const data = await r.json();

        const armedEl = document.getElementById('security-armed-status');
        const countEl = document.getElementById('security-camera-count');
        const gridEl  = document.getElementById('security-camera-grid');
        const alertEl = document.getElementById('security-alerts');

        if (!gridEl || !alertEl) return;

        const armed = data.system_armed;
        if (armedEl) {
            armedEl.textContent = armed ? 'ARMED' : 'DISARMED';
            armedEl.style.color = armed ? '#ef4444' : 'var(--accent)';
        }

        const btnArm = document.getElementById('btn-arm');
        const btnDisarm = document.getElementById('btn-disarm');

        if (btnArm && btnDisarm) {
            if (armed) {
                btnArm.classList.remove('btn-ghost');
                btnArm.classList.add('btn-execute');
                btnDisarm.classList.remove('btn-execute');
                btnDisarm.classList.add('btn-ghost');
            } else {
                btnArm.classList.remove('btn-execute');
                btnArm.classList.add('btn-ghost');
                btnDisarm.classList.remove('btn-ghost');
                btnDisarm.classList.add('btn-execute');
            }
        }

        const cameras = data.cameras || [];
        if (countEl) countEl.textContent = `${cameras.length} camera${cameras.length !== 1 ? 's' : ''}`;

    if (cameras.length === 0) {
        gridEl.innerHTML = '<div class="font-sans text-[13px] text-zinc-600 text-center py-12 sm:col-span-3">No cameras found.</div>';
    } else {
        const ts = Date.now();
        gridEl.innerHTML = cameras.map(cam => `
            <div class="camera-card">
                <img src="/api/security/image/${encodeURIComponent(cam.name)}?t=${ts}"
                     alt="${_escHtml(cam.name)}"
                     onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
                <div class="camera-card-placeholder" style="display:none">NO FEED</div>
                <div class="camera-card-footer">
                    <span class="camera-card-name">${_escHtml(cam.name)}</span>
                    <button class="camera-snap-btn" id="snap-${_escHtml(cam.name)}" onclick="securityAction('snap', '${_escHtml(cam.name)}')">SNAP</button>
                </div>
            </div>
        `).join('');
    }

    const alerts = data.alerts || [];
        if (alerts.length === 0) {
            alertEl.innerHTML = '<div class="font-sans text-[13px] text-zinc-600 py-4">No alerts.</div>';
        } else {
            alertEl.innerHTML = alerts.map(a => `
                <div class="alert-row">
                    <span class="alert-time">${esc(a.time)}</span>
                    <span class="alert-msg ${esc(a.level)}">${esc(a.message)}</span>
                </div>
            `).join('');
        }
    } catch (e) {
        console.error('loadSecurity error:', e);
    }
}

async function securityAction(action, cameraName = null) {
    const btnArm = document.getElementById('btn-arm');
    const btnDisarm = document.getElementById('btn-disarm');
    
    if (action === 'arm' && btnArm && btnDisarm) {
        btnArm.classList.remove('btn-ghost');
        btnArm.classList.add('btn-execute');
        btnDisarm.classList.remove('btn-execute');
        btnDisarm.classList.add('btn-ghost');
    } else if (action === 'disarm' && btnArm && btnDisarm) {
        btnArm.classList.remove('btn-execute');
        btnArm.classList.add('btn-ghost');
        btnDisarm.classList.remove('btn-ghost');
        btnDisarm.classList.add('btn-execute');
    }

    try {
        await fetch('/api/security', {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify({ action, camera_name: cameraName })
        });
        await loadSecurity();
    } catch (e) {
        console.error('securityAction error:', e);
        await loadSecurity();
    }
}

const COUNTRY_CENTROIDS = {
    USA: [37.1,  -95.7],
    GBR: [55.4,   -3.4],
    RUS: [61.5,  105.3],
    CHN: [35.9,  104.2],
    DEU: [51.2,   10.5],
    FRA: [46.2,    2.2],
    JPN: [36.2,  138.3],
    IND: [20.6,   79.0],
    BRA: [-14.2, -51.9],
    AUS: [-25.3, 133.8],
    ZAF: [-30.6,  22.9],
    MEX: [23.6, -102.5],
    SAU: [23.9,   45.1],
    IRN: [32.4,   53.7],
    PRK: [40.3,  127.5],
    UKR: [49.0,   31.5],
    ISR: [31.0,   35.0],
    TUR: [38.9,   35.2],
};

async function loadRecon() {
    await _ensureLeaflet();
    if (!_reconMap) {
        _reconMap = L.map('recon-map', {
            zoomControl: true, 
            attributionControl: false 
        }).setView([20, 0], 2);
        
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
            subdomains: 'abcd',
            maxZoom: 20
        }).addTo(_reconMap);
        
        setTimeout(() => { _reconMap.invalidateSize(); }, 100);
    }

    try {
        const r = await fetch('/api/recon');
        if (!r.ok) throw new Error(`/api/recon ${r.status}`);
        const data = await r.json();

        const statusEl = document.getElementById('recon-status');
        const volValEl = document.getElementById('recon-volatility-value');
        const volBarEl = document.getElementById('recon-volatility-bar');
        const feedEl   = document.getElementById('recon-feed');
        const insightEl = document.getElementById('recon-volatility-insight');
        const volStatusEl = document.getElementById('recon-volatility-status');

        if (!feedEl) return;

        const feed = data.intel_feed || [];
        if (statusEl) statusEl.textContent = `${feed.length} intercept${feed.length !== 1 ? 's' : ''}`;

        const vol = data.wade_volatility_index ?? 0;
        if (volValEl) volValEl.textContent = vol.toFixed(1);
        if (volBarEl) volBarEl.style.width = `${Math.min(vol, 100)}%`;

        if (insightEl) {
            insightEl.textContent = data.wade_insight || "Processing intercepts. Baseline variance detected across monitored geopolitical and market sectors.";
        }

        if (volStatusEl) {
            if (vol >= 80) {
                volStatusEl.textContent = 'CRITICAL';
                volStatusEl.className = 'font-mono-custom text-[10px] text-[#ef4444] tracking-wider';
                if(volBarEl) volBarEl.style.backgroundColor = '#ef4444';
            } else if (vol >= 50) {
                volStatusEl.textContent = 'ELEVATED';
                volStatusEl.className = 'font-mono-custom text-[10px] text-[#fbbf24] tracking-wider';
                if(volBarEl) volBarEl.style.backgroundColor = '#fbbf24';
            } else {
                volStatusEl.textContent = 'NOMINAL';
                volStatusEl.className = 'font-mono-custom text-[10px] text-zinc-500 tracking-wider';
                if(volBarEl) volBarEl.style.backgroundColor = '#d4d4d8';
            }
        }

        _reconMarkers.forEach(m => m.remove());
        _reconMarkers = [];

        feed.forEach(item => {
            const coords = COUNTRY_CENTROIDS[item.iso];
            if (!coords) return;
            const color = COLOR_MAP[item.color] || '#888888';
            
            const customIcon = L.divIcon({
                className: 'bg-transparent border-0',
                html: `
                    <div class="flex items-center gap-2 pointer-events-none" style="width: 250px;">
                        <div class="w-1.5 h-1.5 rounded-full shrink-0" style="background-color: ${color}; box-shadow: 0 0 8px ${color}, 0 0 14px ${color};"></div>
                        <span class="font-mono-custom text-[9px] text-zinc-400 uppercase tracking-widest drop-shadow-md">${esc(item.region)}</span>
                    </div>
                `,
                iconSize: [250, 10],
                iconAnchor: [3, 5],
                popupAnchor: [0, -5]
            });

            const m = L.marker(coords, { icon: customIcon })
              .bindPopup(`
                <span class="font-mono-custom text-[10px] text-[${color}] tracking-widest uppercase block mb-1">${esc(item.region)}</span>
                <span class="font-sans text-[12px] text-zinc-300">${esc(item.story)}</span>
              `)
              .addTo(_reconMap);
              
            _reconMarkers.push(m);
        });

        if (feed.length === 0) {
            feedEl.innerHTML = '<div class="font-sans text-[13px] text-zinc-600 py-4">No intercepts.</div>';
        } else {
            feedEl.innerHTML = feed.map(item => {
                const dotColor = COLOR_MAP[item.color] || '#888888'; 
                
                return `
                <div class="border border-white/5 p-4 rounded-lg bg-white/[0.02] hover:bg-white/[0.04] transition-colors mb-3">
                    <div class="flex justify-between items-center mb-2">
                        <div class="flex items-center gap-2.5">
                            <div class="w-1.5 h-1.5 rounded-full shrink-0" style="background-color: ${dotColor}; box-shadow: 0 0 6px ${dotColor}80;"></div>
                            <span class="font-mono-custom text-[10px] text-zinc-300 tracking-widest uppercase">${esc(item.region)}</span>
                        </div>
                        <span class="font-mono-custom text-[9px] text-zinc-500">${esc(item.time)}</span>
                    </div>
                    <p class="font-sans text-[12px] text-zinc-400 leading-relaxed">${esc(item.story)}</p>
                </div>
            `}).join('');
        }
    } catch (e) {
        console.error('loadRecon error:', e);
    }
}

async function loadAero() {
    await _ensureLeaflet();
    if (!_aeroMap) {
        _aeroMap = L.map('aero-map', {
            zoomControl: false, 
            attributionControl: false 
        }).setView([39.5, -98.35], 4);

        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
            subdomains: 'abcd',
            maxZoom: 20
        }).addTo(_aeroMap);
        
        setTimeout(() => { _aeroMap.invalidateSize(); }, 100);
    }

    try {
        const r = await fetch('/api/aero');
        if (!r.ok) throw new Error(`/api/aero ${r.status}`);
        const data = await r.json();

        const countEl    = document.getElementById('aero-count');
        const dvTagEl    = document.getElementById('aero-deep-view');
        const insightTxt = document.getElementById('aero-insight-text');
        const tableEl    = document.getElementById('aero-table');

        if (!tableEl) return;

        const flights = data.flights || [];
        if (countEl) countEl.textContent = `${flights.length} ASSETS TRACKED`;

        const dv = data.deep_view;
        if (dv && dvTagEl && insightTxt) {
            dvTagEl.textContent = dv.status || 'NOMINAL';
            dvTagEl.style.color = COLOR_MAP[dv.color] || 'var(--accent)';
            dvTagEl.classList.remove('hidden');
            insightTxt.textContent = dv.insight || '';
        }

        _aeroMarkers.forEach(m => m.remove());
        _aeroMarkers = [];

        flights.forEach(f => {
            if (f.lat == null || f.lng == null) return;
            const color = COLOR_MAP[f.ai_prediction?.color] || '#888888';
            
            const customIcon = L.divIcon({
                className: 'bg-transparent border-0',
                html: `
                    <div class="flex items-center gap-1.5 pointer-events-none" style="width: 150px;">
                        <div class="w-1 h-1 rounded-full shrink-0" style="background-color: ${color}; box-shadow: 0 0 6px ${color}, 0 0 10px ${color};"></div>
                        <span class="font-mono-custom text-[8px] text-zinc-600 uppercase tracking-widest drop-shadow-md">${esc(f.callsign || '')}</span>
                    </div>
                `,
                iconSize: [150, 10],
                iconAnchor: [2, 5],
                popupAnchor: [0, -5]
            });

            const m = L.marker([f.lat, f.lng], { icon: customIcon })
                .bindPopup(`
                    <b class="text-white">${esc(f.callsign || 'UNKNOWN')}</b><br>
                    <span class="text-zinc-400">Alt:</span> ${f.alt_ft != null ? f.alt_ft.toLocaleString() : '?'} ft<br>
                    <span class="text-zinc-400">Speed:</span> ${f.speed_kts ?? '?'} kts<br>
                    <span style="color:${color}; margin-top: 4px; display: block; font-size: 10px;" class="font-mono-custom uppercase tracking-widest">${esc(f.ai_prediction?.status ?? '')}</span>
                `).addTo(_aeroMap);
            _aeroMarkers.push(m);
        });

        if (flights.length === 0) {
            tableEl.innerHTML = '<div class="font-sans text-[13px] text-zinc-600 text-center py-12">No flights in range.</div>';
        } else {
            tableEl.innerHTML = flights.slice(0, 50).map(f => `
                <div class="flight-row">
                    <span class="flight-callsign">${esc(f.callsign || '---')}</span>
                    <span class="flight-data">${f.alt_ft != null ? f.alt_ft.toLocaleString() + ' ft' : '---'}</span>
                    <span class="flight-data">${f.speed_kts != null ? f.speed_kts + ' kts' : '---'}</span>
                    <span class="flight-data">${f.heading != null ? f.heading + '°' : '---'}</span>
                    <span class="flight-prediction ${esc(f.ai_prediction?.color || '')}">${esc(f.ai_prediction?.status) || '---'}</span>
                </div>
            `).join('');
        }
    } catch (e) {
        console.error('loadAero error:', e);
    }
}

async function _loadUserProfile() {
    try {
        const res = await fetch('/api/user/profile');
        const data = await res.json();
        if (data.status === 'success' && data.name) {
            _userName = data.name;
            if (UI.userInitial) UI.userInitial.textContent = _userName.charAt(0).toUpperCase();
            if (UI.userNameLabel) UI.userNameLabel.textContent = _userName;
            if (UI.userNameTooltip) UI.userNameTooltip.textContent = _userName;
        }
    } catch (e) {
        console.warn('[W.A.D.E.] Could not fetch user profile:', e);
    }
}

async function _loadVersionTag() {
    try {
        const res = await fetch('/api/version');
        const data = await res.json();
        const el = document.getElementById('version-tag');
        if (el && data.label) el.textContent = data.label;
    } catch (e) {
        console.warn('[W.A.D.E.] Could not fetch version info:', e);
    }
}

(async function checkForUpdate() {
  try {
    const _UPDATE_TTL = 86400000; // 24 hours
    const cached = JSON.parse(localStorage.getItem('wade_update_check') || 'null');
    let data;
    if (cached && (Date.now() - cached.ts) < _UPDATE_TTL) {
        data = cached.data;
    } else {
        const res = await fetch("/api/update-check");
        data = await res.json();
        try { localStorage.setItem('wade_update_check', JSON.stringify({ ts: Date.now(), data })); } catch {}
    }
    if (!data.update_available) return;

    const banner = document.createElement("div");
    banner.id = "update-banner";
    banner.innerHTML = `
      <span>W.A.D.E. v${data.latest} is available &mdash;
        <a href="${data.release_url}" target="_blank" rel="noopener">update</a>
      </span>
      <button onclick="this.parentElement.remove()" aria-label="Dismiss">&times;</button>
    `;
    banner.style.cssText = [
      "position:fixed", "bottom:1rem", "right:1rem",
      "background:var(--bg-panel,#13131a)", "border:1px solid var(--accent,#6c63ff)",
      "border-radius:8px", "padding:0.5rem 1rem", "display:flex",
      "align-items:center", "gap:1rem", "z-index:999",
      "font-size:0.85rem", "color:var(--text-secondary,#8888aa)"
    ].join(";");
    document.body.appendChild(banner);
  } catch {}
})();

// Users Panel
const _TIER_COLORS = {
    admin:    'text-[#00ff66]',
    family:   'text-zinc-300',
    friends:  'text-zinc-400',
    guests:   'text-zinc-500',
    strangers:'text-zinc-600',
};

async function loadUsers() {
    const tierFilter = document.getElementById('users-tier-filter')?.value || '';
    const url = '/api/admin/users' + (tierFilter ? `?tier=${encodeURIComponent(tierFilter)}` : '');
    const el = document.getElementById('users-list');
    if (!el) return;
    try {
        const r = await fetch(url, { headers: { 'X-Device-ID': _deviceId } });
        if (r.status === 403) {
            el.innerHTML = '<div class="font-sans text-[13px] text-zinc-500 text-center py-12">Admin access required to view users.</div>';
            return;
        }
        const data = await r.json();
        const users = data.users || [];
        const statsEl = document.getElementById('users-stats');
        if (statsEl) statsEl.textContent = `${data.summary?.total ?? 0} users`;

        el.innerHTML = users.length
            ? users.map(u => {
                const tier    = u.tier || 'strangers';
                const initial = (u.display || '?')[0].toUpperCase();
                return `<div class="user-card-row"
                     onclick="loadUserHistory('${_escHtml(tier)}','${_escHtml(u.phone)}','${_escHtml(u.display)}')">
                    <div class="user-avatar-circle tier-${_escHtml(tier)}">${_escHtml(initial)}</div>
                    <div class="user-card-info">
                        <span class="user-card-name">${_escHtml(u.display || 'Unknown')}</span>
                        <span class="user-card-phone">${_escHtml(u.phone || '')}</span>
                    </div>
                    <div class="user-card-stat">
                        <span class="user-card-msgs">${u.message_count} msgs</span>
                        <span class="user-card-last">${_escHtml(u.last_active || '')}</span>
                    </div>
                    <span class="tier-pill tp-${_escHtml(tier)}">${_escHtml(tier)}</span>
                    <select onchange="changeUserTier('${_escHtml(u.jid)}', this.value)" onclick="event.stopPropagation()"
                            class="bg-void border border-white/10 text-white px-2 py-1 rounded-md outline-none font-mono-custom text-[10px] cursor-pointer">
                        ${tier === 'strangers' ? '<option value="" disabled selected>strangers</option>' : ''}
                        ${['admin','family','friends','guests'].map(t =>
                            `<option value="${t}" ${t === tier ? 'selected' : ''}>${t}</option>`
                        ).join('')}
                    </select>
                    <div class="flex items-center gap-2">
                        ${u.message_count > 0 ? `<button onclick="showUserDeleteModal('${_escHtml(tier)}','${_escHtml(u.phone)}','${_escHtml(u.display)}');event.stopPropagation()" class="btn-ghost h-7 px-3 text-[10px] text-red-500/60 hover:text-red-400 shrink-0">Delete Chat</button>` : ''}
                        ${tier !== 'strangers' ? `<button onclick="unregisterUser('${_escHtml(u.jid)}','${_escHtml(tier)}');event.stopPropagation()" class="btn-ghost h-7 px-3 text-[10px] text-zinc-500 hover:text-white shrink-0">Remove</button>` : ''}
                    </div>
                </div>`;
            }).join('')
            : '<div class="font-sans text-[13px] text-zinc-600 text-center py-12">No users found.</div>';
    } catch {
        if (el) el.innerHTML = '<div class="font-sans text-[13px] text-red-500/60 text-center py-12">Failed to load users.</div>';
    }
}

// User history deletion
let _userToDelete = null;

function showUserDeleteModal(tier, phone, display) {
    _userToDelete = { tier, phone };
    document.getElementById('delete-user-name').textContent = display || phone;
    document.getElementById('user-delete-modal').classList.remove('hidden');
}

function hideUserDeleteModal() {
    _userToDelete = null;
    document.getElementById('user-delete-modal').classList.add('hidden');
}

async function confirmDeleteUserHistory() {
    if (!_userToDelete) return;
    const { tier, phone } = _userToDelete;
    
    try {
        const res = await fetch(`/api/admin/users/${tier}/${phone}/history`, {
            method: 'DELETE',
            headers: _authHeaders()
        });
        const data = await res.json();
        if (data.status === 'success' || data.status === 'ok') {
            hideUserDeleteModal();
            loadUsers();
        } else {
            await _wadeAlert("Failed to clear history: " + (data.detail || "Unknown error"), { title: 'Error' });
        }
    } catch (e) {
        console.error("Delete history error:", e);
        await _wadeAlert("Failed to clear history.", { title: 'Error' });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const confirmBtn = document.getElementById('confirm-delete-user-btn');
    if (confirmBtn) {
        confirmBtn.onclick = confirmDeleteUserHistory;
    }
});

async function loadUserHistory(tier, phone, display) {
    const panel  = document.getElementById('user-history-panel');
    const title  = document.getElementById('user-history-title');
    const content = document.getElementById('user-history-content');
    if (!panel || !content) return;
    panel.classList.remove('hidden');
    if (title) title.textContent = display;
    content.innerHTML = '<div class="font-mono-custom text-[11px] text-zinc-500">Loading...</div>';
    try {
        const r = await fetch(
            `/api/admin/users/${encodeURIComponent(tier)}/${encodeURIComponent(phone)}/history`,
            { headers: { 'X-Device-ID': _deviceId } }
        );
        const data = await r.json();
        const history = data.history || [];
        content.innerHTML = history.length
            ? history.map(day => `
                <div class="mb-6">
                    <p class="section-header mb-3">${_escHtml(day.date)}</p>
                    <div class="space-y-3">
                        ${day.messages.map(m => `
                            <div class="flex gap-3">
                                <span class="font-mono-custom text-[9px] text-zinc-600 uppercase w-14 shrink-0 mt-0.5">${_escHtml(m.role)}</span>
                                <p class="font-sans text-[12px] text-zinc-400 leading-relaxed">${_escHtml(m.text)}</p>
                            </div>`).join('')}
                    </div>
                </div>`).join('')
            : '<div class="font-sans text-[13px] text-zinc-600 py-4">No history found.</div>';
    } catch {
        content.innerHTML = '<div class="font-sans text-[13px] text-red-500/60 py-4">Failed to load history.</div>';
    }
    panel.scrollIntoView({ behavior: 'smooth' });
}

function closeUserHistory() {
    document.getElementById('user-history-panel')?.classList.add('hidden');
}

async function changeUserTier(jid, newTier) {
    if (!newTier) return;
    let adminConfirm = false;
    if (newTier === 'admin') {
        adminConfirm = await _confirmAdminGrant(jid);
        if (!adminConfirm) { loadUsers(); return; }
    }
    try {
        const r = await fetch('/api/admin/users/tier', {
            method: 'PATCH',
            headers: _authHeaders(),
            body: JSON.stringify({ jid, new_tier: newTier, admin_confirm: adminConfirm }),
        });
        const data = await r.json();
        if (data.status === 'ok') loadUsers();
    } catch {}
}

function _confirmAdminGrant(identifier) {
    return new Promise(resolve => {
        const modal  = document.getElementById('admin-confirm-modal');
        const input  = document.getElementById('admin-confirm-input');
        const btn    = document.getElementById('admin-confirm-btn');
        const cancel = document.getElementById('admin-confirm-cancel');
        const label  = document.getElementById('admin-confirm-label');
        if (!modal) { resolve(false); return; }
        if (label) label.textContent = identifier;
        if (input) input.value = '';
        if (btn)   { btn.disabled = true; btn.classList.add('opacity-40','cursor-not-allowed'); }
        modal.classList.remove('hidden');
        input?.focus();
        const onInput = () => {
            const ok = input?.value === 'GRANT ADMIN';
            if (btn) { btn.disabled = !ok; btn.classList.toggle('opacity-40', !ok); btn.classList.toggle('cursor-not-allowed', !ok); }
        };
        const onConfirm = () => { cleanup(); resolve(true); };
        const onCancel  = () => { cleanup(); resolve(false); };
        function cleanup() {
            modal.classList.add('hidden');
            input?.removeEventListener('input', onInput);
            btn?.removeEventListener('click', onConfirm);
            cancel?.removeEventListener('click', onCancel);
        }
        input?.addEventListener('input', onInput);
        btn?.addEventListener('click', onConfirm);
        cancel?.addEventListener('click', onCancel);
    });
}

async function registerUser() {
    const phoneEl = document.getElementById('new-user-phone');
    const tierEl  = document.getElementById('new-user-tier');
    const phone   = phoneEl?.value.trim();
    const tier    = tierEl?.value;
    if (!phone) { showStatus('user-register-status', 'Phone number required.', 'error'); return; }
    let adminConfirm = false;
    if (tier === 'admin') {
        adminConfirm = await _confirmAdminGrant(phone);
        if (!adminConfirm) return;
    }
    try {
        const r = await fetch('/api/admin/users', {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify({ phone, tier, admin_confirm: adminConfirm }),
        });
        const data = await r.json();
        if (data.status === 'ok') {
            if (phoneEl) phoneEl.value = '';
            showStatus('user-register-status', `Registered as ${tier}.`, 'success');
            loadUsers();
        } else {
            showStatus('user-register-status', data.detail || 'Error.', 'error');
        }
    } catch { showStatus('user-register-status', 'Network error.', 'error'); }
}

async function unregisterUser(jid, tier) {
    const phone = jid.split('@')[0];
    if (!await _wadeConfirm(`Remove ${jid} from registry?\n\nThey will revert to the strangers tier. History is preserved.`, { title: 'Unregister user' })) return;
    try {
        const r = await fetch(`/api/admin/users/${encodeURIComponent(tier)}/${encodeURIComponent(phone)}`, {
            method: 'DELETE',
            headers: _authHeaders(),
        });
        if (r.ok) loadUsers();
        else {
            const data = await r.json().catch(() => ({}));
            await _wadeAlert(data.detail || 'Failed to unregister.', { title: 'Error' });
        }
    } catch { await _wadeAlert('Network error.', { title: 'Error' }); }
}

// Device Management
async function loadDevices() {
    const el = document.getElementById('devices-list');
    if (!el) return;
    try {
        const r = await fetch('/api/admin/devices', { headers: { 'X-Device-ID': _deviceId } });
        if (r.status === 403) {
            el.innerHTML = '<div class="font-mono-custom text-[11px] text-zinc-500">Admin access required.</div>';
            return;
        }
        const data = await r.json();
        const devices = data.devices || [];
        const mine = devices.find(d => d.device_id === _deviceId);
        const myBadge = mine
            ? `<span class="sys-tag border-[#00ff66]/20 text-[#00ff66] bg-[#00ff66]/5 text-[9px]">THIS DEVICE · ${mine.tier.toUpperCase()}</span>`
            : `<span class="sys-tag border-zinc-700 text-zinc-500 text-[9px]">THIS DEVICE · UNREGISTERED</span>`;

        el.innerHTML = `
            <div class="module-card p-3 flex items-center gap-3 mb-3">
                <span class="font-mono-custom text-[10px] text-zinc-500 break-all flex-1">${_escHtml(_deviceId)}</span>
                <button onclick="navigator.clipboard.writeText('${_escHtml(_deviceId)}')" class="btn-ghost h-7 px-3 text-[10px] shrink-0">Copy</button>
                ${myBadge}
            </div>
            ${devices.filter(d => d.device_id !== _deviceId).map(d => `
                <div class="module-card p-3 flex items-center gap-3">
                    <span class="font-mono-custom text-[10px] text-zinc-500 break-all flex-1">${_escHtml(d.device_id)}</span>
                    <span class="sys-tag border-white/10 text-[9px] ${_TIER_COLORS[d.tier]||''}">${_escHtml(d.tier.toUpperCase())}</span>
                    <button onclick="removeDevice('${_escHtml(d.device_id)}')" class="btn-ghost h-7 px-3 text-[10px] text-red-500/60 hover:text-red-400 shrink-0">Remove</button>
                </div>`).join('') || (devices.length <= 1 ? '<div class="font-mono-custom text-[11px] text-zinc-600 py-2">No other registered devices.</div>' : '')}`;
    } catch {
        if (el) el.innerHTML = '<div class="font-mono-custom text-[11px] text-red-500/60">Failed to load devices.</div>';
    }
}

async function registerDevice() {
    const idEl   = document.getElementById('new-device-id');
    const tierEl = document.getElementById('new-device-tier');
    const did    = idEl?.value.trim();
    const tier   = tierEl?.value;
    if (!did) { showStatus('device-status', 'Device ID is required.', 'error'); return; }
    try {
        const r = await fetch('/api/admin/devices', {
            method: 'PUT',
            headers: _authHeaders(),
            body: JSON.stringify({ device_id: did, tier }),
        });
        const data = await r.json();
        if (data.status === 'ok') {
            if (idEl) idEl.value = '';
            showStatus('device-status', `Registered as ${tier}.`, 'success');
            loadDevices();
        } else {
            showStatus('device-status', data.detail || 'Error.', 'error');
        }
    } catch { showStatus('device-status', 'Network error.', 'error'); }
}

async function removeDevice(deviceId) {
    try {
        const r = await fetch(`/api/admin/devices/${encodeURIComponent(deviceId)}`, {
            method: 'DELETE',
            headers: _authHeaders(),
        });
        if (r.ok) loadDevices();
    } catch { /* silent */ }
}

// Skill Permissions
async function loadSkillPermissions() {
    const el = document.getElementById('skill-permissions-table');
    if (!el) return;
    try {
        const [permsRes, catsRes] = await Promise.all([
            fetch('/api/settings/tier-permissions'),
            fetch('/api/settings/skill-categories'),
        ]);
        const perms = (await permsRes.json()).permissions || {};
        const categories = (await catsRes.json()).categories || [];
        const TIERS = ['family', 'friends', 'guests', 'strangers'];

        if (!categories.length) {
            el.innerHTML = '<div class="font-mono-custom text-[11px] text-zinc-500">No skills loaded yet.</div>';
            return;
        }
        el.innerHTML = `
            <table class="w-full text-[11px] font-mono-custom">
                <thead>
                    <tr class="border-b border-white/5">
                        <th class="text-left py-2 pr-4 text-zinc-500 font-medium">Category</th>
                        ${TIERS.map(t => `<th class="text-center py-2 px-3 font-medium capitalize th-${t}">${t}</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
                    ${categories.map(cat => `
                        <tr class="border-b border-white/[0.03] hover:bg-white/[0.01]">
                            <td class="py-2 pr-4 text-zinc-300">${_escHtml(cat)}</td>
                            ${TIERS.map(t => {
                                const checked = (perms[t] || []).includes(cat) ? 'checked' : '';
                                return `<td class="text-center py-2 px-3"><input type="checkbox" id="perm-${_escHtml(t)}-${_escHtml(cat)}" ${checked} class="accent-[#00ff66] w-3.5 h-3.5 cursor-pointer"></td>`;
                            }).join('')}
                        </tr>`).join('')}
                </tbody>
            </table>`;
    } catch {
        if (el) el.innerHTML = '<div class="font-mono-custom text-[11px] text-red-500/60">Failed to load skill permissions.</div>';
    }
}

async function saveSkillPermissions() {
    const TIERS = ['family', 'friends', 'guests', 'strangers'];
    const permissions = {};
    TIERS.forEach(tier => {
        permissions[tier] = Array.from(
            document.querySelectorAll(`[id^="perm-${tier}-"]:checked`)
        ).map(el => el.id.slice(`perm-${tier}-`.length));
    });
    try {
        const r = await fetch('/api/settings/tier-permissions', {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify(permissions),
        });
        const data = await r.json();
        showStatus('permissions-status', data.status === 'success' ? 'Permissions saved.' : 'Error saving.', data.status);
    } catch {
        showStatus('permissions-status', 'Network error.', 'error');
    }
}

// Projects Registry
let _projectsList = [];

async function loadProjects() {
    const el = document.getElementById('projects-list');
    if (!el) return;
    try {
        const res = await fetch('/api/settings/projects');
        if (!res.ok) throw new Error();
        const data = await res.json();
        _projectsList = data.projects || [];
        _renderProjects();
    } catch {
        el.innerHTML = '<div class="font-mono-custom text-[11px] text-red-500/60">Failed to load projects.</div>';
    }
}

function _renderProjects() {
    const el = document.getElementById('projects-list');
    if (!el) return;
    if (!_projectsList.length) {
        el.innerHTML = '<div class="font-mono-custom text-[11px] text-zinc-600">No projects registered yet.</div>';
        return;
    }
    el.innerHTML = _projectsList.map((p, i) => `
        <div class="flex items-start gap-3 p-3 bg-void rounded-lg border border-white/5 group">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-1">
                    <span class="font-mono-custom text-[12px] text-white font-medium">${_escHtml(p.name || 'Unnamed')}</span>
                    <span class="sys-tag text-[9px]">${_escHtml(p.status || 'Active')}</span>
                </div>
                ${p.path    ? `<div class="font-mono-custom text-[11px] text-zinc-500 truncate">${_escHtml(p.path)}</div>` : ''}
                ${p.purpose ? `<div class="text-[11px] text-zinc-400 mt-1 leading-relaxed">${_escHtml(p.purpose)}</div>` : ''}
                ${p.stack   ? `<div class="font-mono-custom text-[10px] text-zinc-600 mt-0.5">${_escHtml(p.stack)}</div>` : ''}
            </div>
            <button onclick="removeProject(${i})" class="opacity-0 group-hover:opacity-100 transition-opacity text-zinc-600 hover:text-red-400 p-1 shrink-0" title="Remove">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
        </div>`).join('');
}

function addProject() {
    const name    = document.getElementById('proj-name')?.value.trim();
    const path    = document.getElementById('proj-path')?.value.trim();
    const stack   = document.getElementById('proj-stack')?.value.trim();
    const purpose = document.getElementById('proj-purpose')?.value.trim();
    const status  = document.getElementById('proj-status')?.value || 'Active';
    const notes   = document.getElementById('proj-notes')?.value.trim();
    if (!name) { showStatus('projects-status', 'Name is required.', 'error'); return; }
    _projectsList.push({ name, path, stack, purpose, status, notes });
    _renderProjects();
    ['proj-name', 'proj-path', 'proj-stack', 'proj-purpose', 'proj-notes'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
}

function removeProject(idx) {
    _projectsList.splice(idx, 1);
    _renderProjects();
}

async function saveProjects() {
    try {
        const res = await fetch('/api/settings/projects', {
            method:  'POST',
            headers: _authHeaders(),
            body:    JSON.stringify({ projects: _projectsList }),
        });
        const data = await res.json();
        showStatus('projects-status', data.status === 'success' ? 'Projects saved.' : 'Error saving.', data.status === 'success' ? 'success' : 'error');
    } catch {
        showStatus('projects-status', 'Network error.', 'error');
    }
}

// Workspace File Editor
async function loadWorkspaceFile() {
    const select   = document.getElementById('workspace-file-select');
    const textarea = document.getElementById('workspace-file-content');
    if (!select || !textarea) return;
    const name = select.value;
    if (!name) return;
    textarea.value = '';
    textarea.placeholder = 'Loading...';
    try {
        const res  = await fetch(`/api/workspace/file?name=${encodeURIComponent(name)}`);
        const data = await res.json();
        textarea.value = data.content || '';
        textarea.placeholder = '';
    } catch {
        textarea.placeholder = 'Failed to load file.';
    }
}

async function saveWorkspaceFile() {
    const select   = document.getElementById('workspace-file-select');
    const textarea = document.getElementById('workspace-file-content');
    if (!select || !textarea) return;
    try {
        const res  = await fetch('/api/workspace/file', {
            method:  'POST',
            headers: _authHeaders(),
            body:    JSON.stringify({ name: select.value, content: textarea.value }),
        });
        const data = await res.json();
        showStatus('workspace-file-status', data.status === 'success' ? 'Saved.' : 'Error saving.', data.status === 'success' ? 'success' : 'error');
    } catch {
        showStatus('workspace-file-status', 'Network error.', 'error');
    }
}

async function showQRModal() {
    await _ensureQRCode();
    const modal     = document.getElementById('qr-modal');
    const container = document.getElementById('qr-code-container');
    const urlEl     = document.getElementById('qr-url-text');
    if (!modal) return;

    container.innerHTML = '';

    let host    = window.location.hostname;
    let portStr = window.location.port;

    if (host === 'localhost' || host === '127.0.0.1') {
        try {
            const data = await fetch('/health').then(r => r.json());
            if (data.lan_ip && data.lan_ip !== '127.0.0.1') {
                host    = data.lan_ip;
                portStr = (data.port && data.port !== 80) ? String(data.port) : '';
            }
        } catch {}
    }

    const portPart = portStr && portStr !== '80' ? ':' + portStr : '';
    const url = `http://${host}${portPart}/ui`;

    if (urlEl) urlEl.textContent = url;

    new QRCode(container, {
        text:         url,
        width:        200,
        height:       200,
        colorDark:    '#ffffff',
        colorLight:   '#0a0a0a',
        correctLevel: QRCode.CorrectLevel.M,
    });

    modal.classList.remove('hidden');
}

function hideQRModal() {
    document.getElementById('qr-modal')?.classList.add('hidden');
}

// God Mode HUD
function toggleGodMode() {
    _godModeOpen = !_godModeOpen;
    const panel = document.getElementById('god-mode-panel');
    const btn   = document.getElementById('god-mode-btn');

    if (_godModeOpen) {
        panel.classList.add('gm-open');
        panel.setAttribute('aria-hidden', 'false');
        btn.classList.add('gm-active');
        _fetchGodModeData();
        _godModeInterval = setInterval(_fetchGodModeData, 3000);
    } else {
        panel.classList.remove('gm-open');
        panel.setAttribute('aria-hidden', 'true');
        btn.classList.remove('gm-active');
        clearInterval(_godModeInterval);
        _godModeInterval = null;
    }
}

async function _fetchGodModeData() {
    if (_godModePending) return;
    _godModePending = true;
    try {
        const mRes = await fetch('/api/godmode/metrics/live', { headers: _authHeaders() });
        if (mRes.ok) {
            _godModeMetrics = await mRes.json();
        }
        if (_godModeTaskId) {
            const tRes = await fetch(`/api/godmode/traces/${_godModeTaskId}`, { headers: _authHeaders() });
            if (tRes.ok) {
                _godModeTraces = await tRes.json();
            } else if (tRes.status === 404) {
                _godModeTraces = null;
            }
        }
        _scheduleGodModeRender();
    } catch (_) {
        // Network error - HUD stays with last known data
    } finally {
        _godModePending = false;
    }
}

function _scheduleGodModeRender() {
    if (_godModeRafId !== null) return;
    _godModeRafId = requestAnimationFrame(() => {
        _godModeRafId = null;
        _renderGodModeAll();
    });
}

function _renderGodModeAll() {
    _renderTaskGraph();
    _renderCriticConsole();
    _renderPerfTape();
}

function _renderTaskGraph() {
    const el = document.getElementById('gm-task-graph');
    const labelEl = document.getElementById('gm-task-id-label');
    if (!el) return;

    if (!_godModeTraces) {
        el.innerHTML = '<span class="gm-empty-state">No task selected</span>';
        if (labelEl) labelEl.textContent = '';
        return;
    }

    const { task, subtasks } = _godModeTraces;
    if (labelEl) labelEl.textContent = task.id.slice(0, 8) + '…';

    const STATUS_COLORS = {
        completed:          '#00ff66',
        failed:             '#ef4444',
        goal_not_satisfied: '#ef4444',
        invalid_plan:       '#ef4444',
        in_progress:        '#fbbf24',
        planning:           '#fbbf24',
        pending:            '#52525b',
        cancelled:          '#52525b',
        tool_mismatch:      '#f97316',
    };

    function nodeHtml(t, indent) {
        const color = STATUS_COLORS[t.status] || '#52525b';
        const pad   = indent ? 'padding-left:12px;border-left:1px solid rgba(255,255,255,0.06);margin-left:3px;' : '';
        const traceCount = (t.traces || []).length;
        const traceBadge = traceCount > 0
            ? `<span style="color:#3f3f46;font-size:8px;">${traceCount} tool${traceCount !== 1 ? 's' : ''}</span>`
            : '';
        const goal = t.goal ?? '';
        return `
        <div class="gm-task-node" style="${pad}">
            <span class="gm-dot" style="background:${color};"></span>
            <span class="gm-node-goal">${_escHtml(goal.slice(0, 80))}${goal.length > 80 ? '…' : ''}</span>
            ${traceBadge}
            <span class="gm-node-status" style="color:${color};">${_escHtml(t.status)}</span>
        </div>`;
    }

    let html = nodeHtml(task, false);
    for (const st of (subtasks || [])) {
        html += nodeHtml(st, true);
    }
    el.innerHTML = html;
}

function _renderCriticConsole() {
    const el      = document.getElementById('gm-critic-console');
    const countEl = document.getElementById('gm-verdict-count');
    if (!el) return;

    const verdicts = [];
    if (_godModeTraces) {
        verdicts.push(...(_godModeTraces.root_verdicts || []));
        for (const st of (_godModeTraces.subtasks || [])) {
            verdicts.push(...(st.verdicts || []));
        }
    }

    if (countEl) countEl.textContent = verdicts.length ? `${verdicts.length} verdict${verdicts.length !== 1 ? 's' : ''}` : '';

    if (verdicts.length === 0) {
        el.innerHTML = '<span class="gm-empty-state">Awaiting verdicts…</span>';
        return;
    }

    const STATUS_LABEL = { ok: '✓ OK', revise: '↻ REVISE', suspect: '⚠ SUSPECT', blocked: '✗ BLOCKED' };
    const STATUS_COLOR = { ok: '#00ff66', revise: '#fbbf24', suspect: '#f97316', blocked: '#ef4444' };

    let html = '';
    for (const v of verdicts.slice(-20)) {
        const label = STATUS_LABEL[v.status] || _escHtml(String(v.status ?? 'unknown').toUpperCase());
        const color = STATUS_COLOR[v.status] || '#71717a';
        const conf  = typeof v.confidence === 'number' ? `${(v.confidence * 100).toFixed(0)}%` : '';
        const type  = v.check_type ? `<span style="color:#3f3f46;">[${_escHtml(v.check_type)}]</span>` : '';
        const reason = v.reason ? `<div class="gm-verdict-reason">${_escHtml(v.reason.slice(0, 120))}${v.reason.length > 120 ? '…' : ''}</div>` : '';
        html += `
        <div class="gm-verdict status-${_escHtml(v.status)}">
            <div class="gm-verdict-header">
                <span style="color:${color};">${label}</span>
                <span style="color:#52525b;">${conf}</span>
                ${type}
                <span style="color:#3f3f46;margin-left:auto;">${_escHtml(_timeAgo(v.created_at))}</span>
            </div>
            ${reason}
        </div>`;
    }
    el.innerHTML = html;
    el.scrollTop = el.scrollHeight;
}

function _renderPerfTape() {
    const tapeEl    = document.getElementById('gm-perf-tape');
    const summaryEl = document.getElementById('gm-role-summary');
    const totalEl   = document.getElementById('gm-total-tokens');
    if (!tapeEl) return;

    if (!_godModeMetrics) {
        tapeEl.innerHTML = '<span class="gm-empty-state">Waiting for inference…</span>';
        return;
    }

    const { by_role, recent, totals } = _godModeMetrics;

    if (totalEl) {
        const tot = (totals.prompt_tokens || 0) + (totals.completion_tokens || 0);
        totalEl.textContent = tot > 0 ? `${tot.toLocaleString()} tok` : '';
    }

    if (summaryEl) {
        if (Object.keys(by_role).length === 0) {
            summaryEl.innerHTML = '';
        } else {
            summaryEl.innerHTML = Object.entries(by_role)
                .map(([role, d]) => `
                    <div class="gm-role-badge">
                        <span>${_escHtml(role)}</span>
                        <span class="gm-badge-count">${_escHtml(String(d.call_count ?? ''))}×</span>
                        <span>${Math.round(d.avg_latency_ms ?? 0)}ms</span>
                    </div>`)
                .join('');
        }
    }

    if (recent.length === 0) {
        tapeEl.innerHTML = '<span class="gm-empty-state">Waiting for inference…</span>';
        return;
    }
    const wasAtBottom = tapeEl.scrollHeight - tapeEl.scrollTop <= tapeEl.clientHeight + 4;
    tapeEl.innerHTML = recent.map(r => `
        <div class="gm-tape-entry">
            <span class="gm-role">${_escHtml(r.role)}</span>
            <span class="gm-model">${_escHtml((r.model || '').split(':')[0])}</span>
            <span class="gm-tokens">${((r.prompt_tokens || 0) + (r.completion_tokens || 0)).toLocaleString()}t</span>
            <span class="gm-lat">${_escHtml(String(r.latency_ms ?? ''))}ms</span>
        </div>`).join('');
    if (wasAtBottom) tapeEl.scrollTop = tapeEl.scrollHeight;
}