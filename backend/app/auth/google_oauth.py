import os
import webbrowser
from google_auth_oauthlib.flow import Flow

# Allow HTTP callback for local development
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Required scopes for Gmail and Google Drive (includes drafting and creating files)
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file'
]

REDIRECT_URI = "http://localhost:8000/auth/google/callback"

def get_google_flow() -> Flow:
    """Initialize the Google OAuth Flow using environment variables."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in environment variables")
        
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    return flow

def initiate_oauth_flow():
    """Generates the OAuth URL and opens the user's default system browser."""
    flow = get_google_flow()
    
    # Generate the authorization URL (this internally creates a code_verifier for PKCE)
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent' # Force consent to ensure we get a refresh token
    )
    
    # Open the system's default browser to the Google login page
    print(f"Opening browser for Google Authentication...")
    webbrowser.open(auth_url)
    
    return state, flow
