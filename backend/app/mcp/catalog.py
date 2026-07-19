"""
Aegis — Pre-configured MCP Connectors Catalog

Catalog of all popular reference and community MCP connectors (matching Claude Desktop / MCP Registry).
Provides metadata, default command, environment variable requirements, and input fields for the frontend.
"""

from typing import Dict, List, Optional


CONNECTORS_CATALOG: Dict[str, dict] = {
    # ── 1. Google Workspace ───────────────────────────────────────────────────
    "google_workspace": {
        "name": "google_workspace",
        "display_name": "Google Workspace",
        "category": "Productivity",
        "description": "Access Gmail messages, create drafts, and read Google Drive documents.",
        "icon": "google",
        "auth_type": "oauth",
        "command": None,  # Handled natively via Google OAuth flow
        "env_schema": [],
        "input_schema": [],
        "official": True,
    },

    # ── 2. GitHub ─────────────────────────────────────────────────────────────
    "github": {
        "name": "github",
        "display_name": "GitHub",
        "category": "Developer Tools",
        "description": "Search repos, manage issues, inspect pull requests, and commit code.",
        "icon": "github",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "env_schema": [
            {
                "key": "GITHUB_PERSONAL_ACCESS_TOKEN",
                "label": "Personal Access Token",
                "placeholder": "ghp_xxxxxxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://github.com/settings/tokens"
            }
        ],
        "input_schema": [],
        "official": True,
    },

    # ── 3. PostgreSQL ─────────────────────────────────────────────────────────
    "postgres": {
        "name": "postgres",
        "display_name": "PostgreSQL",
        "category": "Databases",
        "description": "Inspect database schemas, execute read queries, and analyze database tables.",
        "icon": "database",
        "auth_type": "connection_string",
        "command": ["npx", "-y", "@modelcontextprotocol/server-postgres"],
        "env_schema": [],
        "input_schema": [
            {
                "key": "db_url",
                "label": "Database Connection String",
                "placeholder": "postgresql://user:password@localhost:5432/dbname",
                "required": True,
                "secret": True,
                "command_arg_template": "{value}"
            }
        ],
        "official": True,
    },

    # ── 4. Local Filesystem ────────────────────────────────────────────────────
    "filesystem": {
        "name": "filesystem",
        "display_name": "Local Filesystem",
        "category": "System",
        "description": "Allow AI agent to safely read and write allowed local folders.",
        "icon": "folder",
        "auth_type": "path",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        "env_schema": [],
        "input_schema": [
            {
                "key": "allowed_path",
                "label": "Allowed Folder Path",
                "placeholder": "/Users/username/Projects",
                "required": True,
                "secret": False,
                "command_arg_template": "{value}"
            }
        ],
        "official": True,
    },

    # ── 5. Slack ──────────────────────────────────────────────────────────────
    "slack": {
        "name": "slack",
        "display_name": "Slack",
        "category": "Communication",
        "description": "Read channels, inspect thread messages, and post updates to Slack workspaces.",
        "icon": "slack",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@modelcontextprotocol/server-slack"],
        "env_schema": [
            {
                "key": "SLACK_BOT_TOKEN",
                "label": "Bot User OAuth Token",
                "placeholder": "xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://api.slack.com/apps"
            },
            {
                "key": "SLACK_TEAM_ID",
                "label": "Slack Team ID",
                "placeholder": "T01234567",
                "required": True,
                "secret": False,
            }
        ],
        "input_schema": [],
        "official": True,
    },

    # ── 6. Brave Search ───────────────────────────────────────────────────────
    "brave_search": {
        "name": "brave_search",
        "display_name": "Brave Web Search",
        "category": "Web & Search",
        "description": "Perform live web searches and fetch web results via Brave Search API.",
        "icon": "search",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@modelcontextprotocol/server-brave-search"],
        "env_schema": [
            {
                "key": "BRAVE_API_KEY",
                "label": "Brave Search API Key",
                "placeholder": "BSAvxxxxxxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://brave.com/search/api/"
            }
        ],
        "input_schema": [],
        "official": True,
    },

    # ── 7. Fetch (Web Reader) ─────────────────────────────────────────────────
    "fetch": {
        "name": "fetch",
        "display_name": "Web Content Fetcher",
        "category": "Web & Search",
        "description": "Fetch web page HTML/Markdown and convert web pages to readable context.",
        "icon": "globe",
        "auth_type": "none",
        "command": ["uvx", "mcp-server-fetch"],
        "env_schema": [],
        "input_schema": [],
        "official": True,
    },

    # ── 8. Git ────────────────────────────────────────────────────────────────
    "git": {
        "name": "git",
        "display_name": "Git Repository Tools",
        "category": "Developer Tools",
        "description": "Inspect local git repository history, diffs, branches, and commit logs.",
        "icon": "git-branch",
        "auth_type": "path",
        "command": ["uvx", "mcp-server-git"],
        "env_schema": [],
        "input_schema": [
            {
                "key": "repository_path",
                "label": "Git Repo Path",
                "placeholder": "/Users/username/Desktop/Aegis",
                "required": True,
                "secret": False,
                "command_arg_template": "--repository={value}"
            }
        ],
        "official": True,
    },

    # ── 9. Memory (Knowledge Graph) ────────────────────────────────────────────
    "memory": {
        "name": "memory",
        "display_name": "Knowledge Graph Memory",
        "category": "AI Tools",
        "description": "Persistent graph-based memory structure for entities and relationships.",
        "icon": "brain",
        "auth_type": "none",
        "command": ["npx", "-y", "@modelcontextprotocol/server-memory"],
        "env_schema": [],
        "input_schema": [],
        "official": True,
    },

    # ── 10. Time & Timezones ──────────────────────────────────────────────────
    "time": {
        "name": "time",
        "display_name": "Time & Timezone Converter",
        "category": "Utilities",
        "description": "Get current local time, convert timezones, and calculate time offsets.",
        "icon": "clock",
        "auth_type": "none",
        "command": ["uvx", "mcp-server-time"],
        "env_schema": [],
        "input_schema": [],
        "official": True,
    },

    # ── 11. Notion (Community) ────────────────────────────────────────────────
    "notion": {
        "name": "notion",
        "display_name": "Notion",
        "category": "Productivity",
        "description": "Search Notion pages, query databases, and read workspace documentation.",
        "icon": "file-text",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@notionhq/mcp-server-notion"],
        "env_schema": [
            {
                "key": "NOTION_API_KEY",
                "label": "Notion Integration Secret",
                "placeholder": "secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://www.notion.so/my-integrations"
            }
        ],
        "input_schema": [],
        "official": False,
    },

    # ── 12. Linear (Community) ────────────────────────────────────────────────
    "linear": {
        "name": "linear",
        "display_name": "Linear",
        "category": "Productivity",
        "description": "Manage project issues, software cycles, and track project roadmap.",
        "icon": "check-square",
        "auth_type": "api_key",
        "command": ["npx", "-y", "mcp-server-linear"],
        "env_schema": [
            {
                "key": "LINEAR_API_KEY",
                "label": "Linear Personal API Key",
                "placeholder": "lin_api_xxxxxxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://linear.app/settings/api"
            }
        ],
        "input_schema": [],
        "official": False,
    }
}


def get_catalog_list() -> List[dict]:
    """Returns a list of all catalog connectors for frontend display."""
    return list(CONNECTORS_CATALOG.values())


def resolve_connector_command(server_name: str, input_params: dict) -> List[str]:
    """
    Constructs the command line array for a catalog connector by filling template args.
    """
    cat = CONNECTORS_CATALOG.get(server_name)
    if not cat or not cat.get("command"):
        raise ValueError(f"No catalog command configuration found for connector '{server_name}'.")

    cmd = list(cat["command"])
    for field in cat.get("input_schema", []):
        key = field["key"]
        val = input_params.get(key, "").strip()
        if field.get("required") and not val:
            raise ValueError(f"Field '{field['label']}' is required.")
        if val:
            template = field.get("command_arg_template", "{value}")
            arg_str = template.format(value=val)
            cmd.append(arg_str)

    return cmd
