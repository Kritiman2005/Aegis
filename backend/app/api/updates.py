"""
Aegis — App Update Check API (/api/updates)

Compares the running app's own version (passed by the frontend, which reads
it from Electron's app.getVersion() via the existing window.aegis.app IPC
bridge — see electron/preload.ts) against the latest GitHub Release tag for
this repo. Read-only check-and-notify, not an auto-updater: there's no
code-signed update feed wired up for electron-updater, so "update" here
means pointing the user at the new release to download and reinstall
themselves, same as a first install.
"""

import logging
import re

import httpx
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/updates", tags=["Updates"])

GITHUB_REPO = "Kritiman2005/Aegis"


def _parse_version(v: str) -> tuple:
    """Turns "v1.2.3" or "1.2.3" into (1, 2, 3) for a plain numeric
    comparison — good enough for this app's own tag convention (no
    pre-release suffixes like "-beta" in use). A tag that doesn't parse to
    any digits sorts as (0,), i.e. never "newer" than a real version."""
    parts = re.findall(r"\d+", v.strip())
    return tuple(int(p) for p in parts) if parts else (0,)


@router.get("/check")
async def check_for_update(current_version: str):
    """Best-effort: any failure (offline, GitHub rate-limited, no releases
    published yet) returns update_available=False with an `error` field
    rather than raising — a broken update check should never surface as an
    app error, just silently show nothing to update."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
        if res.status_code != 200:
            return {"update_available": False, "current_version": current_version, "error": f"GitHub returned {res.status_code}"}
        data = res.json()
    except httpx.HTTPError as e:
        logger.info(f"Update check failed (likely offline): {e}")
        return {"update_available": False, "current_version": current_version, "error": "Could not reach GitHub."}

    latest_tag = data.get("tag_name") or ""
    update_available = _parse_version(latest_tag) > _parse_version(current_version)

    # The .dmg asset specifically, when present, so the download button goes
    # straight to the file rather than the release's changelog page.
    dmg_asset = next(
        (a["browser_download_url"] for a in data.get("assets", []) if a.get("name", "").lower().endswith(".dmg")),
        None,
    )

    return {
        "update_available": update_available,
        "current_version": current_version,
        "latest_version": latest_tag.lstrip("vV") or None,
        "release_url": data.get("html_url"),
        "download_url": dmg_asset or data.get("html_url"),
        "release_notes": data.get("body") or "",
        "published_at": data.get("published_at"),
    }
