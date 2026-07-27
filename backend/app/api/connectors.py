"""
Aegis — Connectors API Endpoint (/api/connectors)

Allows the frontend to:
1. Fetch the Pre-configured Catalog (`GET /api/connectors/catalog`)
2. Connect catalog items with 1-click / token input (`POST /api/connectors/catalog/connect`)
3. Connect arbitrary custom stdio MCP servers (`POST /api/connectors/connect`)
4. List active status and disconnect servers
"""

import logging
from typing import Dict, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.mcp.registry import mcp_registry
from app.mcp.catalog import (
    get_catalog_list,
    get_catalog_for_audience,
    resolve_connector_command,
    CONNECTORS_CATALOG
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/connectors", tags=["connectors"])


# ── Request Schemas ───────────────────────────────────────────────────────────

class ConnectCatalogRequest(BaseModel):
    server_name: str
    env: Optional[Dict[str, str]] = None
    input_params: Optional[Dict[str, str]] = None
    model_config = {"defer_build": True}


class ConnectCustomServerRequest(BaseModel):
    server_name: str
    command: List[str]
    env: Optional[Dict[str, str]] = None
    model_config = {"defer_build": True}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/catalog")
def list_catalog(audience: Optional[str] = None):
    """
    Returns the connector gallery.
    Optional ?audience=hr|marketing|sales|operations|developer|all to filter.
    """
    if audience:
        return {"catalog": get_catalog_for_audience(audience)}
    return {"catalog": get_catalog_list()}



@router.get("")
def list_active_connectors():
    """List health status of all currently connected MCP servers and discovered tools."""
    status = mcp_registry.get_status()
    all_tools = mcp_registry.list_all_tools()
    return {
        "status": status,
        "total_tools": len(all_tools),
        "tools": all_tools
    }


@router.post("/catalog/connect")
def connect_from_catalog(req: ConnectCatalogRequest, db: Session = Depends(get_db)):
    """
    Connect a pre-configured connector from the catalog.
    Automatically resolves commands and template parameters.
    """
    if req.server_name not in CONNECTORS_CATALOG:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{req.server_name}' not found in catalog."
        )

    cat_item = CONNECTORS_CATALOG[req.server_name]

    if cat_item.get("auth_type") == "oauth":
        raise HTTPException(
            status_code=400,
            detail=f"Connector '{req.server_name}' uses OAuth authentication. Please use the /auth/{req.server_name}/login route."
        )

    try:
        command = resolve_connector_command(req.server_name, req.input_params or {})
        tools = mcp_registry.connect_server(
            server_name=req.server_name,
            command=command,
            env=req.env,
            db=db
        )
        return {
            "message": f"Successfully connected catalog connector '{cat_item['display_name']}'",
            "server_name": req.server_name,
            "tools_count": len(tools),
            "tools": tools
        }
    except Exception as e:
        logger.error(f"Error connecting catalog item '{req.server_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connect")
def connect_custom(req: ConnectCustomServerRequest, db: Session = Depends(get_db)):
    """
    Connect an arbitrary custom stdio MCP server command.
    Performs handshake, fetches tools dynamically, and persists to SQLite.
    """
    try:
        tools = mcp_registry.connect_server(
            server_name=req.server_name,
            command=req.command,
            env=req.env,
            db=db
        )
        return {
            "message": f"Successfully connected custom MCP server '{req.server_name}'",
            "tools_count": len(tools),
            "tools": tools
        }
    except Exception as e:
        logger.error(f"Error connecting custom MCP server '{req.server_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{server_name}/reload")
def reload_connector(server_name: str, db: Session = Depends(get_db)):
    """
    Hot-reload a running MCP server by dropping its cache and restarting its subprocess.
    This enables rapid iteration on MCP server schemas without restarting the Uvicorn backend.
    """
    try:
        # Currently, Google Workspace is hardcoded, but this can be genericized 
        # by pulling the stored command from the `mcp_servers` table.
        if server_name == "google_workspace":
            from app.db.crud import get_active_google_credentials
            credentials = get_active_google_credentials(db)
            if not credentials:
                raise HTTPException(status_code=400, detail="No Google credentials found to reload.")
            
            # connect_google_workspace natively drops the old server and spawns a new one
            tools = mcp_registry.connect_google_workspace(credentials.to_json(), db=db)
            return {
                "message": f"Successfully hot-reloaded MCP server '{server_name}'",
                "tools_count": len(tools)
            }
        
        # TODO: Add generic reload using `mcp_servers` table lookup
        raise HTTPException(status_code=501, detail=f"Reloading server '{server_name}' is not fully supported yet.")
        
    except Exception as e:
        logger.error(f"Error hot-reloading MCP server '{server_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{server_name}")
def disconnect_connector(server_name: str, db: Session = Depends(get_db)):
    """Disconnect an active MCP server and update database status."""
    try:
        mcp_registry.disconnect_server(server_name, db=db)
        return {"message": f"Successfully disconnected '{server_name}'"}
    except Exception as e:
        logger.error(f"Error disconnecting MCP server '{server_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
