# wade.spec
# PyInstaller spec for bundling W.A.D.E.'s FastAPI backend as a Tauri sidecar.
# Run: pyinstaller wade.spec
# Output: dist/wade_server[.exe] — a self-contained binary with no Python requirement.

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["app/main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=[
        ("app/static", "app/static"),
        ("app/templates", "app/templates"),
        ("config.defaults.yaml", "."),
    ],
    hiddenimports=[
        "app.api.v1.whatsapp",
        "app.skills.registry",
        "app.skills.python.runner",
        "app.skills.web.browser",
        "app.skills.web.web_search",
        "app.skills.workspace.files",
        "app.skills.finance",
        "app.skills.math",
        "app.skills.weather",
        "app.skills.vision",
        "app.skills.scheduling.scheduler",
        "app.skills.system.diagnostics",
        "app.memory.manager",
        "app.memory.episodes",
        "app.memory.semantic_memory",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["llama_cpp", "torch", "transformers", "sentence_transformers"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="wade_server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
