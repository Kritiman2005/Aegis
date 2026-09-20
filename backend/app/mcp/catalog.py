"""
Aegis — MCP Connectors Catalog (free, built-in tier)

Only the connectors that need no external account/credential at all (or, for
filesystem/git, just a local path) live here — these ship free in every
install. Everything that needs a real external account (Slack, Notion,
HubSpot, GitHub, Postgres, AWS, ...) now lives exclusively on the website's
connector catalog (aegisaistudio.online/connectors) and reaches a given
install only via RemoteConnector sync — see get_merged_catalog below.

Each entry includes:
  - Verified npm / uvx command
  - Auth type (oauth | api_key | connection_string | path | none)
  - env_schema: environment variables the user must provide (API keys, tokens)
  - input_schema: CLI arguments built from user input (domains, paths)
  - target_audience: who this connector is most useful for
  - official: True if published by the official vendor
"""

from typing import Dict, List, Optional

# Google's four catalog entries (mail/drive/docs/sheets) share one GCP OAuth
# client under this key — see google_oauth.py's _GOOGLE_CREDENTIAL_KEY.
_GOOGLE_OAUTH_SERVICE_KEY = "google"
_GOOGLE_SETUP_URL = "https://console.cloud.google.com/apis/credentials"
_GOOGLE_SETUP_HINT = (
    "Google Cloud Console → Credentials → Create Credentials → OAuth client ID "
    "→ Application type: Desktop app. Redirect URI: http://127.0.0.1:8000/auth/google/callback"
)


CONNECTORS_CATALOG: Dict[str, dict] = {

    # ═══════════════════════════════════════════════════════════════════════════
    # 🤖  FREE, BUILT-IN TOOLS
    # ═══════════════════════════════════════════════════════════════════════════
    # Everything that needs a real external account/credential (Slack, Notion,
    # HubSpot, GitHub, Postgres, ...) now lives exclusively on the website's
    # connector catalog (aegisaistudio.online/connectors → synced in via
    # RemoteConnector, see get_merged_catalog below) — a single paid tier
    # unlocks picking from that whole set. What's left here is only the
    # handful that need no external account at all (or, for filesystem/git,
    # just a local path) — these ship free in every install, no website
    # involved.

    "fetch": {
        "name": "fetch",
        "display_name": "Web Page Reader",
        "category": "AI & Productivity",
        "description": "Read any public web page and extract its content — useful for reading articles, competitor sites, and job posts.",
        "icon": "globe",
        "auth_type": "none",
        "command": ["uvx", "mcp-server-fetch"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["marketing", "hr", "all"],
        "official": True,
    },

    "memory": {
        "name": "memory",
        "display_name": "Session Memory",
        "category": "AI & Productivity",
        "description": "Lets the AI remember important details, key facts, and named entities across your conversation.",
        "icon": "brain",
        "auth_type": "none",
        "command": ["npx", "-y", "@modelcontextprotocol/server-memory"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["hr", "marketing", "all"],
        "official": True,
    },

    "sequential_thinking": {
        "name": "sequential_thinking",
        "display_name": "Step-by-Step Reasoning",
        "category": "AI & Productivity",
        "description": "Helps the AI break complex problems into clear steps and reason more carefully.",
        "icon": "cpu",
        "auth_type": "none",
        "command": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["all"],
        "official": True,
    },

    "time": {
        "name": "time",
        "display_name": "Time & Timezone Helper",
        "category": "AI & Productivity",
        "description": "Get current time, convert between timezones, and help schedule meetings across regions.",
        "icon": "clock",
        "auth_type": "none",
        "command": ["uvx", "mcp-server-time"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["hr", "marketing", "operations", "all"],
        "official": True,
    },

    "filesystem": {
        "name": "filesystem",
        "display_name": "Local Files & Folders",
        "category": "AI & Productivity",
        "description": "Let the AI read and write files in a specific folder on your computer.",
        "icon": "folder",
        "auth_type": "path",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        "env_schema": [],
        "input_schema": [
            {
                "key": "allowed_path",
                "label": "Folder to Allow Access To",
                "placeholder": "/Users/yourname/Documents/Work",
                "required": True,
                "secret": False,
                "command_arg_template": "{value}"
            }
        ],
        "target_audience": ["hr", "marketing", "operations", "all"],
        "official": True,
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # 🧑‍💻  DEVELOPER TOOLS  (local-only — needs a path, not an account)
    # ═══════════════════════════════════════════════════════════════════════════

    "git": {
        "name": "git",
        "display_name": "Git Repository Tools",
        "category": "Developer Tools",
        "description": "Inspect local git history, diffs, branches and commit logs.",
        "icon": "git-branch",
        "auth_type": "path",
        "command": ["uvx", "mcp-server-git"],
        "env_schema": [],
        "input_schema": [
            {
                "key": "repository_path",
                "label": "Local Git Repository Path",
                "placeholder": "/Users/username/Desktop/MyProject",
                "required": True,
                "secret": False,
                "command_arg_template": "--repository={value}"
            }
        ],
        "target_audience": ["developer"],
        "official": True,
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _oauth_env_schema(oauth_service: Optional[str]) -> tuple[List[dict], str]:
    """Shared by _apply_oauth_app_fields (static entries) and
    remote_entry_to_catalog_dict (public-catalog entries) — same
    "bring your own OAuth app" env_schema shape and help_url lookup, just
    usable per-entry instead of only at import time over the static dict."""
    from app.auth.oauth_service import OAUTH_CONFIGS

    if oauth_service and oauth_service in OAUTH_CONFIGS:
        cfg = OAUTH_CONFIGS[oauth_service]
        help_url = cfg.get("setup_url", "")
        setup_hint = cfg.get("setup_hint", "")
    else:
        help_url = _GOOGLE_SETUP_URL
        setup_hint = _GOOGLE_SETUP_HINT

    return (
        [
            {"key": "OAUTH_CLIENT_ID", "label": "OAuth Client ID", "required": True, "help_url": help_url},
            {"key": "OAUTH_CLIENT_SECRET", "label": "OAuth Client Secret", "required": True, "secret": True, "help_url": help_url},
        ],
        setup_hint,
    )


def _apply_oauth_app_fields() -> None:
    """
    Aegis has no hosted OAuth broker and runs no shared app on anyone's
    behalf — every auth_type=="oauth" entry needs the USER'S OWN OAuth app
    credentials. This populates env_schema with the same
    OAUTH_CLIENT_ID/OAUTH_CLIENT_SECRET field shape used by api_key
    connectors, so the existing CatalogCard UI renders the input form here
    too — the submit action calls /api/connectors/oauth/{name}/configure
    (or /auth/google/configure for Google) instead of spawning a process.
    """
    for entry in CONNECTORS_CATALOG.values():
        if entry.get("auth_type") != "oauth":
            continue
        entry["env_schema"], entry["setup_hint"] = _oauth_env_schema(entry.get("oauth_service"))


_apply_oauth_app_fields()


def remote_entry_to_catalog_dict(rc, plan: str = "free") -> dict:
    """
    Converts a RemoteConnector row (a mirror of one row of
    aegisaistudio.online's public connector catalog — see
    app/db/models.py's RemoteConnector and
    app/auth/supabase_client.py's get_full_catalog) into the same dict shape
    as a CONNECTORS_CATALOG entry, so it can sit in the same list and be
    rendered/connected through identically. `rc` is typed loosely (not
    imported here) to avoid a models.py <-> catalog.py import cycle.

    `plan` is the account's own cached plan ("free"/"paid") — every remote
    entry is `locked` unless it's "paid". There's no per-connector
    selection anymore: one subscription unlocks the whole catalog, so the
    only thing this function decides is whether THIS account's plan
    unlocks it, not whether the connector was individually chosen.
    """
    import json

    entry: dict = {
        "name": rc.id,
        "display_name": rc.display_name,
        "category": rc.category,
        "description": rc.description,
        "icon": rc.icon,
        "auth_type": rc.auth_type,
        "command": json.loads(rc.command_json) if rc.command_json else None,
        "env_schema": json.loads(rc.env_schema_json or "[]"),
        "input_schema": json.loads(rc.input_schema_json or "[]"),
        "oauth_service": rc.oauth_service,
        "setup_guide": rc.setup_guide,
        "target_audience": ["all"],
        "official": False,
        "remote": True,  # lets the frontend show "from aegisaistudio.online" instead of a built-in badge
        "needs_reconnect": rc.needs_reconnect,
        "locked": plan != "paid",
    }
    if entry["auth_type"] == "oauth":
        entry["env_schema"], entry["setup_hint"] = _oauth_env_schema(rc.oauth_service)
    return entry


def get_merged_catalog(remote_entries: List[dict]) -> Dict[str, dict]:
    """CONNECTORS_CATALOG plus already-converted remote entries (see
    remote_entry_to_catalog_dict), keyed by name — the single dict callers
    resolving a connector by name should look in once remote ones exist, so
    a website-selected connector behaves identically to a built-in one for
    command resolution and the oauth-connect gate."""
    merged = dict(CONNECTORS_CATALOG)
    for entry in remote_entries:
        merged[entry["name"]] = entry
    return merged


def get_catalog_list() -> List[dict]:
    """Returns all connectors for frontend display."""
    return list(CONNECTORS_CATALOG.values())


def get_catalog_for_audience(audience: str) -> List[dict]:
    """
    Returns connectors filtered by target audience.
    audience: 'hr' | 'marketing' | 'sales' | 'operations' | 'developer' | 'all'
    """
    return [
        item for item in CONNECTORS_CATALOG.values()
        if audience in item.get("target_audience", []) or "all" in item.get("target_audience", [])
    ]


def resolve_connector_command(server_name: str, input_params: dict, catalog: Optional[Dict[str, dict]] = None) -> List[str]:
    """
    Constructs the full command array for a catalog connector,
    filling in template args from user-provided input_params. Pass a merged
    catalog (see get_merged_catalog) to also resolve a website-selected
    remote connector, not just a built-in one.
    """
    cat = (catalog if catalog is not None else CONNECTORS_CATALOG).get(server_name)
    if not cat or not cat.get("command"):
        raise ValueError(f"No catalog command configuration found for '{server_name}'.")

    cmd = list(cat["command"])
    for field in cat.get("input_schema", []):
        key = field["key"]
        val = (input_params or {}).get(key, "").strip()
        if field.get("required") and not val:
            raise ValueError(f"Required field missing: '{field['label']}'.")
        if val:
            template = field.get("command_arg_template", "{value}")
            cmd.append(template.format(value=val))

    return cmd
