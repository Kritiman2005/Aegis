"""
Aegis — Comprehensive Pre-configured MCP Connectors Catalog

Complete catalog of 30+ official reference and top community MCP connectors
(matching Claude Desktop, Glama, MCP.so, and modelcontextprotocol/servers).
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

    # ── 3. GitLab ─────────────────────────────────────────────────────────────
    "gitlab": {
        "name": "gitlab",
        "display_name": "GitLab",
        "category": "Developer Tools",
        "description": "Interact with GitLab projects, merge requests, issues, and repositories.",
        "icon": "gitlab",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@modelcontextprotocol/server-gitlab"],
        "env_schema": [
            {
                "key": "GITLAB_PERSONAL_ACCESS_TOKEN",
                "label": "GitLab Personal Access Token",
                "placeholder": "glpat-xxxxxxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://gitlab.com/-/profile/personal_access_tokens"
            },
            {
                "key": "GITLAB_API_URL",
                "label": "GitLab Instance URL (Optional)",
                "placeholder": "https://gitlab.com/api/v4",
                "required": False,
                "secret": False,
            }
        ],
        "input_schema": [],
        "official": True,
    },

    # ── 4. PostgreSQL ─────────────────────────────────────────────────────────
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

    # ── 5. MySQL ──────────────────────────────────────────────────────────────
    "mysql": {
        "name": "mysql",
        "display_name": "MySQL / MariaDB",
        "category": "Databases",
        "description": "Connect to MySQL or MariaDB databases for schema inspection and queries.",
        "icon": "database",
        "auth_type": "connection_string",
        "command": ["npx", "-y", "mcp-server-mysql"],
        "env_schema": [
            {
                "key": "MYSQL_URI",
                "label": "MySQL Connection URI",
                "placeholder": "mysql://user:password@localhost:3306/dbname",
                "required": True,
                "secret": True,
            }
        ],
        "input_schema": [],
        "official": False,
    },

    # ── 6. SQLite ─────────────────────────────────────────────────────────────
    "sqlite": {
        "name": "sqlite",
        "display_name": "SQLite Database",
        "category": "Databases",
        "description": "Read and query local SQLite database files.",
        "icon": "database",
        "auth_type": "path",
        "command": ["npx", "-y", "@modelcontextprotocol/server-sqlite"],
        "env_schema": [],
        "input_schema": [
            {
                "key": "db_path",
                "label": "SQLite File Path",
                "placeholder": "/Users/username/data.db",
                "required": True,
                "secret": False,
                "command_arg_template": "--db-path={value}"
            }
        ],
        "official": True,
    },

    # ── 7. Redis ──────────────────────────────────────────────────────────────
    "redis": {
        "name": "redis",
        "display_name": "Redis Store",
        "category": "Databases",
        "description": "Inspect keys, query data structures, and manage Redis memory stores.",
        "icon": "database",
        "auth_type": "connection_string",
        "command": ["npx", "-y", "redis-mcp-server"],
        "env_schema": [
            {
                "key": "REDIS_URL",
                "label": "Redis Connection URL",
                "placeholder": "redis://:password@localhost:6379/0",
                "required": True,
                "secret": True,
            }
        ],
        "input_schema": [],
        "official": False,
    },

    # ── 8. Elasticsearch ──────────────────────────────────────────────────────
    "elasticsearch": {
        "name": "elasticsearch",
        "display_name": "Elasticsearch",
        "category": "Databases",
        "description": "Search indices, execute query DSL, and analyze document clusters.",
        "icon": "search",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@elastic/mcp-server-elasticsearch"],
        "env_schema": [
            {
                "key": "ES_URL",
                "label": "Elasticsearch Endpoint Cluster URL",
                "placeholder": "https://my-cluster.es.amazonaws.com:9243",
                "required": True,
                "secret": False,
            },
            {
                "key": "ES_API_KEY",
                "label": "API Key",
                "placeholder": "V1V4...key...",
                "required": True,
                "secret": True,
            }
        ],
        "input_schema": [],
        "official": True,
    },

    # ── 9. Local Filesystem ────────────────────────────────────────────────────
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

    # ── 10. Slack ─────────────────────────────────────────────────────────────
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

    # ── 11. Discord ───────────────────────────────────────────────────────────
    "discord": {
        "name": "discord",
        "display_name": "Discord",
        "category": "Communication",
        "description": "Manage Discord channels, send bot announcements, and read messages.",
        "icon": "message-square",
        "auth_type": "api_key",
        "command": ["npx", "-y", "mcp-discord-server"],
        "env_schema": [
            {
                "key": "DISCORD_BOT_TOKEN",
                "label": "Discord Bot Token",
                "placeholder": "MTEyMzQ1...token...",
                "required": True,
                "secret": True,
                "help_url": "https://discord.com/developers/applications"
            }
        ],
        "input_schema": [],
        "official": False,
    },

    # ── 12. Brave Search ──────────────────────────────────────────────────────
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

    # ── 13. Fetch (Web Reader) ────────────────────────────────────────────────
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

    # ── 14. Puppeteer (Web Automation) ────────────────────────────────────────
    "puppeteer": {
        "name": "puppeteer",
        "display_name": "Puppeteer Browser Automation",
        "category": "Web & Search",
        "description": "Automate headless Chrome browsing, take page screenshots, and evaluate scripts.",
        "icon": "chrome",
        "auth_type": "none",
        "command": ["npx", "-y", "@modelcontextprotocol/server-puppeteer"],
        "env_schema": [],
        "input_schema": [],
        "official": True,
    },

    # ── 15. Google Maps ───────────────────────────────────────────────────────
    "google_maps": {
        "name": "google_maps",
        "display_name": "Google Maps Location & Places",
        "category": "Web & Search",
        "description": "Search places, calculate transit routes, and lookup location metadata.",
        "icon": "map-pin",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@modelcontextprotocol/server-google-maps"],
        "env_schema": [
            {
                "key": "GOOGLE_MAPS_API_KEY",
                "label": "Google Maps API Key",
                "placeholder": "AIzaSy...key...",
                "required": True,
                "secret": True,
                "help_url": "https://console.cloud.google.com/google/maps-apis"
            }
        ],
        "input_schema": [],
        "official": True,
    },

    # ── 16. Git ───────────────────────────────────────────────────────────────
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

    # ── 17. Sentry ────────────────────────────────────────────────────────────
    "sentry": {
        "name": "sentry",
        "display_name": "Sentry Error Monitoring",
        "category": "Developer Tools",
        "description": "Inspect runtime crash tracebacks, issues, and production error logs.",
        "icon": "alert-triangle",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@sentry/mcp-server"],
        "env_schema": [
            {
                "key": "SENTRY_AUTH_TOKEN",
                "label": "Sentry Auth Token",
                "placeholder": "sntrys_...token...",
                "required": True,
                "secret": True,
                "help_url": "https://sentry.io/settings/account/api/auth-tokens/"
            },
            {
                "key": "SENTRY_ORG",
                "label": "Sentry Organization Slug",
                "placeholder": "my-company-slug",
                "required": True,
                "secret": False,
            }
        ],
        "input_schema": [],
        "official": True,
    },

    # ── 18. AWS S3 ────────────────────────────────────────────────────────────
    "aws_s3": {
        "name": "aws_s3",
        "display_name": "AWS S3 Cloud Storage",
        "category": "Cloud & Infra",
        "description": "List S3 buckets, inspect cloud objects, and read cloud storage files.",
        "icon": "cloud",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@modelcontextprotocol/server-aws-s3"],
        "env_schema": [
            {
                "key": "AWS_ACCESS_KEY_ID",
                "label": "AWS Access Key ID",
                "placeholder": "AKIAIOSFODNN7EXAMPLE",
                "required": True,
                "secret": False,
            },
            {
                "key": "AWS_SECRET_ACCESS_KEY",
                "label": "AWS Secret Access Key",
                "placeholder": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "required": True,
                "secret": True,
            },
            {
                "key": "AWS_REGION",
                "label": "AWS Region",
                "placeholder": "us-east-1",
                "required": True,
                "secret": False,
            }
        ],
        "input_schema": [],
        "official": True,
    },

    # ── 19. Docker ────────────────────────────────────────────────────────────
    "docker": {
        "name": "docker",
        "display_name": "Docker Engine",
        "category": "Cloud & Infra",
        "description": "Inspect running containers, check image layers, and view container logs.",
        "icon": "container",
        "auth_type": "none",
        "command": ["npx", "-y", "mcp-server-docker"],
        "env_schema": [],
        "input_schema": [],
        "official": False,
    },

    # ── 20. Memory (Knowledge Graph) ────────────────────────────────────────────
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

    # ── 21. Sequential Thinking ───────────────────────────────────────────────
    "sequential_thinking": {
        "name": "sequential_thinking",
        "display_name": "Sequential Thinking Reasoning",
        "category": "AI Tools",
        "description": "Enables multi-step step-by-step analytical reasoning and plan reflection.",
        "icon": "cpu",
        "auth_type": "none",
        "command": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
        "env_schema": [],
        "input_schema": [],
        "official": True,
    },

    # ── 22. Time & Timezones ──────────────────────────────────────────────────
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

    # ── 23. Notion ────────────────────────────────────────────────────────────
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

    # ── 24. Linear ────────────────────────────────────────────────────────────
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
    },

    # ── 25. Jira (Atlassian) ──────────────────────────────────────────────────
    "jira": {
        "name": "jira",
        "display_name": "Jira Software",
        "category": "Productivity",
        "description": "Search Jira tickets, manage issue boards, and track sprint progress.",
        "icon": "trello",
        "auth_type": "api_key",
        "command": ["npx", "-y", "mcp-jira-server"],
        "env_schema": [
            {
                "key": "JIRA_API_TOKEN",
                "label": "Atlassian API Token",
                "placeholder": "ATATT3xFfGF0...token...",
                "required": True,
                "secret": True,
                "help_url": "https://id.atlassian.com/manage-profile/security/api-tokens"
            },
            {
                "key": "JIRA_HOST",
                "label": "Jira Domain URL",
                "placeholder": "https://yourcompany.atlassian.net",
                "required": True,
                "secret": False,
            },
            {
                "key": "JIRA_EMAIL",
                "label": "Atlassian Account Email",
                "placeholder": "user@company.com",
                "required": True,
                "secret": False,
            }
        ],
        "input_schema": [],
        "official": False,
    },

    # ── 26. Figma ─────────────────────────────────────────────────────────────
    "figma": {
        "name": "figma",
        "display_name": "Figma Design",
        "category": "Design & UI",
        "description": "Inspect design frames, extract component specs, and export design tokens.",
        "icon": "figma",
        "auth_type": "api_key",
        "command": ["npx", "-y", "figma-developer-mcp"],
        "env_schema": [
            {
                "key": "FIGMA_PERSONAL_ACCESS_TOKEN",
                "label": "Figma Personal Access Token",
                "placeholder": "figd_...token...",
                "required": True,
                "secret": True,
                "help_url": "https://www.figma.com/developers/api#access-tokens"
            }
        ],
        "input_schema": [],
        "official": False,
    },

    # ── 27. Tailwind CSS ──────────────────────────────────────────────────────
    "tailwind": {
        "name": "tailwind",
        "display_name": "Tailwind CSS Documentation & Helper",
        "category": "Design & UI",
        "description": "Lookup Tailwind utility classes, search color tokens, and get design class recommendations.",
        "icon": "code",
        "auth_type": "none",
        "command": ["npx", "-y", "tailwindcss-mcp-server"],
        "env_schema": [],
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
