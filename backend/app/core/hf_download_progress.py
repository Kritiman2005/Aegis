"""
Aegis — real byte-level progress for huggingface_hub downloads

models_hub.py's GGUF downloader gets real progress for free from its own
raw httpx streaming loop. Marketplace's embedding and reranker downloads
instead go through huggingface_hub.snapshot_download() (via fastembed /
sentence_transformers), which exposes NO byte-level progress callback
through its public API — confirmed directly against the installed
version: snapshot_download's own tqdm_class parameter only wraps the
outer "Fetching N files" bar, never an individual file's own download
("the tqdm_class is not passed to each individual download" — its own
docstring), and the per-file byte progress bar is built from a private,
unexported parameter of file_download.http_get.

So this polls the filesystem instead — huggingface_hub always streams an
in-progress file to <cache_dir>/models--org--repo/blobs/<hash>.incomplete
before renaming it to its final name on completion, so the sum of every
blob's current on-disk size (finished or still ".incomplete") is a real,
monotonically-increasing measure of download progress, independent of
whatever internal mechanism any given huggingface_hub version uses.
"""

import asyncio
import threading
from pathlib import Path
from typing import Callable, List, Optional


def repo_folder_name(cache_dir: str, repo_id: str) -> Path:
    return Path(cache_dir) / f"models--{repo_id.replace('/', '--')}"


def get_expected_total_bytes(repo_id: str, allow_filenames: Optional[List[str]] = None) -> int:
    """
    Sum of on-HF file sizes for a repo, optionally restricted to an exact
    filename allowlist (matching fastembed's own download_files_from_huggingface
    allow_patterns — a full snapshot_download call, used for the
    sentence_transformers backend, has no such restriction).
    """
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo_id, files_metadata=True)
    total = 0
    for f in info.siblings or []:
        if allow_filenames is not None and f.rfilename not in allow_filenames:
            continue
        total += f.size or 0
    return total


def _current_downloaded_bytes(cache_dir: str, repo_id: str) -> int:
    blobs_dir = repo_folder_name(cache_dir, repo_id) / "blobs"
    if not blobs_dir.is_dir():
        return 0
    total = 0
    for f in blobs_dir.iterdir():
        try:
            total += f.stat().st_size
        except OSError:
            pass  # renamed/removed mid-scan — negligible, next poll picks it up
    return total


class DownloadProgressPoller:
    """
    Polls disk on its own thread while a snapshot_download runs on another
    (started via anyio.to_thread.run_sync), broadcasting downloaded/total
    bytes every `interval` seconds until stop() is called. on_progress may
    be sync or async; it's always invoked on `loop` via
    run_coroutine_threadsafe since this fires from a plain background
    thread, not the event loop.
    """

    def __init__(
        self, cache_dir: str, repo_id: str, total_bytes: int,
        on_progress: Callable, loop: asyncio.AbstractEventLoop, interval: float = 0.5,
    ):
        self._cache_dir = cache_dir
        self._repo_id = repo_id
        self._total = total_bytes
        self._on_progress = on_progress
        self._loop = loop
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if self._total > 0:
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        last_percent = -1.0
        while not self._stop_event.is_set():
            downloaded = min(_current_downloaded_bytes(self._cache_dir, self._repo_id), self._total)
            percent = round(downloaded / self._total * 100, 1)
            if percent - last_percent >= 0.5:
                last_percent = percent
                try:
                    asyncio.run_coroutine_threadsafe(self._call(downloaded), self._loop)
                except RuntimeError:
                    pass  # loop already closed — safe to drop, download itself is unaffected
            self._stop_event.wait(self._interval)

    async def _call(self, downloaded: int) -> None:
        result = self._on_progress(downloaded, self._total)
        if asyncio.iscoroutine(result):
            await result
