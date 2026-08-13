from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
import logging
import anyio

from app.auth.google_oauth import initiate_oauth_flow, get_google_flow
from app.mcp.registry import mcp_registry
from app.core.connection_manager import manager as ws_manager

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Temporary in-memory state store (in production, use a secure session cookie or DB)
AUTH_STATES = {}

@router.get("/google/login")
def google_login(service: str = "google_workspace"):
    """Triggered by the frontend to start the Google OAuth flow."""
    try:
        from app.auth.google_oauth import initiate_oauth_flow
        auth_url, state, flow = initiate_oauth_flow(service_name=service)
        # Store both flow and service name
        AUTH_STATES[state] = {"flow": flow, "service": service}
        return RedirectResponse(url=auth_url)
    except Exception as e:
        logger.error(f"Error initiating OAuth flow: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/google/callback")
async def google_callback(request: Request):
    """The local listener route that catches Google's redirect."""
    state = request.query_params.get("state")
    code = request.query_params.get("code")
    
    if not state or state not in AUTH_STATES:
        return JSONResponse(status_code=400, content={"error": "Invalid or missing state"})
    if not code:
        return JSONResponse(status_code=400, content={"error": "Missing authorization code"})
        
    # Get the original flow object (which contains the PKCE code_verifier)
    state_data = AUTH_STATES[state]
    flow = state_data["flow"]
    service_name = state_data["service"]
    
    # Clean up state
    del AUTH_STATES[state]
    
    try:
        
        # The OAuthLib fetch_token is synchronous and makes network requests.
        # To avoid blocking the FastAPI async event loop, run it in a thread.
        def fetch():
            flow.fetch_token(authorization_response=str(request.url))
            return flow.credentials
            
        credentials = await anyio.to_thread.run_sync(fetch)
        
        # Initialize the Google Workspace stdio MCP server via registry
        def init_apis_and_db():
            from app.db.database import SessionLocal
            from app.db.crud import save_google_user_and_credentials
            with SessionLocal() as db:
                save_google_user_and_credentials(
                    db=db,
                    credentials=credentials,
                    service_name=service_name
                )
                tools = mcp_registry.connect_google_service(
                    service_name=service_name,
                    credentials_json_str=credentials.to_json(),
                    db=db
                )
                return tools

        tools = await anyio.to_thread.run_sync(init_apis_and_db)

        # Notify all connected WebSocket clients that auth is complete
        tool_names = ", ".join(t["name"] for t in tools)
        await ws_manager.broadcast_json({
            "type": "auth_ready",
            "content": f"✅ Google authentication successful! {len(tools)} tools are now available: {tool_names}\n\nYou can now type your request below."
        })
        
        # Return a friendly HTML response so the user can close the browser tab
        return HTMLResponse(
            content="""
            <html>
                <head><title>Authentication Successful</title></head>
                <body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
                    <h1>Authentication Successful!</h1>
                    <p>You can safely close this tab and return to Aegis.</p>
                    <button onclick="window.close()" style="padding: 10px 20px; font-size: 16px; cursor: pointer; border-radius: 8px; background: #000; color: #fff; border: none; margin-top: 20px;">Close Window</button>
                    <script>
                        if (window.opener) { window.opener.postMessage('auth_success', '*'); }
                        window.close();
                    </script>
                </body>
            </html>
            """
        )
    except Exception as e:
        logger.error(f"Error during token exchange or MCP initialization: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
