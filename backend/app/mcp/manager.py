import logging
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)


class GoogleAPIManager:
    """
    Manages direct Google API connections via google-api-python-client.
    Replaces the broken NPM-based MCP server approach.
    Exposes available 'tools' in the same format as an MCP server would.
    """

    AVAILABLE_TOOLS = [
        {
            "name": "drive_list_files",
            "description": "List files in the user's Google Drive.",
            "parameters": {
                "query": "optional search query (e.g. 'name contains invoice')",
                "max_results": "max number of results to return (default 10)"
            }
        },
        {
            "name": "drive_read_file",
            "description": "Read the content of a Google Doc or text file from Google Drive.",
            "parameters": {
                "file_id": "the Google Drive file ID to read"
            }
        },
        {
            "name": "gmail_list_messages",
            "description": "List recent email messages from the user's Gmail inbox.",
            "parameters": {
                "max_results": "max number of emails to return (default 5)",
                "query": "optional Gmail search query (e.g. 'from:boss@company.com')"
            }
        },
        {
            "name": "gmail_read_message",
            "description": "Read the full content of a specific Gmail message.",
            "parameters": {
                "message_id": "the Gmail message ID to read"
            }
        }
    ]

    def __init__(self):
        self.credentials: Credentials | None = None
        self._drive_service = None
        self._gmail_service = None

    def initialize(self, credentials: Credentials):
        """Store credentials and pre-build the API service clients."""
        self.credentials = credentials
        self._drive_service = build("drive", "v3", credentials=credentials)
        self._gmail_service = build("gmail", "v1", credentials=credentials)
        logger.info("====== AVAILABLE GOOGLE API TOOLS ======")
        for tool in self.AVAILABLE_TOOLS:
            logger.info(f"Tool: {tool['name']} — {tool['description']}")
        logger.info("=" * 40)
        logger.info("Google API Manager initialized successfully.")

    def list_tools(self) -> list:
        """Return the list of available tools (MCP-compatible format)."""
        return self.AVAILABLE_TOOLS

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Dispatch a tool call to the relevant Google API method."""
        if not self.credentials:
            raise RuntimeError("Not authenticated. Please login with Google first.")

        if tool_name == "drive_list_files":
            return self._drive_list_files(
                query=arguments.get("query", ""),
                max_results=int(arguments.get("max_results", 10))
            )
        elif tool_name == "drive_read_file":
            return self._drive_read_file(file_id=arguments.get("file_id", ""))
        elif tool_name == "gmail_list_messages":
            return self._gmail_list_messages(
                max_results=int(arguments.get("max_results", 5)),
                query=arguments.get("query", "")
            )
        elif tool_name == "gmail_read_message":
            return self._gmail_read_message(message_id=arguments.get("message_id", ""))
        else:
            return f"Unknown tool: {tool_name}"

    # ─── Drive Tools ────────────────────────────────────────────────────────

    def _drive_list_files(self, query: str = "", max_results: int = 10) -> str:
        params = {
            "pageSize": max_results,
            "fields": "files(id, name, mimeType, modifiedTime)"
        }
        if query:
            params["q"] = query

        result = self._drive_service.files().list(**params).execute()
        files = result.get("files", [])
        if not files:
            return "No files found."

        output = f"Found {len(files)} files:\n"
        for f in files:
            output += f"  - {f['name']} (ID: {f['id']}, Type: {f['mimeType']}, Modified: {f['modifiedTime']})\n"
        return output

    def _drive_read_file(self, file_id: str = "") -> str:
        # Auto-resolve if empty or placeholder
        if not file_id or file_id.startswith("{") or file_id.lower() in ["latest", "last", "recent", "first"]:
            list_res = self._drive_service.files().list(pageSize=1, fields="files(id, name)").execute()
            files = list_res.get("files", [])
            if not files:
                return "No files found in Google Drive."
            file_id = files[0]["id"]

        # Get file metadata to check type
        meta = self._drive_service.files().get(fileId=file_id, fields="name,mimeType").execute()
        mime_type = meta.get("mimeType", "")
        name = meta.get("name", file_id)

        if mime_type == "application/vnd.google-apps.document":
            # Export Google Doc as plain text
            content = self._drive_service.files().export(
                fileId=file_id, mimeType="text/plain"
            ).execute()
            return f"Content of '{name}':\n{content.decode('utf-8')}"
        else:
            return f"File '{name}' (type: {mime_type}) is not a text-exportable Google Doc."

    # ─── Gmail Tools ─────────────────────────────────────────────────────────

    def _gmail_list_messages(self, max_results: int = 5, query: str = "") -> str:
        params = {"userId": "me", "maxResults": max_results}
        if query:
            params["q"] = query

        result = self._gmail_service.users().messages().list(**params).execute()
        messages = result.get("messages", [])
        if not messages:
            return "No messages found."

        output = f"Found {len(messages)} messages:\n"
        for msg in messages:
            # Fetch subject line for each
            detail = self._gmail_service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["Subject", "From"]
            ).execute()
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            output += f"  - ID: {msg['id']} | From: {headers.get('From', 'unknown')} | Subject: {headers.get('Subject', '(no subject)')}\n"
        return output

    def _gmail_read_message(self, message_id: str = "") -> str:
        # Auto-resolve if empty or placeholder
        if not message_id or message_id.startswith("{") or message_id.lower() in ["latest", "last", "recent", "first"]:
            list_res = self._gmail_service.users().messages().list(userId="me", maxResults=1).execute()
            msgs = list_res.get("messages", [])
            if not msgs:
                return "No email messages found in inbox."
            message_id = msgs[0]["id"]

        detail = self._gmail_service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        snippet = detail.get("snippet", "")
        return (
            f"From: {headers.get('From', 'unknown')}\n"
            f"Subject: {headers.get('Subject', '(no subject)')}\n"
            f"Date: {headers.get('Date', 'unknown')}\n\n"
            f"Snippet: {snippet}"
        )


# Global instance
mcp_manager = GoogleAPIManager()
