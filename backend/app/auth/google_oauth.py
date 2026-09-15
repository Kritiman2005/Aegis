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

REDIRECT_URI = "http://127.0.0.1:8000/auth/google/callback"

# Every Google catalog entry (Mail, Drive, Docs, Sheets) shares one GCP OAuth
# client — Google issues a single client_id/secret covering however many
# scopes are requested, so there's no reason to make the user register four
# separate apps for one project. Stored under this one shared key.
_GOOGLE_CREDENTIAL_KEY = "google"


def save_google_credentials(client_id: str, client_secret: str) -> None:
    """Saves the user's own Google OAuth app credentials (shared across all
    google_* catalog entries). Called by the Connectors panel's Configure
    step, before the first Connect click on any Google service."""
    from app.db.database import SessionLocal
    from app.db.models import OAuthAppCredential
    with SessionLocal() as db:
        row = (
            db.query(OAuthAppCredential)
            .filter(OAuthAppCredential.service_name == _GOOGLE_CREDENTIAL_KEY)
            .first()
        )
        if row:
            row.client_id = client_id
            row.client_secret = client_secret
        else:
            db.add(OAuthAppCredential(
                service_name=_GOOGLE_CREDENTIAL_KEY, client_id=client_id, client_secret=client_secret
            ))
        db.commit()


def has_google_credentials() -> bool:
    from app.db.database import SessionLocal
    from app.db.models import OAuthAppCredential
    with SessionLocal() as db:
        row = (
            db.query(OAuthAppCredential)
            .filter(OAuthAppCredential.service_name == _GOOGLE_CREDENTIAL_KEY)
            .first()
        )
        return bool(row and row.client_id and row.client_secret)


def get_google_flow(service_name: str) -> Flow:
    """Initialize the Google OAuth Flow using the user's own OAuth app.

    Aegis has no hosted OAuth broker — there is no Aegis-owned Google app
    shared across every install. The user creates their own OAuth client in
    Google Cloud Console and pastes it into the Connectors panel, which
    saves it via save_google_credentials() above.
    """
    from app.db.database import SessionLocal
    from app.db.models import OAuthAppCredential
    with SessionLocal() as db:
        row = (
            db.query(OAuthAppCredential)
            .filter(OAuthAppCredential.service_name == _GOOGLE_CREDENTIAL_KEY)
            .first()
        )
    client_id     = row.client_id if row else None
    client_secret = row.client_secret if row else None

    if not client_id or not client_secret:
        raise ValueError(
            "No Google OAuth app configured yet. Open Connectors, click "
            "Configure on Google Mail or Google Drive, and paste in your "
            "own OAuth client ID and secret from Google Cloud Console."
        )

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
