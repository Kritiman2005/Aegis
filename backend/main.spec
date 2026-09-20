# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs, copy_metadata

# ── Hidden imports ────────────────────────────────────────────────────────────
# NOTE on torch/torchvision/transformers/sentence_transformers/easyocr/
# rapidocr_onnxruntime/cv2: NONE of these are bundled — all excluded below
# (see Analysis()'s excludes list). Only the Dependencies panel's pip
# install (app.core.optional_deps) and the Marketplace's reranker/
# embedding/OCR download flows ever touch them, all through lazy,
# function-local imports (never at module load), so their absence doesn't
# break app startup — a user who wants a reranker, a sentence-transformers
# embedding backend, or EasyOCR installs the package first (Dependencies
# panel, or the panel prompts them to when a feature's own
# ModuleNotFoundError surfaces via app.core.friendly_errors), same as any
# other optional package.
#
# History, for whoever next touches this: an EARLIER attempt at exactly
# this — torch as an opt-in runtime download — was tried and reverted
# after a real, multi-layered PyInstaller + Hugging-Face-ecosystem
# compatibility problem: transformers'/huggingface_hub's own internal lazy
# module resolution broke in confusing ways under a PARTIAL split, where
# torch was external but transformers/huggingface_hub stayed bundled. This
# time the WHOLE interdependent group (torch, torchvision, transformers,
# sentence_transformers, easyocr, rapidocr_onnxruntime, cv2) is excluded
# together, not partially — huggingface_hub itself stays bundled (it's
# cheap, ~5MB, pure infrastructure the GGUF model downloader also needs,
# and isn't part of what broke last time), but nothing that transitively
# needs torch does. If this breaks again, it was worth knowing that a full
# clean split still doesn't dodge whatever the underlying issue actually
# was — don't re-attempt a partial split as the "fix".
#
# pip itself is ALSO bundled — not for torch (unnecessary now), but for
# the Dependencies panel's own general-purpose "install any package"
# feature (app.api.optional_deps, app.core.optional_deps) — letting a user
# pip-install some future, unanticipated dependency directly from the app,
# into AEGIS_DATA_DIR, without needing a whole new Aegis release for it.
# Calling pip's own programmatic entry point (pip._internal.cli.main.main)
# in-process is the standard workaround for this class of app: a
# subprocess-based `sys.executable -m pip install ...` doesn't work here
# the way it would in a normal Python install, since this frozen app's own
# exe isn't a general-purpose `python -m X` entry point.
hidden_imports = (
    collect_submodules('pip') +
    collect_submodules('app') +
    collect_submodules('uvicorn') +
    collect_submodules('fastapi') +
    collect_submodules('pydantic') +
    collect_submodules('sqlalchemy') +
    collect_submodules('qdrant_client') +
    collect_submodules('fastembed') +
    # huggingface_hub itself stays bundled — cheap (~5MB), pure
    # infrastructure the GGUF model downloader (app.core.model_catalog)
    # also depends on, unrelated to the excluded torch/transformers/
    # sentence_transformers group above.
    collect_submodules('huggingface_hub') +
    collect_submodules('llama_cpp') +
    # app.core.exporter's PDF export (generating a file FROM a chat
    # reply) — unrelated to the removed document text-extraction, which
    # used to be this package's only other consumer in this app.
    collect_submodules('fitz') +
    collect_submodules('gguf') +
    # All of keyring's own OS-backend submodules (macOS/Windows/SecretService/
    # kwallet/libsecret) — keyring's own runtime OS-detection picks whichever
    # one actually works, this just needs all of them present in the bundle.
    # Each backend's own OS-specific import (e.g. Windows.py's win32ctypes)
    # is a plain top-level import PyInstaller's analysis finds on its own
    # when building on that OS — nothing extra needed here for those.
    collect_submodules('keyring') +
    # Same "OS-specific backend selected at runtime" shape as keyring above —
    # truststore (main.py's own module docstring note on it) dispatches to
    # macOS/Windows/OpenSSL-specific submodules based on sys.platform.
    collect_submodules('truststore') +
    # Voice transcription (app/core/transcription.py). faster_whisper itself
    # is pure Python; ctranslate2/onnxruntime/av/tokenizers are the actual
    # native-extension packages — their .so/.dylib files are handled below
    # via collect_dynamic_libs, same split as llama_cpp's binaries/hiddenimports.
    collect_submodules('faster_whisper') +
    collect_submodules('ctranslate2') +
    collect_submodules('tokenizers') +
    # Google Workspace MCP server (app/mcp/servers/google_mcp_server.py) —
    # lazy-imported inside a function, well past where main.py dispatches
    # to it (see main.py's mcp_google branch), so PyInstaller's static
    # analysis of the entry script alone won't necessarily walk into it.
    collect_submodules('googleapiclient') +
    collect_submodules('google_auth_httplib2') +
    collect_submodules('google.auth') +
    collect_submodules('google.oauth2') +
    collect_submodules('google_auth_oauthlib') +
    collect_submodules('httplib2') +
    collect_submodules('uritemplate') +
    # PyInstaller's own bundled runtime hook (pyi_rth_pkgres.py) fully
    # exercises pkg_resources at startup — pkg_resources' own optional use
    # of jaraco.text (a normally-optional soft dependency it catches with
    # its own try/except when unfrozen) isn't optional once that hook runs
    # it unconditionally, so a frozen build crashes on launch with
    # "ModuleNotFoundError: No module named 'jaraco.text'" if it isn't
    # bundled too — confirmed by actually launching the frozen binary, not
    # just trusting the build log. pkg_resources itself gets pulled in
    # transitively by several of the packages above (easyocr and others
    # use it for version checks), not anything this app imports directly.
    ['jaraco.text', 'jaraco.functools', 'jaraco.context', 'jaraco.classes'] +
    ['passlib.handlers.bcrypt', 'multipart']
)

datas = (
    collect_data_files('fastembed') +
    collect_data_files('huggingface_hub') +
    # copy_metadata (installed-package .dist-info — importlib.metadata's
    # data, NOT the actual code collect_data_files/collect_submodules
    # already handle) for huggingface_hub and its own runtime dependencies
    # — its internal capability-detection code depends on being able to
    # read its own installed-package metadata, which a frozen build
    # doesn't carry unless explicitly told to via copy_metadata. Confirmed
    # directly this matters (a real "cannot import name 'is_offline_mode'"
    # error) during the external-bundle experiment; kept here as cheap
    # insurance now that everything's bundled together normally, since the
    # Dependencies panel's own "install any package" feature could still
    # pull in something that exercises this same code path.
    copy_metadata('huggingface_hub') +
    copy_metadata('filelock') +
    copy_metadata('fsspec') +
    copy_metadata('packaging') +
    copy_metadata('pyyaml') +
    copy_metadata('requests') +
    copy_metadata('tqdm') +
    copy_metadata('typing-extensions') +
    # certifi's cacert.pem so verify=certifi.where() in models_hub.py resolves
    # to a real bundled CA file instead of a path that doesn't exist in the
    # packaged app (the original cause of the Windows SSL failures this used
    # to work around by disabling verification entirely).
    collect_data_files('certifi') +
    # pip vendors its OWN separate copy of certifi (pip._vendor.certifi,
    # not the top-level certifi package above) for its own HTTPS requests —
    # confirmed directly: without this, a frozen app's real pip install
    # (app.core.optional_deps's Dependencies-panel installer) fails
    # immediately with "Could not find a suitable TLS CA certificate
    # bundle" since its cacert.pem isn't a .py file PyInstaller's
    # import-graph analysis would ever pick up on its own.
    collect_data_files('pip._vendor.certifi') +
    # googleapiclient.discovery.build() defaults to static_discovery=True
    # (whenever discoveryServiceUrl isn't passed, which google_mcp_server.py
    # never does) — it loads each API's discovery document (gmail.v1.json,
    # drive.v3.json, etc.) straight from this package's own bundled JSON
    # files rather than fetching them over the network. Without them
    # collected here, build() raises in the frozen build the moment Gmail/
    # Drive/Sheets/Docs auth completes and the MCP server tries to
    # construct its service clients.
    collect_data_files('googleapiclient') +
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
    ] +
    # Both requirements files, read at runtime by app.core.optional_deps'
    # list_bundled() to show the Dependencies panel's "App dependencies"
    # section — the actual declared list, not every incidental package
    # importlib.metadata happens to see on sys.path (a dev pyenv can have
    # hundreds of unrelated ones from other projects). Not bundled by
    # default the way collect_data_files() bundles a Python package's own
    # files; these are plain project files PyInstaller has no reason to
    # know about otherwise. Destination "." puts each at the frozen app's
    # root (sys._MEIPASS), matching where optional_deps.py looks for it.
    [
        (str(Path(SPECPATH).parent / "requirements.txt"), "."),
        (str(Path(SPECPATH) / "requirements.txt"), "backend"),
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
# torch's own compiled tensor/math engine (libtorch_cpu, libtorch_python,
# libc10, ...) is NOT collected here — torch itself is excluded from the
# bundle entirely, see hidden_imports' own note above.
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
        # The whole torch-dependent stack, deliberately excluded together
        # (not partially) — see hidden_imports' own note above for why.
        # Without these, PyInstaller's static analysis would still walk
        # into and bundle torch anyway: it collects app/ via
        # collect_submodules('app') and follows every import statement it
        # finds in those files' AST, including the lazy, function-local
        # `from sentence_transformers import CrossEncoder` in
        # app/core/rerankers.py and similar lazy imports in
        # app/core/embeddings/manager.py and app/core/media_engines.py —
        # PyInstaller doesn't know or care that those imports only
        # actually execute if a user reaches that code path at runtime.
        'torch', 'torchvision', 'torchaudio',
        'transformers', 'sentence_transformers',
        'easyocr', 'rapidocr_onnxruntime', 'cv2',
        # Pure build-machine drift — confirmed zero real imports anywhere
        # in app/ (grepped directly, not guessed). None of these are
        # declared in either requirements.txt; they were getting swept in
        # by other packages' own PyInstaller hooks opportunistically
        # bundling whatever happens to be installed in the SAME dev pyenv
        # this build ran from (e.g. SQLAlchemy's hook includes any
        # installed DB driver — psycopg2 here — even though this app only
        # ever uses sqlite). ~350MB combined. NOTE: grpc is NOT in this
        # list despite zero direct app imports either — qdrant_client's
        # own __init__.py imports its gRPC protobuf stubs unconditionally
        # at module load (qdrant_client/grpc/collections_service_pb2_grpc),
        # even though this app only ever constructs QdrantClient(path=...)
        # — local/embedded mode, no gRPC transport actually used. Excluding
        # it anyway broke app startup outright (confirmed by actually
        # running the built exe, not just a successful build — the build
        # itself doesn't catch this class of failure). A build run from a
        # clean venv containing only requirements.txt's declared packages
        # wouldn't have picked any of these up in the first place — worth
        # doing at some point instead of maintaining this list by hand as
        # new drift accumulates.
        'playwright', 'pyarrow', 'scipy', 'botocore', 'boto3', 's3transfer',
        'pandas', 'sklearn', 'matplotlib', 'psycopg2', 'primp',
        'oracledb', 'elasticsearch', 'pymilvus', 'opensearchpy', 'datasets',
        'ddgs',
        # Every alternative (or, for PPTX/XLSX, the only) document-
        # extraction engine is install-on-demand from the Marketplace's
        # Document Extraction category, not bundled — see
        # app.core.extraction_engines and app.core.optional_deps.
        # 'docx' (python-docx's import name) and 'fitz' (pymupdf's) are
        # deliberately NOT here — app.core.exporter needs both regardless,
        # for DOCX/PDF export (generating a file FROM a chat reply) as
        # well as PDF/DOCX being their own formats' bundled default
        # extraction engine (the opposite, read-in direction).
        'pptx', 'pdfplumber', 'pypdf', 'pdfminer',
        'docx2txt', 'mammoth', 'openpyxl',
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
