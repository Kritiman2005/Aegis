import re

from fastapi import APIRouter, UploadFile, File, HTTPException

from app.core.skills import load_skills, SKILLS_DIR, _parse_frontmatter

router = APIRouter(prefix="/api/skills", tags=["Skills"])


@router.get("")
def list_skills():
    """Lists installed chat skills and the folder they live in, so the
    frontend can show what's active and let the user open that folder."""
    skills = load_skills()
    return {
        "folder": str(SKILLS_DIR),
        "skills": [
            {"name": s.name, "description": s.description, "folder": s.folder}
            for s in skills
        ],
    }


@router.post("/upload")
async def upload_skill(file: UploadFile = File(...)):
    """
    Installs a user-supplied SKILL.md directly — the composer's "Upload
    Custom Skill" action. Same install target as a Marketplace skill
    (SKILLS_DIR/<folder>/SKILL.md), so it shows up and toggles the same way
    once installed; the only difference is where the file came from.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text.")

    fm, _ = _parse_frontmatter(text)
    name = fm.get("name", "").strip()
    description = fm.get("description", "").strip()
    if not name or not description:
        raise HTTPException(
            status_code=400,
            detail="SKILL.md must have YAML frontmatter with a 'name' and a 'description'.",
        )

    folder_name = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "custom-skill"
    dest_dir = SKILLS_DIR / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "SKILL.md").write_text(text, encoding="utf-8")

    return {"status": "installed", "id": folder_name, "name": name}
