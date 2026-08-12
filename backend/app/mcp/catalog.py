"""
Aegis — MCP Connectors Catalog (HR & Marketing Edition)

Curated for non-technical users in HR, Marketing, Sales, Customer Success, and Operations.
Every connector here maps to a tool real HR/Marketing teams use daily.

Each entry includes:
  - Verified npm / uvx command
  - Auth type (oauth | api_key | connection_string | path | none)
  - env_schema: environment variables the user must provide (API keys, tokens)
  - input_schema: CLI arguments built from user input (domains, paths)
  - target_audience: who this connector is most useful for
  - official: True if published by the official vendor
"""

from typing import Dict, List, Optional


CONNECTORS_CATALOG: Dict[str, dict] = {

    # ═══════════════════════════════════════════════════════════════════════════
    # 📧  EMAIL & COMMUNICATION
    # ═══════════════════════════════════════════════════════════════════════════

    "google_workspace": {
        "name": "google_workspace",
        "display_name": "Google Workspace",
        "category": "Email & Communication",
        "description": "Read Gmail, create email drafts, and access Google Drive documents — all in one place.",
        "icon": "google",
        "auth_type": "oauth",
        "command": None,          # Handled natively via Aegis Google OAuth flow
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["hr", "marketing", "sales", "operations", "all"],
        "official": True,
    },

    "slack": {
        "name": "slack",
        "display_name": "Slack",
        "category": "Email & Communication",
        "description": "Read team channel messages, search conversations, and post updates to Slack workspaces.",
        "icon": "slack",
        "auth_type": "oauth",           # 1-click OAuth — no token pasting
        "oauth_service": "slack",       # matches key in oauth_service.OAUTH_CONFIGS
        "command": ["npx", "-y", "@modelcontextprotocol/server-slack"],
        "env_schema": [],               # tokens injected automatically after OAuth
        "input_schema": [],
        "target_audience": ["hr", "marketing", "sales", "operations", "all"],
        "official": True,
    },

    "notion": {
        "name": "notion",
        "display_name": "Notion",
        "category": "Email & Communication",
        "description": "Search pages, query team wikis, access meeting notes and project documentation.",
        "icon": "file-text",
        "auth_type": "oauth",
        "oauth_service": "notion",
        "command": ["npx", "-y", "@notionhq/mcp-server-notion"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["hr", "marketing", "operations", "all"],
        "official": True,
    },

    "figma": {
        "name": "figma",
        "display_name": "Figma",
        "category": "Design",
        "description": "Read designs, inspect component properties, and search your Figma workspace.",
        "icon": "figma",
        "auth_type": "api_key",
        "command": ["npx", "-y", "figma-developer-mcp", "--figma-api-key={FIGMA_ACCESS_TOKEN}"],
        "env_schema": [
            {
                "name": "FIGMA_ACCESS_TOKEN",
                "label": "Figma Personal Access Token",
                "type": "password",
                "placeholder": "figd_xxxxxxxxxxxxxxxxxxxxxxx",
                "help": "Get from figma.com → Settings → Security → Personal access tokens",
            }
        ],
        "input_schema": [],
        "target_audience": ["design", "marketing", "operations", "all"],
        "official": False,
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # 👥  CRM & SALES
    # ═══════════════════════════════════════════════════════════════════════════

    "hubspot": {
        "name": "hubspot",
        "display_name": "HubSpot CRM",
        "category": "CRM & Sales",
        "description": "Look up contacts, read deal pipeline, track lead activity, and get company data.",
        "icon": "users",
        "auth_type": "oauth",
        "oauth_service": "hubspot",
        "command": ["npx", "-y", "@hubspot/mcp-server"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["sales", "marketing", "hr"],
        "official": True,
    },

    "salesforce": {
        "name": "salesforce",
        "display_name": "Salesforce CRM",
        "category": "CRM & Sales",
        "description": "Query Salesforce objects, search contacts and accounts, track opportunity pipelines.",
        "icon": "briefcase",
        "auth_type": "oauth",
        "oauth_service": "salesforce",
        "command": ["uvx", "--from", "mcp-salesforce-connector", "mcp-salesforce"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["sales", "marketing"],
        "official": False,
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # 📊  PROJECT & TASK MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════════

    "airtable": {
        "name": "airtable",
        "display_name": "Airtable",
        "category": "Project & Task Management",
        "description": "Read and write Airtable bases, tables and records — great for campaign trackers, content calendars, and people databases.",
        "icon": "table",
        "auth_type": "oauth",
        "oauth_service": "airtable",
        "command": ["npx", "-y", "airtable-mcp-server"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["marketing", "hr", "operations"],
        "official": False,
    },

    "linear": {
        "name": "linear",
        "display_name": "Linear",
        "category": "Project & Task Management",
        "description": "View and create project issues, track sprint progress, and manage team roadmaps.",
        "icon": "check-square",
        "auth_type": "oauth",
        "oauth_service": "linear",
        "command": ["npx", "-y", "mcp-server-linear"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["marketing", "operations"],
        "official": False,
    },

    "jira": {
        "name": "jira",
        "display_name": "Jira",
        "category": "Project & Task Management",
        "description": "Search Jira tickets, track sprint boards, and view issue details.",
        "icon": "trello",
        "auth_type": "oauth",
        "oauth_service": "jira",
        "command": ["npx", "-y", "mcp-jira-server"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["marketing", "hr", "operations"],
        "official": False,
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # 💰  PAYMENTS & E-COMMERCE
    # ═══════════════════════════════════════════════════════════════════════════

    "stripe": {
        "name": "stripe",
        "display_name": "Stripe Payments",
        "category": "Payments & E-Commerce",
        "description": "View customers, payment history, subscriptions, invoices, and refund details.",
        "icon": "credit-card",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@stripe/mcp", "--tools=all"],
        "env_schema": [
            {
                "key": "STRIPE_SECRET_KEY",
                "label": "Stripe Secret API Key",
                "placeholder": "sk_live_xxxxxxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://dashboard.stripe.com/apikeys"
            }
        ],
        "input_schema": [],
        "target_audience": ["sales", "marketing", "operations"],
        "official": True,
    },

    "shopify": {
        "name": "shopify",
        "display_name": "Shopify Store",
        "category": "Payments & E-Commerce",
        "description": "Query products, orders, customers and inventory from your Shopify store.",
        "icon": "shopping-bag",
        "auth_type": "api_key",
        "command": ["npx", "-y", "shopify-mcp"],
        "env_schema": [
            {
                "key": "SHOPIFY_ACCESS_TOKEN",
                "label": "Shopify Admin API Access Token",
                "placeholder": "shpat_xxxxxxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://help.shopify.com/en/api/getting-started/authentication/private-authentication"
            }
        ],
        "input_schema": [
            {
                "key": "shop_domain",
                "label": "Your Shopify Store Domain",
                "placeholder": "mystore.myshopify.com",
                "required": True,
                "secret": False,
                "command_arg_template": "--domain={value}"
            }
        ],
        "target_audience": ["sales", "marketing"],
        "official": False,
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # 📣  MARKETING & SOCIAL
    # ═══════════════════════════════════════════════════════════════════════════

    "brave_search": {
        "name": "brave_search",
        "display_name": "Brave Web Search",
        "category": "Marketing & Social",
        "description": "Search the internet for competitor research, industry news, and market trends.",
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
        "target_audience": ["marketing", "hr", "all"],
        "official": True,
    },

    "fetch": {
        "name": "fetch",
        "display_name": "Web Page Reader",
        "category": "Marketing & Social",
        "description": "Read any public web page and extract its content — useful for reading articles, competitor sites, and job posts.",
        "icon": "globe",
        "auth_type": "none",
        "command": ["uvx", "mcp-server-fetch"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["marketing", "hr", "all"],
        "official": True,
    },

    "google_maps": {
        "name": "google_maps",
        "display_name": "Google Maps & Places",
        "category": "Marketing & Social",
        "description": "Find business addresses, lookup places, calculate routes and get location details.",
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
        "target_audience": ["marketing", "hr", "operations"],
        "official": True,
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # 🎯  CUSTOMER SUPPORT
    # ═══════════════════════════════════════════════════════════════════════════

    "zendesk": {
        "name": "zendesk",
        "display_name": "Zendesk Support",
        "category": "Customer Support",
        "description": "Search customer support tickets, check ticket status, and read issue threads.",
        "icon": "headphones",
        "auth_type": "api_key",
        "command": ["npx", "-y", "mcp-server-zendesk"],
        "env_schema": [
            {
                "key": "ZENDESK_API_TOKEN",
                "label": "Zendesk API Token",
                "placeholder": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "required": True,
                "secret": True,
                "help_url": "https://developer.zendesk.com/api-reference/introduction/security-and-auth/"
            },
            {
                "key": "ZENDESK_EMAIL",
                "label": "Your Zendesk Email",
                "placeholder": "agent@yourcompany.com",
                "required": True,
                "secret": False,
            },
            {
                "key": "ZENDESK_SUBDOMAIN",
                "label": "Zendesk Subdomain",
                "placeholder": "yourcompany",
                "required": True,
                "secret": False,
            }
        ],
        "input_schema": [],
        "target_audience": ["sales", "marketing", "operations"],
        "official": False,
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # 🤖  AI & PRODUCTIVITY TOOLS
    # ═══════════════════════════════════════════════════════════════════════════

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
    # 🧑‍💻  DEVELOPER TOOLS  (kept for technical team members)
    # ═══════════════════════════════════════════════════════════════════════════

    "github": {
        "name": "github",
        "display_name": "GitHub",
        "category": "Developer Tools",
        "description": "Search code repositories, manage issues, inspect pull requests and commit history.",
        "icon": "github",
        "auth_type": "oauth",
        "oauth_service": "github",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "env_schema": [],
        "input_schema": [],
        "target_audience": ["developer"],
        "official": True,
    },

    "postgres": {
        "name": "postgres",
        "display_name": "PostgreSQL Database",
        "category": "Developer Tools",
        "description": "Connect to a PostgreSQL database and run read-only SQL queries.",
        "icon": "database",
        "auth_type": "connection_string",
        "command": ["npx", "-y", "@modelcontextprotocol/server-postgres"],
        "env_schema": [],
        "input_schema": [
            {
                "key": "db_url",
                "label": "Database Connection URL",
                "placeholder": "postgresql://user:password@localhost:5432/dbname",
                "required": True,
                "secret": True,
                "command_arg_template": "{value}"
            }
        ],
        "target_audience": ["developer"],
        "official": True,
    },

    "sentry": {
        "name": "sentry",
        "display_name": "Sentry Error Monitoring",
        "category": "Developer Tools",
        "description": "Inspect production crash reports, error tracebacks, and performance issues.",
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
        "target_audience": ["developer"],
        "official": True,
    },

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

    "elasticsearch": {
        "name": "elasticsearch",
        "display_name": "Elasticsearch",
        "category": "Developer Tools",
        "description": "Search indices, run query DSL, and analyze document clusters.",
        "icon": "search",
        "auth_type": "api_key",
        "command": ["npx", "-y", "@elastic/mcp-server-elasticsearch"],
        "env_schema": [
            {
                "key": "ES_URL",
                "label": "Elasticsearch Cluster URL",
                "placeholder": "https://my-cluster.es.amazonaws.com:9243",
                "required": True,
                "secret": False,
            },
            {
                "key": "ES_API_KEY",
                "label": "Elasticsearch API Key",
                "placeholder": "V1V4...key...",
                "required": True,
                "secret": True,
            }
        ],
        "input_schema": [],
        "target_audience": ["developer"],
        "official": True,
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def resolve_connector_command(server_name: str, input_params: dict) -> List[str]:
    """
    Constructs the full command array for a catalog connector,
    filling in template args from user-provided input_params.
    """
    cat = CONNECTORS_CATALOG.get(server_name)
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
