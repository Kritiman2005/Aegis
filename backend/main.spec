# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

# ── Hidden imports ────────────────────────────────────────────────────────────
# NOTE: torch, torchvision, torchaudio are excluded — they are lazy-imported
# at runtime by the embedding/reranker models, not at startup.
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
    collect_submodules('gguf') +
    collect_submodules('playwright') +
    collect_submodules('trafilatura') +
    collect_submodules('dateparser') +
    # All of keyring's own OS-backend submodules (macOS/Windows/SecretService/
    # kwallet/libsecret) — keyring's own runtime OS-detection picks whichever
    # one actually works, this just needs all of them present in the bundle.
    # Each backend's own OS-specific import (e.g. Windows.py's win32ctypes)
    # is a plain top-level import PyInstaller's analysis finds on its own
    # when building on that OS — nothing extra needed here for those.
    collect_submodules('keyring') +
    # Voice transcription (app/core/transcription.py). faster_whisper itself
    # is pure Python; ctranslate2/onnxruntime/av/tokenizers are the actual
    # native-extension packages — their .so/.dylib files are handled below
    # via collect_dynamic_libs, same split as llama_cpp's binaries/hiddenimports.
    collect_submodules('faster_whisper') +
    collect_submodules('ctranslate2') +
    collect_submodules('tokenizers') +
    ['passlib.handlers.bcrypt', 'multipart']
)

datas = (
    collect_data_files('fastembed') +
    collect_data_files('sentence_transformers') +
    # certifi's cacert.pem so verify=certifi.where() in models_hub.py resolves
    # to a real bundled CA file instead of a path that doesn't exist in the
    # packaged app (the original cause of the Windows SSL failures this used
    # to work around by disabling verification entirely).
    collect_data_files('certifi') +
    # playwright ships its own official PyInstaller hook (auto-discovered via
    # the pyinstaller40 entry point) which already does this same
    # collect_data_files('playwright') call — listed explicitly here anyway
    # to match this file's style and make the dependency visible. This pulls
    # in playwright/driver/ (its Node-based protocol driver), NOT a browser —
    # Chromium itself is never bundled, see app/core/scraper.py.
    collect_data_files('playwright') +
    # trafilatura ships its own settings.cfg (extraction thresholds like
    # min_extracted_size) that it reads via configparser at runtime — without
    # this it's not a silent fallback, extract() raises outright in a frozen
    # build (confirmed against a real packaged binary).
    collect_data_files('trafilatura') +
    # justext (one of trafilatura's extraction backends) ships stoplist text
    # files per language, read straight off disk at runtime — same story.
    collect_data_files('justext') +
    # trafilatura's date-detection path (via htmldate) uses dateparser, which
    # needs its locale/data files collected explicitly or it silently returns
    # empty results in a frozen build.
    collect_data_files('dateparser') +
    collect_data_files('babel') +
    # scraper_driver.js / browser_driver.js are plain data files (not .py
    # modules), so PyInstaller's import-graph analysis can't discover them
    # on its own — must be listed here explicitly. See
    # app/core/scraper.py::_run_driver_script and app/core/browser_session.py
    # for why browser automation runs through these Node scripts rather than
    # Playwright's own Python API.
    [
        ('app/core/scraper_driver.js', 'app/core'),
        ('app/core/browser_driver.js', 'app/core'),
    ] +
    # Bundled Whisper model (see app/core/transcription.py) — downloaded by
    # scripts/download_whisper_model.py, which `npm run build:python` runs
    # right before this spec, so the directory is guaranteed to exist by
    # the time Analysis runs. Enumerated file-by-file (glob, not a bare
    # directory path) since PyInstaller's datas directory-vs-file handling
    # has been a repeated source of surprises in this same file already.
    # Errors loudly instead of silently shipping a voice-less build if the
    # predownload step didn't run.
    #
    # huggingface_hub's cache layout stores the actual weight files once
    # under blobs/<hash> and reaches them everywhere else (snapshots/<rev>/*)
    # via symlinks (on macOS/Linux — Windows without symlink support just
    # writes real files directly, so blobs/ won't even exist there). Since
    # PyInstaller's datas copy follows symlinks and materializes real bytes
    # at the destination, including both blobs/ and snapshots/ would bundle
    # the same ~460MB weight file twice. snapshots/ + refs/ alone still
    # resolve correctly under local_files_only=True (faster-whisper only
    # ever reads from the snapshot path), so blobs/ (and the download-time
    # .locks/ dir, never read at runtime) are skipped here.
    [
        (str(p), str(Path('app/whisper_bundled') / p.relative_to('app/whisper_bundled').parent))
        for p in Path('app/whisper_bundled').rglob('*')
        if p.is_file() and 'blobs' not in p.parts and '.locks' not in p.parts
    ]
)

if not Path('app/whisper_bundled').is_dir() or not any(Path('app/whisper_bundled').rglob('*')):
    raise SystemExit(
        "app/whisper_bundled/ is missing or empty — run "
        "`python scripts/download_whisper_model.py` before pyinstaller "
        "(npm run build:python does this automatically)."
    )

# llama-cpp-python loads its actual inference engine as a compiled shared
# library (llama.dll / libllama.dylib / libllama.so, plus ggml backends) via
# ctypes at runtime — PyInstaller's static import-graph analysis can't see
# that, so it must be collected explicitly or the packaged app fails the
# moment a model is loaded (health checks still pass since they never touch
# llama_cpp). Dev mode never shows this because the venv keeps the native
# libs sitting right next to the .py files.
#
# ctranslate2 (Whisper's inference engine) and av (audio decoding) are wheel-
# vendored the same delocate/auditwheel way — a package-local .dylibs/ folder
# of shared libs (FFmpeg's libav*, libctranslate2 itself) loaded via rpath
# from a .so, invisible to static analysis. onnxruntime keeps its .dylib
# alongside its extension module in capi/ instead of a .dylibs folder, but
# is the same "native lib PyInstaller's import graph can't see" story.
binaries = (
    collect_dynamic_libs('llama_cpp') +
    collect_dynamic_libs('ctranslate2') +
    collect_dynamic_libs('av') +
    collect_dynamic_libs('onnxruntime') +
    collect_dynamic_libs('tokenizers')
)

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
        # torch is lazy-imported at runtime — never bundle it
        'torch', 'torchvision', 'torchaudio',
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
