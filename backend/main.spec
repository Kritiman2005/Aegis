# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

# ── Hidden imports ────────────────────────────────────────────────────────────
# NOTE: easyocr, torch, torchvision, torchaudio are excluded — they are lazy-
# imported at runtime when OCR is first used, not at startup.
hidden_imports = (
    collect_submodules('app') +
    collect_submodules('uvicorn') +
    collect_submodules('fastapi') +
    collect_submodules('pydantic') +
    collect_submodules('sqlalchemy') +
    collect_submodules('qdrant_client') +
    collect_submodules('fastembed') +
    collect_submodules('sentence_transformers') +
    collect_submodules('llama_cpp') +
    collect_submodules('fitz') +
    ['passlib.handlers.bcrypt', 'multipart']
)

datas = (
    collect_data_files('fastembed') +
    collect_data_files('sentence_transformers') +
    # certifi's cacert.pem so verify=certifi.where() in models_hub.py resolves
    # to a real bundled CA file instead of a path that doesn't exist in the
    # packaged app (the original cause of the Windows SSL failures this used
    # to work around by disabling verification entirely).
    collect_data_files('certifi')
)

# llama-cpp-python loads its actual inference engine as a compiled shared
# library (llama.dll / libllama.dylib / libllama.so, plus ggml backends) via
# ctypes at runtime — PyInstaller's static import-graph analysis can't see
# that, so it must be collected explicitly or the packaged app fails the
# moment a model is loaded (health checks still pass since they never touch
# llama_cpp). Dev mode never shows this because the venv keeps the native
# libs sitting right next to the .py files.
binaries = collect_dynamic_libs('llama_cpp')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'PyQt5', 'PySide2',
        # easyocr/torch are lazy-imported at runtime — never bundle them
        'easyocr', 'torch', 'torchvision', 'torchaudio',
        # dev-only tools
        'pytest', 'setuptools', 'distutils',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ── EXE: just the launcher, NOT the full onefile bundle ───────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],               # empty — binaries/datas go into COLLECT below
    exclude_binaries=True,
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,        # DISABLED — UPX-packed binaries trigger Windows Defender
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ── COLLECT: output goes to dist/main/ folder (--onedir mode) ────────────────
# No runtime extraction to %TEMP% → no Defender scan on launch → instant startup
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='main',      # output: backend/dist/main/main.exe  (+ all DLLs alongside)
)
