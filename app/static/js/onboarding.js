(async function initOnboarding() {
  const overlay = document.getElementById("onboarding-overlay");
  if (!overlay) return;

  let status;
  try {
    const res = await fetch("/api/onboarding/status");
    status = await res.json();
  } catch (e) {
    return;
  }

  if (!status.needs_onboarding && status.name_set) return;

  overlay.style.display = "flex";

  // Screen 1: Ollama not installed
  if (!status.ollama_ready) {
    showScreen("ob-screen-ollama");
    document.getElementById("ob-ollama-check").addEventListener("click", async () => {
      const check = await fetch("/api/onboarding/status");
      const re = await check.json();
      if (re.ollama_ready) {
        showScreen("ob-screen-name");
      } else {
        document.getElementById("ob-ollama-check").textContent = "Ollama not detected yet — try again";
      }
    });
    return;
  }

  // Screen 2: Name
  showScreen("ob-screen-name");

  let _csrfToken = "";

  async function submitName() {
    const name = document.getElementById("ob-name-input").value.trim();
    if (!name) return;

    try {
      const tokenRes = await fetch("/api/csrf-token");
      _csrfToken = (await tokenRes.json()).token || "";
    } catch (_) {}

    try {
      await fetch("/api/onboarding/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-WADE-Token": _csrfToken },
        body: JSON.stringify({ name }),
      });
    } catch (_) {}

    const chatInput = document.getElementById("chat-input") || document.querySelector("input[type=text]");
    if (chatInput) chatInput.placeholder = `Ask W.A.D.E. anything, ${name}...`;

    showScreen("ob-screen-credentials");
  }

  document.getElementById("ob-name-submit").addEventListener("click", submitName);
  document.getElementById("ob-name-input").addEventListener("keydown", e => {
    if (e.key === "Enter") submitName();
  });

  // Screen 3: Credentials
  const _ONBOARDING_CREDS = [
    { service: "openai",    fields: { api_key:       "ob-cred-openai-api_key" } },
    { service: "anthropic", fields: { api_key:       "ob-cred-anthropic-api_key" } },
    { service: "gemini",    fields: { api_key:       "ob-cred-gemini-api_key" } },
    { service: "notion",    fields: { token:         "ob-cred-notion-token" } },
    { service: "spotify",   fields: {
        client_id:     "ob-cred-spotify-client_id",
        client_secret: "ob-cred-spotify-client_secret",
    }},
    { service: "blink",    fields: {
        email:    "ob-cred-blink-email",
        password: "ob-cred-blink-password",
    }},
  ];

  async function saveAndFinish() {
    const btn = document.getElementById("ob-creds-save");
    if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }

    for (const { service, fields } of _ONBOARDING_CREDS) {
      const data = {};
      for (const [key, inputId] of Object.entries(fields)) {
        const el = document.getElementById(inputId);
        if (el && el.value.trim()) data[key] = el.value.trim();
      }
      if (Object.keys(data).length === 0) continue;
      try {
        await fetch(`/api/credentials/${service}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json", "X-WADE-Token": _csrfToken },
          body:    JSON.stringify(data),
        });
      } catch (_) {}
    }

    overlay.style.display = "none";
  }

  document.getElementById("ob-creds-save").addEventListener("click", saveAndFinish);
  document.getElementById("ob-creds-skip").addEventListener("click", () => {
    overlay.style.display = "none";
  });

  // Blink 2FA helpers
  window.obBlinkLogin = async function () {
    const emailEl = document.getElementById("ob-cred-blink-email");
    const passEl  = document.getElementById("ob-cred-blink-password");
    const btn     = document.getElementById("ob-blink-login-btn");
    const statusEl = document.getElementById("ob-blink-status");

    if (!emailEl?.value.trim() || !passEl?.value.trim()) {
      _obBlinkStatus("Enter your Blink email and password first.", "error");
      return;
    }

    if (btn) { btn.textContent = "Sending…"; btn.disabled = true; }

    try {
      await fetch("/api/credentials/blink", {
        method:  "POST",
        headers: { "Content-Type": "application/json", "X-WADE-Token": _csrfToken },
        body:    JSON.stringify({ email: emailEl.value.trim(), password: passEl.value.trim() }),
      });
    } catch (_) {}

    try {
      const r    = await fetch("/api/blink/login", { method: "POST", headers: { "X-WADE-Token": _csrfToken } });
      const data = await r.json();

      if (!r.ok) {
        _obBlinkStatus(data.detail || "Login failed.", "error");
      } else if (data.needs_2fa) {
        document.getElementById("ob-blink-pin-row").style.display = "flex";
        _obBlinkStatus("Code sent — check your texts.", "info");
      } else {
        document.getElementById("ob-blink-pin-row").style.display = "none";
        _obBlinkStatus("Connected!", "ok");
      }
    } catch (_) {
      _obBlinkStatus("Request failed.", "error");
    } finally {
      if (btn) { btn.textContent = "Login"; btn.disabled = false; }
    }
  };

  window.obBlinkVerify = async function () {
    const pinEl = document.getElementById("ob-cred-blink-pin");
    const pin   = pinEl ? pinEl.value.trim() : "";
    if (!pin) { _obBlinkStatus("Enter the 6-digit code.", "error"); return; }

    try {
      const r    = await fetch("/api/blink/verify", {
        method:  "POST",
        headers: { "Content-Type": "application/json", "X-WADE-Token": _csrfToken },
        body:    JSON.stringify({ pin }),
      });
      const data = await r.json();
      if (data.ok) {
        document.getElementById("ob-blink-pin-row").style.display = "none";
        _obBlinkStatus("Connected!", "ok");
      } else {
        _obBlinkStatus(data.message || "Invalid code.", "error");
      }
    } catch (_) {
      _obBlinkStatus("Request failed.", "error");
    }
  };

  window.obBlinkCancelPin = function () {
    document.getElementById("ob-blink-pin-row").style.display = "none";
    const statusEl = document.getElementById("ob-blink-status");
    if (statusEl) statusEl.style.display = "none";
  };

  function _obBlinkStatus(msg, type) {
    const el = document.getElementById("ob-blink-status");
    if (!el) return;
    const colors = { ok: "#4ade80", error: "#f87171", info: "#facc15" };
    el.textContent  = msg;
    el.style.color  = colors[type] || "#a1a1aa";
    el.style.display = "block";
  }
})();

function showScreen(id) {
  document.querySelectorAll(".ob-screen").forEach(s => s.style.display = "none");
  const screen = document.getElementById(id);
  if (screen) screen.style.display = "flex";
}
