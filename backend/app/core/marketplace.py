"""
Aegis — Marketplace

Lets the user browse and explicitly choose what to install, rather than
features silently downloading on first use (e.g. web scraping used to
download a ~95MB headless Chromium the first time someone clicked "Scrape a
Web Page" — now that install only happens when the user picks it here).

Two catalogs:
  - Automation Tools: capabilities that need a native download (currently
    just Playwright-based web scraping — voice transcription used to be
    here too, but its model is small and universal enough that it now
    ships inside the app bundle itself instead, see
    app/core/transcription.py's module docstring). Install/status is
    delegated to whatever module actually owns that capability
    (app.core.scraper) rather than duplicated here.
  - Skills: pre-built SKILL.md packs bundled with the app under
    app/marketplace/skills/. "Installing" one just copies its folder into
    the user's own skills directory (app.core.skills.SKILLS_DIR) — from
    that point on it's an ordinary user skill, picked up by load_skills()
    like any hand-written one, and can be removed the same way.
"""

import shutil
from pathlib import Path
from typing import List, Dict

from app.core.skills import SKILLS_DIR, _parse_frontmatter
from app.core.scraper import is_chromium_installed

MARKETPLACE_SKILLS_DIR = Path(__file__).resolve().parent.parent / "marketplace" / "skills"

TOOLS_CATALOG = [
    {
        "id": "playwright_scraper",
        "name": "Web Scraping",
        "description": (
            "Lets Aegis open a real headless browser to scrape any web page — "
            "including JavaScript-heavy sites — and add its content to your "
            "chat as a searchable document."
        ),
        "category": "Automation",
        "size_estimate": "~95 MB, downloaded once",
        "status_endpoint": "/api/scrape/status",
    },
]

_STATUS_CHECKS = {
    "playwright_scraper": is_chromium_installed,
}


def list_tools() -> List[Dict]:
    """Merges TOOLS_CATALOG with each tool's live install status."""
    out = []
    for tool in TOOLS_CATALOG:
        entry = dict(tool)
        check = _STATUS_CHECKS.get(tool["id"])
        entry["installed"] = check() if check else False
        out.append(entry)
    return out


def list_marketplace_skills() -> List[Dict]:
    """
    Scans the bundled marketplace skills directory, parses each SKILL.md for
    its name/description, and reports whether it's already installed (i.e.
    a matching folder already exists in the user's own skills directory).
    """
    out = []
    if not MARKETPLACE_SKILLS_DIR.exists():
        return out
    for folder in sorted(MARKETPLACE_SKILLS_DIR.iterdir()):
        skill_file = folder / "SKILL.md"
        if not folder.is_dir() or not skill_file.exists():
            continue
        try:
            fm, _ = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            out.append({
                "id": folder.name,
                "name": fm.get("name", folder.name),
                "description": fm.get("description", ""),
                "installed": (SKILLS_DIR / folder.name / "SKILL.md").exists(),
            })
        except Exception:
            continue
    return out


def install_marketplace_skill(skill_id: str) -> None:
    src = MARKETPLACE_SKILLS_DIR / skill_id
    if not (src / "SKILL.md").exists():
        raise ValueError(f"Unknown marketplace skill: '{skill_id}'")
    dest = SKILLS_DIR / skill_id
    shutil.copytree(src, dest, dirs_exist_ok=True)


def uninstall_marketplace_skill(skill_id: str) -> None:
    dest = SKILLS_DIR / skill_id
    if dest.exists():
        shutil.rmtree(dest)
