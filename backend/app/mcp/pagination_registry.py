"""
Aegis — Pagination Registry

Provides deterministic cursor extraction for known MCP tool pagination patterns.
Avoids burning an Executor LLM call per page for the common (REST/GitHub/Gmail) case.

Extraction priority:
  1. Per-tool registry (exact match or prefix wildcard)
  2. Generic JSON field scan (common cursor field names)
  3. LLM fallback (caller's responsibility — not handled here)

Usage:
    from app.mcp.pagination_registry import get_next_cursor, MUTATING_TOOLS

    cursor_info = get_next_cursor(tool_name, result_json_dict)
    # Returns: {"cursor_value": "abc123", "inject_arg": "page_token"} or None if exhausted
"""

import fnmatch
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-tool pagination patterns
# Key: exact tool name OR glob pattern (e.g. "gmail_*")
# Value: dict with:
#   cursor_field  — the key in the result JSON that holds the next cursor/token
#   inject_arg    — the argument name to pass to the next call
# ---------------------------------------------------------------------------
PAGINATION_PATTERNS: Dict[str, Dict[str, str]] = {
    # GitHub
    "list_commits":         {"cursor_field": "next_page",      "inject_arg": "page"},
    "list_issues":          {"cursor_field": "next_page",      "inject_arg": "page"},
    "list_pull_requests":   {"cursor_field": "next_page",      "inject_arg": "page"},
    "search_repositories":  {"cursor_field": "next_page",      "inject_arg": "page"},
    # Gmail
    "gmail_*":              {"cursor_field": "nextPageToken",   "inject_arg": "pageToken"},
    # Google Drive
    "drive_*":              {"cursor_field": "nextPageToken",   "inject_arg": "pageToken"},
    # Slack
    "slack_*":              {"cursor_field": "next_cursor",     "inject_arg": "cursor"},
    # Notion
    "notion_*":             {"cursor_field": "next_cursor",     "inject_arg": "start_cursor"},
    # Linear
    "linear_*":             {"cursor_field": "endCursor",       "inject_arg": "after"},
    # Jira
    "jira_*":               {"cursor_field": "startAt",         "inject_arg": "startAt"},
}

# Generic field names to scan if no per-tool pattern is found.
# Checked in order; first non-null value wins.
GENERIC_CURSOR_FIELDS = [
    "nextCursor",
    "next_cursor",
    "nextPageToken",
    "next_page_token",
    "cursor",
    "next_page",
    "after",
    "endCursor",
]

# ---------------------------------------------------------------------------
# Tools classified as mutating (write/delete).
# The pagination auto-loop MUST NOT proceed for these tools.
# Default is mutating (fail-closed) — only listed read-only tools are allowed
# to auto-loop.  This registry is checked in addition to any is_mutating field
# on the MCP tool schema itself.
# ---------------------------------------------------------------------------
READ_ONLY_TOOLS = {
    "list_commits",
    "list_issues",
    "list_pull_requests",
    "search_repositories",
    "list_branches",
    "list_releases",
    "get_commit",
    "gmail_list_messages",
    "gmail_get_message",
    "gmail_list_drafts",
    "drive_list_files",
    "drive_read_file",
    "slack_list_channels",
    "slack_list_messages",
    "notion_list_pages",
    "notion_get_page",
    "linear_list_issues",
    "jira_list_issues",
    "jira_search_issues",
    # Local tool (not an MCP server) — always reads a page and never
    # persists or mutates anything (see ChatAgent._execute_web_scrape).
    # Doesn't match the get_/list_/search_ verb-prefix heuristic in
    # is_write_tool below, so it needs to be listed explicitly.
    "web_scrape",
}


def is_tool_safe_to_autoloop(tool_name: str, tool_schema: Optional[Dict] = None) -> bool:
    """
    Returns True only if a tool is safe to run in an unattended auto-loop.

    Priority:
    1. Explicit is_mutating field on the tool schema (if present) — overrides registry.
    2. READ_ONLY_TOOLS registry — known-safe list.
    3. Default: False (fail-closed for unknown tools).
    """
    # 1. Explicit schema field takes priority
    if tool_schema and "is_mutating" in tool_schema:
        return not bool(tool_schema["is_mutating"])

    # 2. Registry check
    if tool_name in READ_ONLY_TOOLS:
        return True

    # 3. Fail-closed default
    logger.warning(
        f"[PaginationRegistry] Tool '{tool_name}' not in READ_ONLY_TOOLS and has no "
        "is_mutating field — defaulting to mutating (auto-loop BLOCKED)."
    )
    return False


# Common write-verb prefixes across the tool ecosystems this app connects to
# (GitHub, Gmail, Drive, Slack, Notion, Jira, Linear, ...). Used only for the
# plan-confirmation card's read/write badge — a different, lower-stakes
# purpose than is_tool_safe_to_autoloop above, which deliberately fail-closes
# to "mutating" for anything not in the curated READ_ONLY_TOOLS allowlist.
# Reusing that fail-closed check here would badge ordinary read tools like
# 'get_repository' or 'search_code' as "writes" just because they're not on
# that narrow list, making the badge noisy enough that users learn to ignore
# it — so this checks verb shape first, and only stays cautious for names
# that don't look like either.
_WRITE_VERB_PREFIXES = (
    "create_", "update_", "delete_", "remove_", "send_", "push_", "merge_",
    "add_", "upload_", "set_", "write_", "close_", "reopen_", "assign_",
    "invite_", "archive_", "disable_", "enable_", "revoke_", "approve_",
    "reject_", "cancel_", "schedule_", "execute_", "run_", "fork_", "star_",
    "unstar_", "watch_", "unwatch_", "connect_", "disconnect_", "post_",
    "publish_", "share_", "grant_", "block_", "unblock_", "move_", "copy_",
    "rename_", "trash_", "restore_", "invoke_",
)
_READ_VERB_PREFIXES = (
    "list_", "get_", "search_", "find_", "fetch_", "read_", "check_",
    "count_", "describe_", "show_", "view_", "download_", "query_",
)


def is_write_tool(tool_name: str, tool_schema: Optional[Dict] = None) -> bool:
    """
    Best-effort read/write classification for surfacing to the user — e.g.
    "this step only reads data" vs "this step changes something" on the plan
    confirmation card. NOT the same bar as is_tool_safe_to_autoloop (that one
    guards unattended auto-pagination and rightly fail-closes much harder).

    Priority:
    1. Explicit is_mutating field on the tool schema, if present — authoritative.
    2. READ_ONLY_TOOLS registry — known-safe list.
    3. Verb-prefix match against the tool's own name (covers the vast
       majority of real tool names in this app without needing every one
       individually registered).
    4. Unrecognized shape — stays cautious and reports "write".
    """
    if tool_schema and "is_mutating" in tool_schema:
        return bool(tool_schema["is_mutating"])
    if tool_name in READ_ONLY_TOOLS:
        return False
    name_lower = (tool_name or "").lower()
    if any(name_lower.startswith(p) for p in _WRITE_VERB_PREFIXES):
        return True
    if any(name_lower.startswith(p) for p in _READ_VERB_PREFIXES):
        return False
    return True


def _match_pattern(tool_name: str) -> Optional[Dict[str, str]]:
    """Look up the tool in PAGINATION_PATTERNS using exact match then glob."""
    # Exact match first
    if tool_name in PAGINATION_PATTERNS:
        return PAGINATION_PATTERNS[tool_name]
    # Glob wildcard match
    for pattern, info in PAGINATION_PATTERNS.items():
        if "*" in pattern and fnmatch.fnmatch(tool_name, pattern):
            return info
    return None


def get_next_cursor(
    tool_name: str,
    result: Any,
) -> Optional[Dict[str, str]]:
    """
    Attempt deterministic extraction of the next pagination cursor from a tool result.

    Returns a dict {"cursor_value": <str>, "inject_arg": <str>} if a next page exists,
    or None if pagination is exhausted / not applicable.

    Args:
        tool_name:  The MCP tool name.
        result:     The raw tool result (dict, list, or string).

    The caller is responsible for the LLM-fallback path when this returns None
    but the result shape is ambiguous (rare for custom MCPs).
    """
    if not isinstance(result, dict):
        return None

    # --- Step 1: Per-tool pattern ---
    pattern_info = _match_pattern(tool_name)
    if pattern_info:
        cursor_field = pattern_info["cursor_field"]
        inject_arg = pattern_info["inject_arg"]
        # Handle nested fields (e.g. "pagination.nextCursor")
        value = _deep_get(result, cursor_field)
        if value and str(value).strip():
            return {"cursor_value": str(value), "inject_arg": inject_arg}
        # Cursor field present but null/empty → pagination exhausted
        if cursor_field in result or _deep_key_exists(result, cursor_field):
            return None

    # --- Step 2: Generic field scan ---
    for field in GENERIC_CURSOR_FIELDS:
        value = _deep_get(result, field)
        if value and str(value).strip():
            # Best-effort: use the field name as the inject_arg too
            inject_arg = pattern_info["inject_arg"] if pattern_info else field
            return {"cursor_value": str(value), "inject_arg": inject_arg}

    return None


def _deep_get(data: Dict, key: str, default=None):
    """Recursively search a dict for a key, returning the first match."""
    if not isinstance(data, dict):
        return default
    if key in data:
        return data[key]
    for v in data.values():
        if isinstance(v, dict):
            result = _deep_get(v, key)
            if result is not None:
                return result
    return default


def _deep_key_exists(data: Dict, key: str) -> bool:
    """Returns True if `key` exists anywhere in the nested dict (even if null)."""
    if not isinstance(data, dict):
        return False
    if key in data:
        return True
    return any(_deep_key_exists(v, key) for v in data.values() if isinstance(v, dict))
