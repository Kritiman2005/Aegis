# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

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

datas = collect_data_files('fastembed') + collect_data_files('sentence_transformers')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
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
