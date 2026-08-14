"""
Aegis — Generic OAuth 2.0 Engine

Provides reusable OAuth 2.0 helpers for all third-party services.
Each service entry in OAUTH_CONFIGS defines:
  - auth_url / token_url / scopes
  - client_id_env / client_secret_env  (env var names — set these in .env)
  - token_auth  ('basic' | 'params' | 'json')
  - env_extraction  (maps env_var_name → path inside token JSON response)
  - mcp_command   (command to launch after successful OAuth)
  - requires_pkce  (True for services like Airtable that enforce PKCE)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, parse_qs

import httpx

logger = logging.getLogger(__name__)

# Base redirect URI — all callbacks land at http://localhost:8000/auth/{service}/callback
REDIRECT_BASE = "http://localhost:8000"


# ─── Service Configs ──────────────────────────────────────────────────────────

OAUTH_CONFIGS: Dict[str, dict] = {

    # ── Slack ──────────────────────────────────────────────────────────────────
    "slack": {
        "display_name": "Slack",
        "auth_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        # Scopes needed by @modelcontextprotocol/server-slack
        "scopes": "channels:read channels:history chat:write im:read im:history mpim:read mpim:history groups:read groups:history users:read",
        "client_id_env": "SLACK_CLIENT_ID",
        "client_secret_env": "SLACK_CLIENT_SECRET",
        "token_auth": "params",       # client_id/secret as form body params
        "token_response_format": "json",
        "mcp_command": ["npx", "-y", "@modelcontextprotocol/server-slack"],
        # Map env var names → dot-path inside token response JSON
        "env_extraction": {
            "SLACK_BOT_TOKEN": ["access_token"],
            "SLACK_TEAM_ID":   ["team", "id"],
        },
        "setup_url": "https://api.slack.com/apps",
        "setup_hint": "Create a Slack App → OAuth & Permissions → copy Client ID & Secret. Set redirect URL to http://localhost:8000/auth/slack/callback",
    },

    # ── Notion ─────────────────────────────────────────────────────────────────
    "notion": {
        "display_name": "Notion",
        "auth_url": "https://api.notion.com/v1/oauth/authorize",
        "token_url": "https://api.notion.com/v1/oauth/token",
        "scopes": "",  # Notion does not use scope param in auth URL
        "client_id_env": "NOTION_CLIENT_ID",
        "client_secret_env": "NOTION_CLIENT_SECRET",
        "token_auth": "basic",        # HTTP Basic Auth: client_id:client_secret
        "token_response_format": "json",
        "mcp_command": ["npx", "-y", "@notionhq/mcp-server-notion"],
        "env_extraction": {
            "NOTION_API_KEY": ["access_token"],
        },
        "auth_extra_params": {
            "owner": "user",
        },
        "setup_url": "https://www.notion.so/my-integrations",
        "setup_hint": "Go to Notion Integrations → New integration → Public → copy Client ID & Secret. Redirect URL: http://localhost:8000/auth/notion/callback",
    },

    # ── Figma ──────────────────────────────────────────────────────────────────
    "figma": {
        "display_name": "Figma",
        "auth_url": "https://www.figma.com/oauth",
        "token_url": "https://api.figma.com/v1/oauth/token",
        "scopes": "",  # Scopes are set per-app in the Figma Developer Portal — leave empty to use the app defaults
        "client_id_env": "FIGMA_CLIENT_ID",
        "client_secret_env": "FIGMA_CLIENT_SECRET",
        "token_auth": "basic",
        "token_response_format": "json",
        "mcp_command": ["npx", "-y", "figma-developer-mcp", "--figma-api-key={FIGMA_ACCESS_TOKEN}"],
        "env_extraction": {
            "FIGMA_ACCESS_TOKEN": ["access_token"],
        },
        "setup_url": "https://www.figma.com/developers/apps",
        "setup_hint": "Create an OAuth App in Figma with callback: http://localhost:8000/auth/figma/callback",
    },

    # ── GitHub ───────────────────────────────────────────────────────────────────────
    "github": {
        "display_name": "GitHub",
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "scopes": "repo read:org read:user gist",
        "client_id_env": "GITHUB_CLIENT_ID",
        "client_secret_env": "GITHUB_CLIENT_SECRET",
        "token_auth": "params",
        "token_response_format": "json",  # Request JSON via Accept header
        "mcp_command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "env_extraction": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": ["access_token"],
        },
        # After token exchange, fetch the authenticated user's login name
        "post_token_hook": "github_fetch_username",
        "setup_url": "https://github.com/settings/applications/new",
        "setup_hint": "GitHub → Settings → Developer settings → OAuth Apps → New → Redirect: http://localhost:8000/auth/github/callback",
    },

    # ── HubSpot ────────────────────────────────────────────────────────────────
    "hubspot": {
        "display_name": "HubSpot CRM",
        "auth_url": "https://app.hubspot.com/oauth/authorize",
        "token_url": "https://api.hubapi.com/oauth/v1/token",
        "scopes": "contacts content reports social automation tickets e-commerce timeline business-intelligence",
        "client_id_env": "HUBSPOT_CLIENT_ID",
        "client_secret_env": "HUBSPOT_CLIENT_SECRET",
        "token_auth": "params",
        "token_response_format": "json",
        "mcp_command": ["npx", "-y", "@hubspot/mcp-server"],
        "env_extraction": {
            "HUBSPOT_ACCESS_TOKEN": ["access_token"],
        },
        "setup_url": "https://app.hubspot.com/developer-docs",
        "setup_hint": "HubSpot → Settings → Integrations → Private Apps → Create → OAuth → Redirect URL: http://localhost:8000/auth/hubspot/callback",
    },

    # ── Linear ─────────────────────────────────────────────────────────────────
    "linear": {
        "display_name": "Linear",
        "auth_url": "https://linear.app/oauth/authorize",
        "token_url": "https://api.linear.app/oauth/token",
        "scopes": "read write",
        "client_id_env": "LINEAR_CLIENT_ID",
        "client_secret_env": "LINEAR_CLIENT_SECRET",
        "token_auth": "params",
        "token_response_format": "json",
        "mcp_command": ["npx", "-y", "mcp-server-linear"],
        "env_extraction": {
            "LINEAR_API_KEY": ["access_token"],
        },
        "setup_url": "https://linear.app/settings/api",
        "setup_hint": "Linear → Settings → API → OAuth Applications → New → Redirect: http://localhost:8000/auth/linear/callback",
    },

    # ── Airtable ───────────────────────────────────────────────────────────────
    "airtable": {
        "display_name": "Airtable",
        "auth_url": "https://airtable.com/oauth2/v1/authorize",
        "token_url": "https://airtable.com/oauth2/v1/token",
        "scopes": "data.records:read data.records:write schema.bases:read schema.bases:write",
        "client_id_env": "AIRTABLE_CLIENT_ID",
        "client_secret_env": "AIRTABLE_CLIENT_SECRET",
        "token_auth": "basic",
        "token_response_format": "json",
        "mcp_command": ["npx", "-y", "airtable-mcp-server"],
        "env_extraction": {
            "AIRTABLE_API_KEY": ["access_token"],
        },
        "requires_pkce": True,        # Airtable mandates PKCE
        "setup_url": "https://airtable.com/create/oauth",
        "setup_hint": "Airtable → Account → Developer hub → New OAuth integration → Redirect: http://localhost:8000/auth/airtable/callback",
    },

    # ── Jira (Atlassian) ───────────────────────────────────────────────────────
    "jira": {
        "display_name": "Jira (Atlassian)",
        "auth_url": "https://auth.atlassian.com/authorize",
        "token_url": "https://auth.atlassian.com/oauth/token",
        "scopes": "read:jira-work write:jira-work read:jira-user offline_access",
        "client_id_env": "ATLASSIAN_CLIENT_ID",
        "client_secret_env": "ATLASSIAN_CLIENT_SECRET",
        "token_auth": "json",         # POST JSON body with client_id/secret
        "token_response_format": "json",
        "mcp_command": ["npx", "-y", "mcp-jira-server"],
        "env_extraction": {
            "JIRA_API_TOKEN": ["access_token"],
        },
        "auth_extra_params": {
            "audience": "api.atlassian.com",
            "prompt": "consent",
        },
        # After token exchange, fetch the user's Jira cloud ID
        "post_token_hook": "jira_fetch_cloud_id",
        "setup_url": "https://developer.atlassian.com/console/myapps/",
        "setup_hint": "Atlassian Developer Console → Create app → OAuth 2.0 → Add Jira scopes → Redirect: http://localhost:8000/auth/jira/callback",
    },

    # ── Salesforce ─────────────────────────────────────────────────────────────
    "salesforce": {
        "display_name": "Salesforce CRM",
        "auth_url": "https://login.salesforce.com/services/oauth2/authorize",
        "token_url": "https://login.salesforce.com/services/oauth2/token",
        "scopes": "api refresh_token offline_access",
        "client_id_env": "SALESFORCE_CLIENT_ID",
        "client_secret_env": "SALESFORCE_CLIENT_SECRET",
        "token_auth": "params",
        "token_response_format": "json",
        "mcp_command": ["uvx", "--from", "mcp-salesforce-connector", "mcp-salesforce"],
        "env_extraction": {
            "SALESFORCE_ACCESS_TOKEN":  ["access_token"],
            "SALESFORCE_INSTANCE_URL":  ["instance_url"],
        },
        "setup_url": "https://trailhead.salesforce.com/content/learn/modules/connected-app-basics",
        "setup_hint": "Salesforce Setup → Apps → App Manager → New Connected App → Enable OAuth → Redirect: http://localhost:8000/auth/salesforce/callback",
    },
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_client_credentials(service_name: str) -> Tuple[str, str]:
    """Reads CLIENT_ID and CLIENT_SECRET from environment for a service."""
    config = OAUTH_CONFIGS.get(service_name)
    if not config:
        raise ValueError(f"Unknown OAuth service: '{service_name}'")

    client_id     = os.environ.get(config["client_id_env"], "").strip()
    client_secret = os.environ.get(config["client_secret_env"], "").strip()

    if not client_id or not client_secret:
        raise ValueError(
            f"Missing OAuth credentials for {config['display_name']}. "
            f"Please set {config['client_id_env']} and {config['client_secret_env']} in your .env file. "
            f"Get them here: {config.get('setup_url', '')}"
        )
    return client_id, client_secret


def generate_pkce_pair() -> Tuple[str, str]:
    """Generates a PKCE code_verifier + code_challenge (S256) pair."""
    code_verifier  = secrets.token_urlsafe(96)
    digest         = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_auth_url(
    service_name: str,
    state: str,
    code_challenge: Optional[str] = None,
) -> str:
    """Constructs the OAuth 2.0 authorization URL for the given service."""
    config    = OAUTH_CONFIGS[service_name]
    client_id, _ = get_client_credentials(service_name)
    redirect_uri  = f"{REDIRECT_BASE}/auth/{service_name}/callback"

    params: Dict[str, str] = {
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "state":         state,
    }

    if config.get("scopes"):
        params["scope"] = config["scopes"]

    # Merge any service-specific extra params
    params.update(config.get("auth_extra_params", {}))

    # PKCE — required by Airtable, optional for others
    if config.get("requires_pkce") and code_challenge:
        params["code_challenge"]        = code_challenge
        params["code_challenge_method"] = "S256"

    return f"{config['auth_url']}?{urlencode(params)}"


def exchange_code_for_token(
    service_name: str,
    code: str,
    code_verifier: Optional[str] = None,
) -> dict:
    """
    Exchanges an OAuth authorization code for tokens.
    Handles three exchange styles:
      'basic'  → HTTP Basic Auth (Notion, Airtable)
      'json'   → JSON body (Atlassian)
      'params' → form-encoded body params (Slack, GitHub, HubSpot, Linear, Salesforce)
    """
    config        = OAUTH_CONFIGS[service_name]
    client_id, client_secret = get_client_credentials(service_name)
    redirect_uri  = f"{REDIRECT_BASE}/auth/{service_name}/callback"

    payload: Dict[str, str] = {
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        payload["code_verifier"] = code_verifier

    token_auth = config.get("token_auth", "params")

    with httpx.Client(timeout=30) as client:
        if token_auth == "basic":
            response = client.post(
                config["token_url"],
                data=payload,
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
            )
        elif token_auth == "json":
            payload.update({"client_id": client_id, "client_secret": client_secret})
            response = client.post(
                config["token_url"],
                json=payload,
                headers={"Accept": "application/json"},
            )
        else:  # "params" — form-encoded with credentials in body
            payload.update({"client_id": client_id, "client_secret": client_secret})
            response = client.post(
                config["token_url"],
                data=payload,
                headers={"Accept": "application/json"},
            )

    # GitHub returns URL-encoded text unless we request JSON explicitly
    if config.get("token_response_format") == "form":
        parsed = parse_qs(response.text)
        # parse_qs returns lists — unwrap first element of each
        return {k: v[0] if isinstance(v, list) and v else v for k, v in parsed.items()}

    response.raise_for_status()
    return response.json()


def extract_env_vars(service_name: str, token_response: dict) -> dict:
    """
    Walks the token_response JSON using the paths in env_extraction
    and returns a dict of env var name → value ready for the MCP subprocess.
    """
    config  = OAUTH_CONFIGS[service_name]
    env_vars: Dict[str, str] = {}

    for env_key, path in config.get("env_extraction", {}).items():
        value: any = token_response
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                value = None
                break
        if value is not None and not isinstance(value, dict):
            env_vars[env_key] = str(value)
        else:
            logger.warning(
                "Could not extract %s from token response for %s (path: %s)",
                env_key, service_name, path
            )

    return env_vars


async def jira_fetch_cloud_id(access_token: str) -> Optional[str]:
    """
    Post-token hook for Jira: calls the Atlassian accessible-resources
    API to retrieve the first available Jira cloud ID.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            resources = response.json()
            if resources:
                cloud_id = resources[0].get("id")
                logger.info("Jira cloud ID fetched: %s", cloud_id)
                return cloud_id
    except Exception as e:
        logger.warning("Could not fetch Jira cloud ID: %s", e)
    return None


async def github_fetch_username(access_token: str) -> Optional[str]:
    """
    Post-token hook for GitHub: fetches the authenticated user's login name
    via GET /user so it can be injected as GITHUB_USERNAME into the MCP env.
    This lets the planner construct correct search queries like 'user:Kritiman2005'.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            data = response.json()
            username = data.get("login")
            logger.info("GitHub username fetched: %s", username)
            return username
    except Exception as e:
        logger.warning("Could not fetch GitHub username: %s", e)
    return None
