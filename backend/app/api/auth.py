from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse
import logging
import anyio

from app.auth.google_oauth import initiate_oauth_flow, get_google_flow
from app.mcp.manager import mcp_manager
from app.core.connection_manager import manager as ws_manager

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Temporary in-memory state store (in production, use a secure session cookie or DB)
AUTH_STATES = {}

@router.get("/google/login")
def google_login():
    """Triggered by the frontend to start the Google OAuth flow."""
    try:
        state, flow = initiate_oauth_flow()
        AUTH_STATES[state] = flow
        return JSONResponse(content={"message": "Browser opened for authentication", "state": state})
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
    flow = AUTH_STATES[state]
    
    # Clean up state
    del AUTH_STATES[state]
    
    try:
        
        # The OAuthLib fetch_token is synchronous and makes network requests.
        # To avoid blocking the FastAPI async event loop, run it in a thread.
        def fetch():
            flow.fetch_token(authorization_response=str(request.url))
            return flow.credentials
            
        credentials = await anyio.to_thread.run_sync(fetch)
        
        # Initialize the Google API manager with the full credentials object
        def init_apis():
            mcp_manager.initialize(credentials)
        
        await anyio.to_thread.run_sync(init_apis)
        
        # Notify all connected WebSocket clients that auth is complete
        tools = mcp_manager.list_tools()
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
                    <p>The Google Drive MCP Server has been securely initialized.</p>
                    <p>Check your terminal to see the available tools!</p>
                    <p>You can close this tab and return to Aegis.</p>
                    <script>
                        setTimeout(() => window.close(), 3000);
                    </script>
                </body>
            </html>
            """
        )
    except Exception as e:
        logger.error(f"Error during token exchange or MCP initialization: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
