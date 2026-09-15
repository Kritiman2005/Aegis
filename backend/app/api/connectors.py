"""
Aegis — Connectors API Endpoint (/api/connectors)

Two routers, both mounted unconditionally in main.py — the split is about
which pieces of the *catalog* router still need
app.core.feature_flags.CONNECTORS_ENABLED, not about hiding it entirely:

- `router`: the pre-configured catalog. Listing (`GET .../catalog`) and
  connecting (`POST .../catalog/connect`) filter out every auth_type=="oauth"
  entry while CONNECTORS_ENABLED is off — those need the user's own OAuth
  app credentials (see that flag's docstring), configured via
  `/auth/{service}/configure` before a Connect click can work, never a
  hosted broker Aegis runs on their behalf. `POST .../catalog/connect`
  itself always rejects auth_type=="oauth" regardless of the flag — that
  path only spawns a stdio command, which doesn't apply to OAuth entries.
  Every other auth_type (api_key/path/connection_string/none) needs nothing
  from Aegis beyond what custom_mcp_router already does for a pasted
  config, so there's no reason to hide those behind the same flag.
  `auth_router`/`oauth_router` (the actual `/auth/{service}/login` OAuth
  dance) stay separately gated in main.py.
- `custom_mcp_router`: everything that isn't a curated catalog entry — a raw
  stdio command/args/env the user provides directly (see http_client.py's
  sibling module, stdio_client.py), a hosted server connected by URL over
  Streamable HTTP (http_client.py — no OAuth, only a static header value
  the user pastes in themselves), a live search of the public MCP Registry
  (registry_client.py), or a server installed straight from a GitHub repo
  (github_installer.py). No OAuth involved anywhere in this router.

Allows the frontend to:
1. Fetch the catalog, oauth entries filtered out while CONNECTORS_ENABLED is off (`GET /api/connectors/catalog`)
2. Connect a non-oauth catalog item, auto-downloading whatever runtime its command needs (`POST /api/connectors/catalog/connect`)
3. Connect arbitrary custom stdio MCP servers (`POST /api/connectors/connect`)
4. Connect a hosted server over Streamable HTTP (`POST /api/connectors/remote/connect`)
5. Search the public MCP Registry (`GET /api/connectors/registry/search`)
6. Inspect and install a server from a GitHub repo (`POST /api/connectors/github/detect`, `POST /api/connectors/github/install`)
7. List active status, reload, and disconnect servers
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.connection_manager import manager
from app.core.feature_flags import CONNECTORS_ENABLED
from app.db.database import SessionLocal, get_db
from app.mcp.registry import mcp_registry, reconnect_from_saved_config
from app.mcp.runtime_manager import ensure_runtime
from app.mcp import registry_client as mcp_registry_client
from app.mcp import github_installer
from app.mcp.catalog import (
    get_catalog_list,
    get_catalog_for_audience,
    resolve_connector_command,
    CONNECTORS_CATALOG
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/connectors", tags=["connectors"])
custom_mcp_router = APIRouter(prefix="/api/connectors", tags=["mcp"])


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


class ConnectRemoteServerRequest(BaseModel):
    server_name: str
    url: str
    headers: Optional[Dict[str, str]] = None
    model_config = {"defer_build": True}


class GitHubDetectRequest(BaseModel):
    repo_url: str
    model_config = {"defer_build": True}


class GitHubInstallRequest(BaseModel):
    repo_url: str
    server_name: str
    command: List[str]
    build_steps: Optional[List[List[str]]] = None
    env: Optional[Dict[str, str]] = None
    model_config = {"defer_build": True}


# ── Routes ────────────────────────────────────────────────────────────────────

def _visible_catalog(items: List[dict]) -> List[dict]:
    """Drops auth_type=="oauth" entries while CONNECTORS_ENABLED is off —
    see this module's docstring. Once the broker ships, flipping that one
    flag brings the full catalog back with no other change needed."""
    if CONNECTORS_ENABLED:
        return items
    return [item for item in items if item.get("auth_type") != "oauth"]


@router.get("/catalog")
def list_catalog(audience: Optional[str] = None):
    """
    Returns the connector gallery.
    Optional ?audience=hr|marketing|sales|operations|developer|all to filter.
    """
    if audience:
        return {"catalog": _visible_catalog(get_catalog_for_audience(audience))}
    return {"catalog": _visible_catalog(get_catalog_list())}


async def _connect_catalog_task(server_name: str, command: List[str], env: Optional[Dict[str, str]], input_params: Optional[Dict[str, str]]) -> None:
    """Background task behind POST /catalog/connect — same shape as
    _connect_custom_task below (auto-download whatever runtime `command`
    needs via runtime_manager, then the MCP handshake), reusing the exact
    same mcp_connect_progress/_complete/_failed broadcast events so
    MCPServersPanel.tsx's existing progress UI needs no changes to also
    show catalog connects."""
    async def progress(**kwargs):
        await _broadcast_connect_progress(server_name, **kwargs)

    try:
        resolved_command = await ensure_runtime(command, progress)

        await progress(stage="connect", status="running", message=f"Starting '{server_name}' and discovering its tools…")

        config_json = {"type": "catalog", "server_name": server_name, "env": env, "input_params": input_params}

        def _do_connect():
            with SessionLocal() as db:
                return mcp_registry.connect_server(
                    server_name=server_name, command=resolved_command, env=env, db=db, config_json=config_json,
                )

        tools = await asyncio.to_thread(_do_connect)

        await manager.broadcast_json({
            "type": "mcp_connect_complete", "server_name": server_name,
            "tools_count": len(tools), "tools": tools,
        })
    except Exception as e:
        logger.error(f"Error connecting catalog connector '{server_name}': {e}")
        await manager.broadcast_json({"type": "mcp_connect_failed", "server_name": server_name, "message": str(e)})


@router.post("/catalog/connect")
def connect_from_catalog(req: ConnectCatalogRequest, background_tasks: BackgroundTasks):
    """
    Connect a pre-configured connector from the catalog. Resolves commands
    and template parameters, then — same as the custom-server connect path
    — auto-downloads whatever runtime the command needs (npx/uvx) in the
    background and reports progress over the same WebSocket events, rather
    than blocking the request on a cold download.
    """
    if req.server_name not in CONNECTORS_CATALOG:
        raise HTTPException(status_code=404, detail=f"Connector '{req.server_name}' not found in catalog.")

    cat_item = CONNECTORS_CATALOG[req.server_name]

    if cat_item.get("auth_type") == "oauth":
        raise HTTPException(
            status_code=400,
            detail=f"Connector '{req.server_name}' uses OAuth authentication, which Aegis doesn't support connecting yet."
        )

    try:
        command = resolve_connector_command(req.server_name, req.input_params or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(_connect_catalog_task, req.server_name, command, req.env, req.input_params)
    return {"status": "connecting", "server_name": req.server_name}


@custom_mcp_router.get("")
def list_active_connectors():
    """List health status of all currently connected MCP servers (catalog, OAuth, and
    custom alike — this endpoint itself carries no OAuth dependency) and discovered tools."""
    status = mcp_registry.get_status()
    all_tools = mcp_registry.list_all_tools()
    return {
        "status": status,
        "total_tools": len(all_tools),
        "tools": all_tools
    }


async def _broadcast_connect_progress(server_name: str, **kwargs) -> None:
    """Progress callback handed to runtime_manager.ensure_runtime — reshapes its
    generic {stage, status, message, ...} events into the mcp_connect_progress
    broadcast the frontend listens for, tagged with which server this is about
    (a user can paste several servers into one JSON blob, each connecting/
    downloading independently — see MCPServersPanel.tsx)."""
    await manager.broadcast_json({
        "type": "mcp_connect_progress",
        "server_name": server_name,
        **kwargs,
    })


async def _connect_custom_task(server_name: str, command: List[str], env: Optional[Dict[str, str]]) -> None:
    """
    Background task doing the real work behind POST /connect: resolve the
    command (auto-downloading a Node/uv runtime via runtime_manager if it's
    missing), then perform the actual MCP handshake. Broadcasts every step so
    the frontend can show live progress instead of one long blocking spinner
    — same pattern as models_hub.py's download_file_task for GGUF downloads.
    """
    async def progress(**kwargs):
        await _broadcast_connect_progress(server_name, **kwargs)

    try:
        resolved_command = await ensure_runtime(command, progress)

        await progress(stage="connect", status="running", message=f"Starting '{server_name}' and discovering its tools…")

        config_json = {
            "type": "custom",
            "server_name": server_name,
            # The ORIGINAL command is what's persisted/restored on next
            # launch — re-resolving through ensure_runtime each time (cheap
            # once cached) keeps this robust to the runtime cache being
            # cleared or moved, rather than baking in a path that might not
            # exist next time.
            "command": command,
            "env": env,
        }

        def _do_connect():
            with SessionLocal() as db:
                return mcp_registry.connect_server(
                    server_name=server_name,
                    command=resolved_command,
                    env=env,
                    db=db,
                    config_json=config_json,
                )

        tools = await asyncio.to_thread(_do_connect)

        await manager.broadcast_json({
            "type": "mcp_connect_complete",
            "server_name": server_name,
            "tools_count": len(tools),
            "tools": tools,
        })
    except Exception as e:
        logger.error(f"Error connecting custom MCP server '{server_name}': {e}")
        await manager.broadcast_json({
            "type": "mcp_connect_failed",
            "server_name": server_name,
            "message": str(e),
        })


@custom_mcp_router.post("/connect")
def connect_custom(req: ConnectCustomServerRequest, background_tasks: BackgroundTasks):
    """
    Connect an arbitrary custom stdio MCP server command — a user-supplied
    command/args/env, not one of the curated catalog entries. Returns
    immediately; the actual work (auto-installing a Node/uv runtime if the
    command needs one, then the MCP handshake) runs in the background and
    reports progress via mcp_connect_progress/_complete/_failed WebSocket
    broadcasts, since resolving a cold runtime download can take real time
    and the frontend needs something to show during it — a single blocking
    HTTP response can't do that. See MCPServersPanel.tsx for the listener.
    """
    background_tasks.add_task(_connect_custom_task, req.server_name, req.command, req.env)
    return {"status": "connecting", "server_name": req.server_name}


async def _connect_remote_task(server_name: str, url: str, headers: Optional[Dict[str, str]]) -> None:
    """Background task behind POST /remote/connect — no runtime download step
    (nothing to spawn locally), just the MCP handshake over HTTP. Same
    mcp_connect_progress/_complete/_failed broadcasts as the stdio paths so
    the frontend's existing progress UI needs no changes to show this too."""
    async def progress(**kwargs):
        await _broadcast_connect_progress(server_name, **kwargs)

    try:
        await progress(stage="connect", status="running", message=f"Connecting to '{server_name}' at {url}…")

        config_json = {"type": "remote", "server_name": server_name, "url": url, "headers": headers}

        def _do_connect():
            with SessionLocal() as db:
                return mcp_registry.connect_remote_server(
                    server_name=server_name, url=url, headers=headers, db=db, config_json=config_json,
                )

        tools = await asyncio.to_thread(_do_connect)

        await manager.broadcast_json({
            "type": "mcp_connect_complete", "server_name": server_name,
            "tools_count": len(tools), "tools": tools,
        })
    except Exception as e:
        logger.error(f"Error connecting remote MCP server '{server_name}': {e}")
        await manager.broadcast_json({"type": "mcp_connect_failed", "server_name": server_name, "message": str(e)})


@custom_mcp_router.post("/remote/connect")
def connect_remote(req: ConnectRemoteServerRequest, background_tasks: BackgroundTasks):
    """
    Connect a hosted MCP server by URL over the Streamable HTTP transport —
    no subprocess, no OAuth exchange. `headers` is whatever static values
    (an API key, a bearer token) the user pasted in themselves; if the
    server actually requires an OAuth login this will simply fail to
    authenticate, which is expected — Aegis doesn't run that flow.
    """
    background_tasks.add_task(_connect_remote_task, req.server_name, req.url, req.headers)
    return {"status": "connecting", "server_name": req.server_name}


@custom_mcp_router.get("/registry/search")
def search_mcp_registry(q: str, limit: int = 20):
    """
    Live-searches the official public MCP Registry (registry.modelcontextprotocol.io)
    by name/description. Read-only, unauthenticated — nothing is installed
    just from a search. See registry_client.py for the response shape.
    """
    try:
        return {"results": mcp_registry_client.search(q, limit=limit)}
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@custom_mcp_router.post("/github/detect")
async def detect_github_server(req: GitHubDetectRequest):
    """
    Clones (or updates an existing clone of) a GitHub repo and inspects it
    for a runnable MCP server — a package.json with a bin/main entry, or a
    Python project script. Returns a best-guess command + any build steps
    needed for the user to review before POSTing /github/install; when
    nothing recognizable is found, `detected` is false and the user can
    still type the run command in by hand.
    """
    try:
        repo_dir = await github_installer.clone_or_update(req.repo_url)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    config = github_installer.detect_run_config(repo_dir)
    return {"repo_dir": str(repo_dir), **config}


async def _install_github_task(
    repo_url: str, server_name: str, command: List[str],
    build_steps: List[List[str]], env: Optional[Dict[str, str]],
) -> None:
    """Background task behind POST /github/install: (re-)clone, run any build
    steps, then connect the resulting local command as a normal stdio MCP
    server — same progress broadcasts as every other connect path."""
    async def progress(**kwargs):
        await _broadcast_connect_progress(server_name, **kwargs)

    try:
        repo_dir = await github_installer.clone_or_update(repo_url, progress)

        if build_steps:
            await github_installer.build(repo_dir, build_steps, progress)

        await progress(stage="connect", status="running", message=f"Starting '{server_name}' and discovering its tools…")

        config_json = {
            "type": "github", "server_name": server_name, "repo_url": repo_url,
            "command": command, "env": env,
        }

        def _do_connect():
            with SessionLocal() as db:
                return mcp_registry.connect_server(
                    server_name=server_name, command=command, env=env, db=db, config_json=config_json,
                )

        tools = await asyncio.to_thread(_do_connect)

        await manager.broadcast_json({
            "type": "mcp_connect_complete", "server_name": server_name,
            "tools_count": len(tools), "tools": tools,
        })
    except Exception as e:
        logger.error(f"Error installing GitHub MCP server '{server_name}' from {repo_url}: {e}")
        await manager.broadcast_json({"type": "mcp_connect_failed", "server_name": server_name, "message": str(e)})


@custom_mcp_router.post("/github/install")
def install_from_github(req: GitHubInstallRequest, background_tasks: BackgroundTasks):
    """
    Installs and connects an MCP server from a GitHub repo, using the
    command (and optional build steps) the user reviewed after /github/detect
    — either the auto-detected guess or one they edited/typed themselves.
    Runs in the background (clone/build can take real time) reporting
    progress over the same mcp_connect_progress broadcasts as other connects.
    """
    background_tasks.add_task(
        _install_github_task, req.repo_url, req.server_name, req.command, req.build_steps or [], req.env,
    )
    return {"status": "connecting", "server_name": req.server_name}


@custom_mcp_router.post("/{server_name}/reload")
def reload_connector(server_name: str, db: Session = Depends(get_db)):
    """
    Hot-reload a running (or previously saved but not currently running) MCP
    server by dropping its cache and restarting its subprocess. Google's
    OAuth services keep their own credential-based path; every other server
    type (catalog/oauth/custom) goes through the same reconnect_from_saved_config
    helper main.py's startup auto-restore uses, so there's one dispatch
    implementation instead of two drifting copies.
    """
    try:
        if server_name in ["google_mail", "google_drive"]:
            from app.db.crud import get_active_google_credentials
            credentials = get_active_google_credentials(db, service_name=server_name)
            if not credentials:
                raise HTTPException(status_code=400, detail=f"No Google credentials found to reload for {server_name}.")

            # connect_google_service natively drops the old server and spawns a new one
            tools = mcp_registry.connect_google_service(server_name, credentials.to_json(), db=db)
            return {
                "message": f"Successfully hot-reloaded MCP server '{server_name}'",
                "tools_count": len(tools)
            }

        from app.db.crud import get_mcp_server_by_name
        server = get_mcp_server_by_name(db, server_name)
        if not server:
            raise HTTPException(status_code=404, detail=f"No saved server named '{server_name}'.")

        tools = reconnect_from_saved_config(db, server)
        return {
            "message": f"Successfully hot-reloaded MCP server '{server_name}'",
            "tools_count": len(tools)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error hot-reloading MCP server '{server_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@custom_mcp_router.delete("/{server_name}")
def disconnect_connector(server_name: str, db: Session = Depends(get_db)):
    """Disconnect an active MCP server and update database status."""
    try:
        mcp_registry.disconnect_server(server_name, db=db)
        return {"message": f"Successfully disconnected '{server_name}'"}
    except Exception as e:
        logger.error(f"Error disconnecting MCP server '{server_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
