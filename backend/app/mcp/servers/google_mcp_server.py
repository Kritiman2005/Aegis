#!/usr/bin/env python3
"""
Aegis — Google Workspace MCP Server

Standalone stdio MCP server exposing Gmail and Google Drive tools.
Runs as a subprocess spawned by the Aegis registry.

Communication: JSON-RPC 2.0 over stdin/stdout (newline-delimited).
ALL logging goes to stderr — stdout is reserved for the MCP protocol.

Environment:
    GOOGLE_CREDENTIALS_JSON   Serialized Google OAuth credentials (JSON string)
"""

import sys
import json
import os
import logging
import base64
import argparse
from email.mime.text import MIMEText

# ── All output except MCP messages goes to stderr ────────────────────────────
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[google-mcp] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ── Tool definitions (MCP inputSchema format) ────────────────────────────────

TOOLS = [
    {
        "name": "drive_list_files",
        "description": "List files in the user's Google Drive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional Google Drive search query (e.g. 'name contains \"invoice\"'). CRITICAL: NEVER put 'orderBy' or 'limit' in this string. The API will crash. If you want to sort or limit, use the separate 'order_by' and 'max_results' arguments instead. Do NOT use relative dates like 'now-1d'; date queries must use exact RFC 3339 timestamps."
                },
                "order_by": {
                    "type": "string",
                    "description": "Optional sorting order. Valid values: 'modifiedTime desc', 'modifiedTime', 'name'"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 10)"
                }
            }
        }
    },
    {
        "name": "drive_read_file",
        "description": "Read the content of a Google Doc or text file from Google Drive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The Google Drive file ID to read"
                }
            },
            "required": ["file_id"]
        }
    },
    {
        "name": "gmail_list_messages",
        "description": "List recent email messages from the user's Gmail inbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of emails to return (default 5)"
                },
                "query": {
                    "type": "string",
                    "description": "Optional Gmail search query (e.g. 'from:boss@company.com')"
                }
            }
        }
    },
    {
        "name": "gmail_read_message",
        "description": "Read the full content of a specific Gmail message.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The Gmail message ID to read"
                }
            },
            "required": ["message_id"]
        }
    },
    {
        "name": "gmail_create_draft",
        "description": "Create a new email draft in the user's Gmail account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address"
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line"
                },
                "body": {
                    "type": "string",
                    "description": "Email body content"
                }
            }
        }
    },
    {
        "name": "sheets_read_range",
        "description": "Read values from a specific Google Sheets range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "The ID of the spreadsheet"},
                "range": {"type": "string", "description": "The A1 notation of the range to read (e.g. 'Sheet1!A1:D10')"}
            },
            "required": ["spreadsheet_id", "range"]
        }
    },
    {
        "name": "sheets_update_range",
        "description": "Update values in a specific Google Sheets range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "The ID of the spreadsheet"},
                "range": {"type": "string", "description": "The A1 notation of the range to update"},
                "values": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "2D array of values to write. E.g. [['A1', 'B1'], ['A2', 'B2']]"
                }
            },
            "required": ["spreadsheet_id", "range", "values"]
        }
    },
    {
        "name": "docs_read_document",
        "description": "Read the text content of a Google Doc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "The ID of the Google Document"}
            },
            "required": ["document_id"]
        }
    }
]


# ── Google API helpers ────────────────────────────────────────────────────────

def _build_services(creds_json_str: str):
    """Build and return (drive, gmail, sheets, docs) from credentials JSON."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds_data = json.loads(creds_json_str)
        credentials = Credentials.from_authorized_user_info(creds_data)

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            logger.info("Auto-refreshed Google access token.")

        drive_service = build("drive", "v3", credentials=credentials)
        gmail_service = build("gmail", "v1", credentials=credentials)
        sheets_service = build("sheets", "v4", credentials=credentials)
        docs_service = build("docs", "v1", credentials=credentials)
        logger.info("Google API services initialized successfully.")
        return drive_service, gmail_service, sheets_service, docs_service
    except Exception as e:
        logger.error(f"Failed to initialize Google services: {e}")
        return None, None, None, None


def _drive_list_files(drive_service, query: str = "", order_by: str = "", max_results: int = 10) -> str:
    params = {"pageSize": max_results, "fields": "files(id, name, mimeType, modifiedTime)"}
    if query:
        # LLMs often hallucinate SQL-like clauses into the 'q' parameter despite schema warnings.
        # Google's API strictly rejects them, so we strip them out defensively.
        import re
        sanitized_q = re.sub(r"(?i)\b(order\s*by|limit)\s*(=|:)?\s*['\"]?[^'\"]*['\"]?", "", query).strip()
        if sanitized_q:
            params["q"] = sanitized_q
    if order_by:
        params["orderBy"] = order_by
    result = drive_service.files().list(**params).execute()
    files = result.get("files", [])
    if not files:
        return "No files found."
    output = f"Found {len(files)} files:\n"
    for f in files:
        output += f"  - {f['name']} (ID: {f['id']}, Type: {f['mimeType']}, Modified: {f['modifiedTime']})\n"
    return output


def _drive_read_file(drive_service, file_id: str) -> str:
    if not file_id or file_id.startswith("{") or file_id.lower() in ["latest", "last", "recent", "first"]:
        list_res = drive_service.files().list(pageSize=1, fields="files(id, name)").execute()
        files = list_res.get("files", [])
        if not files:
            return "No files found in Google Drive."
        file_id = files[0]["id"]

    meta = drive_service.files().get(fileId=file_id, fields="name,mimeType").execute()
    mime_type = meta.get("mimeType", "")
    name = meta.get("name", file_id)

    if mime_type == "application/vnd.google-apps.document":
        content = drive_service.files().export(fileId=file_id, mimeType="text/plain").execute()
        return f"Content of '{name}':\n{content.decode('utf-8')}"
    else:
        return f"File '{name}' (type: {mime_type}) is not a text-exportable Google Doc."


def _gmail_list_messages(gmail_service, max_results: int = 5, query: str = "") -> str:
    params = {"userId": "me", "maxResults": max_results}
    if query:
        params["q"] = query
    result = gmail_service.users().messages().list(**params).execute()
    messages = result.get("messages", [])
    if not messages:
        return "No messages found."
    output = f"Found {len(messages)} messages:\n"
    for msg in messages:
        detail = gmail_service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["Subject", "From"]
        ).execute()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        output += f"  - ID: {msg['id']} | From: {headers.get('From', 'unknown')} | Subject: {headers.get('Subject', '(no subject)')}\n"
    return output


def _gmail_read_message(gmail_service, message_id: str) -> str:
    if not message_id or message_id.startswith("{") or message_id.lower() in ["latest", "last", "recent", "first"]:
        list_res = gmail_service.users().messages().list(userId="me", maxResults=1).execute()
        msgs = list_res.get("messages", [])
        if not msgs:
            return "No email messages found in inbox."
        message_id = msgs[0]["id"]

    detail = gmail_service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
    snippet = detail.get("snippet", "")
    return (
        f"From: {headers.get('From', 'unknown')}\n"
        f"Subject: {headers.get('Subject', '(no subject)')}\n"
        f"Date: {headers.get('Date', 'unknown')}\n\n"
        f"Snippet: {snippet}"
    )


def _gmail_create_draft(gmail_service, to: str = "", subject: str = "", body: str = "") -> str:
    message = MIMEText(body)
    if to:
        message["to"] = to
    if subject:
        message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = gmail_service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    return f"Email draft created successfully! (Draft ID: {draft.get('id')})"


# ── MCP Server ────────────────────────────────────────────────────────────────


def _sheets_read_range(sheets_service, spreadsheet_id: str, range_name: str) -> str:
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=range_name).execute()
    values = result.get('values', [])
    if not values:
        return "No data found."
    return json.dumps(values, ensure_ascii=False)

def _sheets_update_range(sheets_service, spreadsheet_id: str, range_name: str, values: list) -> str:
    body = {
        'values': values
    }
    result = sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=range_name,
        valueInputOption="RAW", body=body).execute()
    return f"{result.get('updatedCells')} cells updated."

def _docs_read_document(docs_service, document_id: str) -> str:
    document = docs_service.documents().get(documentId=document_id).execute()
    text = ""
    for element in document.get('body').get('content'):
        if 'paragraph' in element:
            elements = element.get('paragraph').get('elements')
            for elem in elements:
                if 'textRun' in elem:
                    text += elem.get('textRun').get('content')
    return text.strip()

class GoogleMCPServer:
    """
    Minimal stdio MCP server implementing the 2024-11-05 protocol version.
    Reads JSON-RPC messages from stdin, writes responses to stdout.
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self):
        self._drive_service = None
        self._gmail_service = None
        self._sheets_service = None
        self._docs_service = None
        self._initialized = False

        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            self._drive_service, self._gmail_service, self._sheets_service, self._docs_service = _build_services(creds_json)
        else:
            logger.warning("GOOGLE_CREDENTIALS_JSON not set — tool calls will fail.")

    # ── I/O helpers ─────────────────────────────────────────────────────────

    def _write(self, obj: dict):
        """Write a JSON-RPC message to stdout."""
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _ok(self, msg_id, result):
        self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _err(self, msg_id, code: int, message: str):
        self._write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})

    # ── Handlers ────────────────────────────────────────────────────────────

    def _handle_initialize(self, msg_id, params: dict):
        self._initialized = True
        self._ok(msg_id, {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {}
            },
            "serverInfo": {"name": "aegis-google-workspace", "version": "1.0.0"}
        })

    def _handle_tools_list(self, msg_id):
        self._ok(msg_id, {"tools": TOOLS})

    def _handle_tools_call(self, msg_id, params: dict):
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if not self._drive_service or not self._gmail_service or not self._sheets_service or not self._docs_service:
            self._err(msg_id, -32000, "Google services not initialized. Check GOOGLE_CREDENTIALS_JSON.")
            return

        try:
            if tool_name == "drive_list_files":
                text = _drive_list_files(
                    self._drive_service,
                    query=arguments.get("query", ""),
                    order_by=arguments.get("order_by", ""),
                    max_results=int(arguments.get("max_results", 10))
                )
            elif tool_name == "drive_read_file":
                text = _drive_read_file(self._drive_service, file_id=arguments.get("file_id", ""))
            elif tool_name == "gmail_list_messages":
                text = _gmail_list_messages(
                    self._gmail_service,
                    max_results=int(arguments.get("max_results", 5)),
                    query=arguments.get("query", "")
                )
            elif tool_name == "gmail_read_message":
                text = _gmail_read_message(self._gmail_service, message_id=arguments.get("message_id", ""))
            elif tool_name == "gmail_create_draft":
                text = _gmail_create_draft(
                    self._gmail_service,
                    to=arguments.get("to", ""),
                    subject=arguments.get("subject", ""),
                    body=arguments.get("body", "")
                )
            elif tool_name == "sheets_read_range":
                text = _sheets_read_range(self._sheets_service, arguments.get("spreadsheet_id", ""), arguments.get("range", ""))
            elif tool_name == "sheets_update_range":
                text = _sheets_update_range(self._sheets_service, arguments.get("spreadsheet_id", ""), arguments.get("range", ""), arguments.get("values", []))
            elif tool_name == "docs_read_document":
                text = _docs_read_document(self._docs_service, arguments.get("document_id", ""))
            else:
                self._err(msg_id, -32601, f"Unknown tool: {tool_name}")
                return

            self._ok(msg_id, {"content": [{"type": "text", "text": text}]})

        except Exception as e:
            logger.error(f"Tool call '{tool_name}' failed: {e}")
            self._err(msg_id, -32000, str(e))

    def _handle_resources_list(self, msg_id):
        self._ok(msg_id, {"resources": []})

    def _handle_prompts_list(self, msg_id):
        self._ok(msg_id, {"prompts": []})

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        logger.info("Google Workspace MCP Server started. Waiting for messages...")
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON: {e}")
                continue

            msg_id = msg.get("id")       # None for notifications
            method = msg.get("method", "")
            params = msg.get("params", {})

            logger.info(f"← {method} (id={msg_id})")

            # Notifications have no id and need no response
            if method == "notifications/initialized":
                logger.info("Client sent initialized notification.")
                continue
            if method.startswith("notifications/"):
                continue

            # Requests
            if method == "initialize":
                self._handle_initialize(msg_id, params)
            elif method == "tools/list":
                self._handle_tools_list(msg_id)
            elif method == "tools/call":
                self._handle_tools_call(msg_id, params)
            elif method == "resources/list":
                self._handle_resources_list(msg_id)
            elif method == "prompts/list":
                self._handle_prompts_list(msg_id)
            else:
                if msg_id is not None:
                    self._err(msg_id, -32601, f"Method not found: {method}")


def run_server(args_list=None):
    global TOOLS
    parser = argparse.ArgumentParser(description="Google Workspace MCP Server")
    parser.add_argument("--service", type=str, default="all", help="Which service tools to expose")
    args = parser.parse_args(args_list)

    # Filter tools based on the requested service
    if args.service == "google_mail":
        TOOLS = [t for t in TOOLS if t["name"].startswith("gmail_")]
    elif args.service == "google_drive":
        TOOLS = [t for t in TOOLS if t["name"].startswith("drive_")]
    elif args.service == "google_sheets":
        TOOLS = [t for t in TOOLS if t["name"].startswith("sheets_")]
    elif args.service == "google_docs":
        TOOLS = [t for t in TOOLS if t["name"].startswith("docs_")]

    server = GoogleMCPServer()
    server.run()

if __name__ == "__main__":
    run_server()
