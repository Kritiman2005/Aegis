"""
Aegis — Generic OAuth Routes

Provides /auth/{service_name}/login and /auth/{service_name}/callback
for every service in OAUTH_CONFIGS (Slack, Notion, GitHub, HubSpot, Linear, Airtable, Jira, Salesforce).

The user flow for each service:
  1. Frontend calls GET /auth/{service}/login
  2. Server builds the auth URL, opens system browser → user sees service login popup
  3. Service redirects browser to GET /auth/{service}/callback?code=xxx&state=yyy
  4. Server exchanges code → access token → extracts env vars → launches MCP subprocess
  5. MCP tools become available immediately; WebSocket notifies the frontend
"""

from __future__ import annotations

import logging
import secrets
import webbrowser

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth.oauth_service import (
    OAUTH_CONFIGS,
    build_auth_url,
    exchange_code_for_token,
    extract_env_vars,
    generate_pkce_pair,
    get_client_credentials,
    jira_fetch_cloud_id,
    github_fetch_username,
)
from app.core.connection_manager import manager as ws_manager
from app.mcp.registry import mcp_registry

router  = APIRouter(prefix="/auth", tags=["oauth"])
logger  = logging.getLogger(__name__)

# In-process state store: "{service}:{state}" → code_verifier (or None)
# This is fine for a single-user local desktop app.
_OAUTH_STATES: dict[str, str | None] = {}

# ─── Success HTML template ────────────────────────────────────────────────────

def _success_html(display_name: str, n_tools: int) -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <title>{display_name} Connected — Aegis</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: #0d0d0d;
                color: #f0f0f0;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
            }}
            .card {{
                background: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 16px;
                padding: 48px 56px;
                text-align: center;
                max-width: 420px;
            }}
            .icon {{ font-size: 56px; margin-bottom: 20px; }}
            h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 10px; color: #fff; }}
            p  {{ font-size: 14px; color: #888; line-height: 1.6; }}
            .badge {{
                display: inline-block;
                margin-top: 16px;
                background: #1a3a2a;
                color: #34d399;
                border-radius: 999px;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: 500;
            }}
            .closing {{ margin-top: 24px; font-size: 12px; color: #555; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">✅</div>
            <h1>{display_name} Connected!</h1>
            <p>{n_tools} new tools are now available in Aegis.</p>
            <div class="badge">{n_tools} tools activated</div>
            <p class="closing">This tab will close automatically…</p>
        </div>
        <script>
            if (window.opener) {{
                window.opener.postMessage('auth_success', '*');
            }}
            setTimeout(() => window.close(), 2500);
        </script>
    </body>
    </html>
    """

def _error_html(display_name: str, message: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <title>Connection Failed — Aegis</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: #0d0d0d; color: #f0f0f0;
                display: flex; justify-content: center; align-items: center; min-height: 100vh;
            }}
            .card {{
                background: #1a1a1a; border: 1px solid #3a1a1a; border-radius: 16px;
                padding: 48px 56px; text-align: center; max-width: 480px;
            }}
            h1 {{ color: #f87171; margin: 16px 0 8px; font-size: 20px; }}
            pre {{ text-align: left; background: #111; padding: 16px; border-radius: 8px; font-size: 12px; color: #ccc; white-space: pre-wrap; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div style="font-size:48px">❌</div>
            <h1>Could not connect {display_name}</h1>
            <pre>{message}</pre>
            <p style="margin-top:16px;font-size:13px;color:#666">You can close this tab and try again.</p>
        </div>
    </body>
    </html>
    """

# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/services")
def list_oauth_services():
    """Returns all services that support 1-click OAuth (for the frontend Connect buttons)."""
    return {
        "services": [
            {
                "service": name,
                "display_name": cfg["display_name"],
                "login_url":    f"/auth/{name}/login",
                "configured":   bool(
                    __import__("os").environ.get(cfg["client_id_env"])
                    and __import__("os").environ.get(cfg["client_secret_env"])
                ),
                "setup_url":  cfg.get("setup_url", ""),
                "setup_hint": cfg.get("setup_hint", ""),
            }
            for name, cfg in OAUTH_CONFIGS.items()
        ]
    }


@router.get("/{service_name}/login")
def service_oauth_login(service_name: str):
    """
    Starts the OAuth 2.0 flow for any configured service.
    Opens the system browser to the service's authorization page.
    """
    if service_name not in OAUTH_CONFIGS:
        return JSONResponse(
            status_code=404,
            content={"error": f"Unknown service: '{service_name}'. Available: {list(OAUTH_CONFIGS.keys())}"}
        )

    config = OAUTH_CONFIGS[service_name]

    # Validate credentials are configured before opening browser
    try:
        get_client_credentials(service_name)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error":      str(exc),
                "setup_url":  config.get("setup_url", ""),
                "setup_hint": config.get("setup_hint", ""),
            }
        )

    state = secrets.token_urlsafe(32)

    # Generate PKCE pair if service requires it
    code_verifier   = None
    code_challenge  = None
    if config.get("requires_pkce"):
        code_verifier, code_challenge = generate_pkce_pair()

    _OAUTH_STATES[f"{service_name}:{state}"] = code_verifier  # None if no PKCE

    auth_url = build_auth_url(service_name, state, code_challenge)
    logger.info("Redirecting to OAuth for %s: %s", config["display_name"], auth_url)
    
    return RedirectResponse(url=auth_url)


@router.get("/{service_name}/callback")
async def service_oauth_callback(service_name: str, request: Request):
    """
    Receives the OAuth redirect, exchanges the code for tokens,
    launches the MCP server subprocess, and notifies the frontend via WebSocket.
    """
    if service_name not in OAUTH_CONFIGS:
        return JSONResponse(status_code=404, content={"error": f"Unknown service: '{service_name}'"})

    config       = OAUTH_CONFIGS[service_name]
    display_name = config["display_name"]

    # Extract query params
    state = request.query_params.get("state", "")
    code  = request.query_params.get("code", "")
    error = request.query_params.get("error", "")

    # User denied or provider error
    if error:
        msg = request.query_params.get("error_description", error)
        logger.warning("OAuth denied for %s: %s", service_name, msg)
        return HTMLResponse(content=_error_html(display_name, f"Authorization denied: {msg}"))

    # Validate state to prevent CSRF
    state_key = f"{service_name}:{state}"
    if not state or state_key not in _OAUTH_STATES:
        return HTMLResponse(content=_error_html(display_name, "Invalid or expired state parameter."))

    code_verifier = _OAUTH_STATES.pop(state_key)

    try:
        def _exchange_tokens():
            """Blocking work: token exchange + extraction — runs in thread pool."""
            token_res = exchange_code_for_token(service_name, code, code_verifier)
            vars = extract_env_vars(service_name, token_res)
            return token_res, vars

        token_response, env_vars = await anyio.to_thread.run_sync(_exchange_tokens)

        # Post-token hooks: run async AFTER exchange but BEFORE server start
        # Collect account_context from post-token hooks
        account_context: dict = {}
        hook = config.get("post_token_hook")
        if hook == "jira_fetch_cloud_id" and "JIRA_API_TOKEN" in env_vars:
            cloud_id = await jira_fetch_cloud_id(env_vars["JIRA_API_TOKEN"])
            if cloud_id:
                env_vars["JIRA_CLOUD_ID"] = cloud_id
                account_context["cloud_id"] = cloud_id
        elif hook == "github_fetch_username" and "GITHUB_PERSONAL_ACCESS_TOKEN" in env_vars:
            username = await github_fetch_username(env_vars["GITHUB_PERSONAL_ACCESS_TOKEN"])
            if username:
                env_vars["GITHUB_USERNAME"] = username
                account_context["authenticated_username"] = username
                logger.info("GitHub username fetched: %s", username)

        def _connect_server():
            """Blocking work: launch MCP server subprocess — runs in thread pool."""
            from app.db.database import SessionLocal
            from app.db.crud import sync_mcp_server_and_tools
            with SessionLocal() as db:
                # Build config_json so this server can be auto-restored on restart
                oauth_config_json = {
                    "type": "oauth",
                    "service_name": service_name,
                    "command": config["mcp_command"],
                    "env": env_vars,
                }
                # Start the subprocess
                tools = mcp_registry.connect_server(
                    server_name=service_name,
                    command=config["mcp_command"],
                    env=env_vars,
                    db=None,  # we'll sync manually below with account_context
                    config_json=oauth_config_json,
                )
                # Persist tools + account_context together
                sync_mcp_server_and_tools(
                    db=db,
                    server_name=service_name,
                    tools=tools,
                    account_context=account_context if account_context else None,
                    config_json=oauth_config_json,
                )
                return tools

        tools = await anyio.to_thread.run_sync(_connect_server)

        n_tools = len(tools)
        tool_names = ", ".join(t.get("name", "?") for t in tools)

        logger.info("%s OAuth success — %d tools: %s", display_name, n_tools, tool_names)

        # Notify frontend via WebSocket
        await ws_manager.broadcast_json({
            "type":    "auth_ready",
            "service": service_name,
            "content": (
                f"✅ **{display_name}** connected successfully!\n"
                f"{n_tools} new tools are now available: {tool_names}\n\n"
                f"You can now use them — just type your request."
            ),
        })

        return HTMLResponse(content=_success_html(display_name, n_tools))

    except Exception as exc:
        logger.exception("OAuth callback failed for %s", service_name)
        await ws_manager.broadcast_json({
            "type":    "error",
            "service": service_name,
            "content": f"❌ Could not connect {display_name}: {exc}",
        })
        return HTMLResponse(
            status_code=500,
            content=_error_html(display_name, str(exc))
        )
