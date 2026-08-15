import os
import webbrowser
from google_auth_oauthlib.flow import Flow

# Allow HTTP callback for local development and relax scope checks
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

# Scopes for Gmail
GMAIL_SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose'
]

# Scopes for Google Drive
DRIVE_SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file'
]

# Scopes for Google Sheets
SHEETS_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets'
]

# Scopes for Google Docs
DOCS_SCOPES = [
    'https://www.googleapis.com/auth/documents.readonly'
]

REDIRECT_URI = "http://localhost:8000/auth/google/callback"

def get_google_flow(service_name: str) -> Flow:
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
    
    if service_name == "google_mail":
        scopes = GMAIL_SCOPES
    elif service_name == "google_drive":
        scopes = DRIVE_SCOPES
    elif service_name == "google_sheets":
        scopes = SHEETS_SCOPES
    elif service_name == "google_docs":
        scopes = DOCS_SCOPES
    else:
        scopes = GMAIL_SCOPES + DRIVE_SCOPES + SHEETS_SCOPES + DOCS_SCOPES
    
    flow = Flow.from_client_config(
        client_config,
        scopes=scopes,
        redirect_uri=REDIRECT_URI
    )
    return flow

def initiate_oauth_flow(service_name: str):
    """Generates the OAuth URL for the user to visit."""
    flow = get_google_flow(service_name)
    
    # Generate the authorization URL (this internally creates a code_verifier for PKCE)
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent' # Force consent to ensure we get a refresh token
    )
    
    return auth_url, state, flow
