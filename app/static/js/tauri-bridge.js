(function () {
    'use strict';

    if (!window.__TAURI__) return;

    const invoke = window.__TAURI__.core.invoke;

    function tray(state) {
        invoke('update_tray', { state }).catch(err =>
            console.warn('[tauri-bridge] update_tray failed:', err)
        );
    }

    function notify(title, body) {
        invoke('show_notification', { title, body }).catch(err =>
            console.warn('[tauri-bridge] show_notification failed:', err)
        );
    }

    window.addEventListener('wade:chat:start', () => tray('thinking'));
    window.addEventListener('wade:chat:end',   () => tray('idle'));
    window.addEventListener('wade:focus',      () => tray('idle'));
    window.addEventListener('focus', () => tray('idle'));
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) tray('idle');
    });

    function startBridgeSse() {
        function connect() {
            const es = new EventSource('/api/events');
            es.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data);
                    if (data.type === 'proactive_message' && data.content) {
                        tray('attention');
                        const body = data.content.length > 200
                            ? data.content.slice(0, 197) + '...'
                            : data.content;
                        notify('W.A.D.E.', body);
                    }
                } catch (_) { /* malformed SSE frame - ignore */ }
            };
            es.onerror = () => { es.close(); setTimeout(connect, 5000); };
        }
        setTimeout(connect, 3500);
    }

    startBridgeSse();
})();
