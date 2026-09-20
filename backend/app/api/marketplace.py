from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core import marketplace, optional_deps
from app.core.friendly_errors import humanize_exception

router = APIRouter(prefix="/api/marketplace", tags=["Marketplace"])

# Delegates to each tool's own /install route logic so there's one code path
# (and one in-memory install-state tracker) per download, not one duplicated
# here. Empty now that every remaining plain Marketplace tool ships bundled
# (nothing left needs a native download) — kept as the dispatch point for
# whenever a future tool does. Extraction engines (below) are handled
# separately since installing one is a real (if usually quick) pip install
# plus an enable/disable preference, not a download with its own progress
# tracker to plug in here.
_INSTALLERS = {}


@router.get("/tools")
def get_tools():
    return {"tools": marketplace.list_tools()}


@router.post("/tools/{tool_id}/install")
async def install_tool(tool_id: str, background_tasks: BackgroundTasks):
    extraction_entry = marketplace.find_extraction_entry(tool_id)
    if extraction_entry:
        pip_package = extraction_entry.get("pip_package")
        if pip_package and not optional_deps.is_available(pip_package):
            # Small, fast packages (pdfplumber, pypdf, pdfminer.six,
            # docx2txt, mammoth, python-pptx, openpyxl, pandas) — a real
            # pip install, same mechanism the Dependencies panel's own
            # install box uses, run off the event loop but still awaited
            # here (not a background task with its own progress polling):
            # MarketplaceView's ToolCard already shows a spinner for the
            # duration of this request, and none of these need
            # byte-level progress the way a multi-hundred-MB model
            # download does — typically done in a few seconds.
            import anyio
            try:
                await anyio.to_thread.run_sync(optional_deps.install_custom_sync, pip_package)
            except Exception as e:
                friendly = humanize_exception(e, context=f"installing '{pip_package}'")
                raise HTTPException(status_code=502, detail=friendly)

        from app.core.extraction_engines import set_engine_enabled
        set_engine_enabled(extraction_entry["format"], extraction_entry["engine_id"], True)
        return {"status": "installed"}

    installer = _INSTALLERS.get(tool_id)
    if installer is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool: '{tool_id}'")
    return await installer(background_tasks)


@router.delete("/tools/{tool_id}")
def uninstall_tool(tool_id: str):
    """
    Uninstalls a Marketplace tool. Right now this only means an extraction
    engine (a per-format enable/disable preference — see
    app.core.extraction_engines.set_engine_enabled), including a format's
    own default one — nothing here is protected from removal except
    Aegis's core SQLite/Qdrant stores, which are never listed as tools in
    the first place (see marketplace_databases.py's is_builtin guard). A
    format left with zero installed engines still extracts fine: extract()
    falls back to its own hardcoded default regardless of this state.
    Never uninstalls the underlying pip package itself — that's a
    separate concern the Dependencies panel owns; disabling a preference
    here doesn't mean the user wants the package gone from disk too.
    """
    extraction_entry = marketplace.find_extraction_entry(tool_id)
    if not extraction_entry:
        raise HTTPException(status_code=404, detail=f"'{tool_id}' isn't an installed tool that can be removed.")

    from app.core.extraction_engines import set_engine_enabled
    set_engine_enabled(extraction_entry["format"], extraction_entry["engine_id"], False)
    return {"status": "uninstalled"}
