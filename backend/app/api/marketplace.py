from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core import marketplace
from app.api.scraping import start_install as start_scraper_install  # reuse the existing Chromium install flow

router = APIRouter(prefix="/api/marketplace", tags=["Marketplace"])

# Delegates to each tool's own /install route logic so there's one code path
# (and one in-memory install-state tracker) per download, not one duplicated
# here.
_INSTALLERS = {
    "playwright_scraper": start_scraper_install,
}


@router.get("/tools")
def get_tools():
    return {"tools": marketplace.list_tools()}


@router.post("/tools/{tool_id}/install")
async def install_tool(tool_id: str, background_tasks: BackgroundTasks):
    installer = _INSTALLERS.get(tool_id)
    if installer is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool: '{tool_id}'")
    return await installer(background_tasks)


@router.get("/skills")
def get_skills():
    return {"skills": marketplace.list_marketplace_skills()}


@router.post("/skills/{skill_id}/install")
def install_skill(skill_id: str):
    try:
        marketplace.install_marketplace_skill(skill_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "installed"}


@router.delete("/skills/{skill_id}")
def uninstall_skill(skill_id: str):
    marketplace.uninstall_marketplace_skill(skill_id)
    return {"status": "removed"}
