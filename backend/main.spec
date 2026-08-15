# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# Collect all hidden imports for FastAPI, Uvicorn, and AI dependencies
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
    excludes=['tkinter', 'PyQt5', 'PySide2'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
