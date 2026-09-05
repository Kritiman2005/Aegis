"""
response_shapers.py — Pre-built MCP response transformation layer.

For each supported MCP tool, two functions are registered:
  executor_fn(raw)  → minimal JSON dict passed to the Executor LLM
  display_fn(raw)   → human-readable markdown string shown to the user

Public API
----------
  shape_for_executor(tool_name, raw) -> dict
  shape_for_display(tool_name, raw)  -> str

Both functions fall back gracefully (truncated raw) if no shaper is registered
for the given tool, so future connectors work without touching this file first.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe(obj: Any, *keys, default=None) -> Any:
    """Safely traverse nested dicts/lists without raising."""
    for key in keys:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(key, default)
        elif isinstance(obj, list):
            try:
                obj = obj[int(key)]
            except (IndexError, ValueError, TypeError):
                return default
        else:
            return default
    return obj


def _trunc(s: Optional[Any], n: int = 150) -> str:
    if s is None:
        return ""
    s = str(s)
    return s[:n] + ("…" if len(s) > n else "")


def _date(s: Optional[str]) -> str:
    """Return just the date portion (YYYY-MM-DD) from an ISO timestamp."""
    if not s:
        return "—"
    return str(s)[:10]


def _num(n: Any) -> str:
    """Format a number with commas, e.g. 16300 → '16,300'."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n) if n is not None else "0"


# ═══════════════════════════════════════════════════════════════════════════════
# GitHub shapers
# ═══════════════════════════════════════════════════════════════════════════════

def _gh_search_repositories_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    items = (raw.get("items") or [])[:10]
    return {
        "total_count": raw.get("total_count"),
        "repositories": [
            {
                "id": r.get("id"),
                "full_name": r.get("full_name"),        # owner/repo used by list_commits etc.
                "owner": _safe(r, "owner", "login"),
                "name": r.get("name"),
                "description": _trunc(r.get("description"), 120),
                "html_url": r.get("html_url"),
                "stars": r.get("stargazers_count"),
                "forks": r.get("forks_count"),
                "default_branch": r.get("default_branch", "main"),
                "updated_at": _date(r.get("updated_at")),
                "private": r.get("private"),
            }
            for r in items
        ],
    }


def _gh_search_repositories_disp(raw: Any) -> str:
    shaped = _gh_search_repositories_exec(raw)
    if "error" in shaped:
        return f"{shaped['error']}"
    repos = shaped.get("repositories", [])
    total = shaped.get("total_count", len(repos))
    if not repos:
        return "No repositories found."
    lines = [f"**Found {_num(total)} repositories** (showing top {len(repos)}):\n"]
    for r in repos:
        visibility = "Private" if r.get("private") else "Public"
        desc = r.get("description") or "_No description_"
        lines.append(
            f"**[{r['full_name']}]({r['html_url']})** ({visibility}) — "
            f"{_num(r.get('stars', 0))} stars · {_num(r.get('forks', 0))} forks\n"
            f"> {desc}"
        )
    return "\n\n".join(lines)


def _gh_list_commits_exec(raw: Any) -> Dict:
    if not isinstance(raw, list):
        return {"error": _trunc(str(raw), 300)}
    commits = raw[:30]
    return {
        "total_shown": len(raw),
        "commits": [
            {
                "sha": (c.get("sha") or "")[:7],
                "message": _trunc(_safe(c, "commit", "message"), 100),
                "author": _safe(c, "commit", "author", "name"),
                "date": _date(_safe(c, "commit", "author", "date")),
            }
            for c in commits
        ],
    }


def _gh_list_commits_disp(raw: Any) -> str:
    shaped = _gh_list_commits_exec(raw)
    if "error" in shaped:
        return f"{shaped['error']}"
    commits = shaped.get("commits", [])
    total = shaped.get("total_shown", len(commits))
    if not commits:
        return "No commits found."
    lines = [f"**{_num(total)} commits shown**:\n"]
    for c in commits:
        lines.append(f"- `{c['sha']}` {c['message']} — _{c['author']}_ on {c['date']}")
    return "\n".join(lines)


def _gh_get_repository_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "id": raw.get("id"),
        "full_name": raw.get("full_name"),
        "owner": _safe(raw, "owner", "login"),
        "name": raw.get("name"),
        "description": _trunc(raw.get("description"), 200),
        "html_url": raw.get("html_url"),
        "clone_url": raw.get("clone_url"),
        "stars": raw.get("stargazers_count"),
        "forks": raw.get("forks_count"),
        "open_issues": raw.get("open_issues_count"),
        "default_branch": raw.get("default_branch"),
        "private": raw.get("private"),
        "created_at": _date(raw.get("created_at")),
        "updated_at": _date(raw.get("updated_at")),
    }


def _gh_get_repository_disp(raw: Any) -> str:
    s = _gh_get_repository_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    return (
        f"## [{s['full_name']}]({s['html_url']})\n"
        f"{s.get('description') or '_No description_'}\n\n"
        f"- **{_num(s.get('stars', 0))}** stars · "
        f"**{_num(s.get('forks', 0))}** forks · "
        f"**{_num(s.get('open_issues', 0))}** open issues\n"
        f"- Default branch: `{s.get('default_branch')}` · Private: `{s.get('private')}`\n"
        f"- Created: {s.get('created_at')} · Updated: {s.get('updated_at')}"
    )


def _gh_list_issues_exec(raw: Any) -> Dict:
    if not isinstance(raw, list):
        return {"error": _trunc(str(raw), 300)}
    issues = raw[:20]
    return {
        "total_shown": len(raw),
        "issues": [
            {
                "number": i.get("number"),
                "title": _trunc(i.get("title"), 100),
                "state": i.get("state"),
                "html_url": i.get("html_url"),
                "user": _safe(i, "user", "login"),
                "created_at": _date(i.get("created_at")),
                "labels": [lbl.get("name") for lbl in (i.get("labels") or [])],
            }
            for i in issues
        ],
    }


def _gh_list_issues_disp(raw: Any) -> str:
    s = _gh_list_issues_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    issues = s.get("issues", [])
    if not issues:
        return "No issues found."
    lines = [f"**{_num(s.get('total_shown', len(issues)))} issues shown**:\n"]
    for issue in issues:
        labels = ", ".join(f"`{l}`" for l in issue.get("labels", [])) or "—"
        lines.append(
            f"**#{issue['number']} {issue['title']}** (`{issue['state']}`) "
            f"by _{issue['user']}_ on {issue['created_at']}\n"
            f"  Labels: {labels} · [View]({issue['html_url']})"
        )
    return "\n\n".join(lines)


def _gh_create_repository_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "id": raw.get("id"),
        "full_name": raw.get("full_name"),
        "html_url": raw.get("html_url"),
        "clone_url": raw.get("clone_url"),
        "private": raw.get("private"),
    }


def _gh_create_repository_disp(raw: Any) -> str:
    s = _gh_create_repository_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    return (
        f"Repository **[{s['full_name']}]({s['html_url']})** created!\n\n"
        f"Clone: `git clone {s['clone_url']}`"
    )


def _gh_fork_repository_exec(raw: Any) -> Dict:
    return _gh_create_repository_exec(raw)


def _gh_fork_repository_disp(raw: Any) -> str:
    s = _gh_fork_repository_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    return f"Forked as **[{s['full_name']}]({s['html_url']})**"


def _gh_create_issue_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "number": raw.get("number"),
        "title": raw.get("title"),
        "state": raw.get("state"),
        "html_url": raw.get("html_url"),
        "created_at": _date(raw.get("created_at")),
    }


def _gh_create_issue_disp(raw: Any) -> str:
    s = _gh_create_issue_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    return f"Issue **#{s['number']}: {s['title']}** created! [View]({s['html_url']})"


def _gh_search_code_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    items = (raw.get("items") or [])[:10]
    return {
        "total_count": raw.get("total_count"),
        "results": [
            {
                "path": i.get("path"),
                "repository": _safe(i, "repository", "full_name"),
                "html_url": i.get("html_url"),
            }
            for i in items
        ],
    }


def _gh_search_code_disp(raw: Any) -> str:
    s = _gh_search_code_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    results = s.get("results", [])
    if not results:
        return "No code matches found."
    lines = [f"**{_num(s.get('total_count', len(results)))} code matches** (top {len(results)}):\n"]
    for r in results:
        lines.append(f"- `{r['path']}` in [{r['repository']}]({r['html_url']})")
    return "\n".join(lines)


def _gh_get_file_contents_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "name": raw.get("name"),
        "path": raw.get("path"),
        "sha": (raw.get("sha") or "")[:7],
        "size": raw.get("size"),
        "html_url": raw.get("html_url"),
        "content": _decode_gh_content(raw),
    }


def _decode_gh_content(raw: Dict) -> Optional[str]:
    import base64
    content_b64 = raw.get("content")
    if not content_b64:
        return None
    try:
        decoded = base64.b64decode(content_b64.replace("\n", "")).decode("utf-8", errors="ignore")
        return _trunc(decoded, 2000)
    except Exception:
        return None


def _gh_get_file_contents_disp(raw: Any) -> str:
    s = _gh_get_file_contents_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    content = s.get("content") or "_Binary or empty file_"
    return (
        f"**[{s['path']}]({s['html_url']})** ({s.get('size', '?')} bytes)\n\n"
        f"```\n{content}\n```"
    )


def _gh_push_files_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    commit = raw.get("commit") or {}
    return {
        "sha": (_safe(commit, "sha") or "")[:7],
        "message": _safe(commit, "message"),
        "html_url": _safe(commit, "html_url"),
    }


def _gh_push_files_disp(raw: Any) -> str:
    s = _gh_push_files_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    return f"Pushed! Commit `{s['sha']}`: _{s.get('message')}_  [View]({s.get('html_url')})"


def _gh_create_pull_request_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "number": raw.get("number"),
        "title": raw.get("title"),
        "state": raw.get("state"),
        "html_url": raw.get("html_url"),
        "head": _safe(raw, "head", "ref"),
        "base": _safe(raw, "base", "ref"),
    }


def _gh_create_pull_request_disp(raw: Any) -> str:
    s = _gh_create_pull_request_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    return (
        f"Pull Request **#{s['number']}: {s['title']}** opened!\n"
        f"`{s['head']}` → `{s['base']}` · [View PR]({s['html_url']})"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Gmail shapers
# ═══════════════════════════════════════════════════════════════════════════════

def _gmail_list_messages_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    messages = (raw.get("messages") or [])[:20]
    return {
        "result_size_estimate": raw.get("resultSizeEstimate"),
        "messages": [
            {"id": m.get("id"), "thread_id": m.get("threadId")}
            for m in messages
        ],
    }


def _gmail_list_messages_disp(raw: Any) -> str:
    s = _gmail_list_messages_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    msgs = s.get("messages", [])
    est = s.get("result_size_estimate")
    if not msgs:
        return "No messages found."
    ids_block = "\n".join(f"- `{m['id']}`" for m in msgs)
    return (
        f"**~{_num(est)} messages** (showing {len(msgs)} IDs):\n\n"
        f"{ids_block}\n\n"
        f"_Use `get_message` with an ID to read the full email._"
    )


def _extract_gmail_body(payload: Dict) -> Optional[str]:
    import base64

    def _decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    direct_data = _safe(payload, "body", "data")
    if direct_data:
        return _decode(direct_data)

    for part in (payload.get("parts") or []):
        if part.get("mimeType") == "text/plain":
            data = _safe(part, "body", "data")
            if data:
                return _decode(data)
        for subpart in (part.get("parts") or []):
            if subpart.get("mimeType") == "text/plain":
                data = _safe(subpart, "body", "data")
                if data:
                    return _decode(data)
    return None


def _gmail_get_message_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    headers: Dict[str, str] = {}
    for h in (_safe(raw, "payload", "headers") or []):
        name = (h.get("name") or "").lower()
        if name in ("from", "to", "cc", "bcc", "subject", "date"):
            headers[name] = h.get("value", "")
    body = _extract_gmail_body(raw.get("payload") or {})
    return {
        "id": raw.get("id"),
        "thread_id": raw.get("threadId"),
        "subject": headers.get("subject", "(no subject)"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "cc": headers.get("cc"),
        "date": headers.get("date"),
        "snippet": _trunc(raw.get("snippet"), 300),
        "body": _trunc(body, 1500) if body else None,
        "label_ids": raw.get("labelIds", []),
    }


def _gmail_get_message_disp(raw: Any) -> str:
    s = _gmail_get_message_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    body_section = s.get("body") or s.get("snippet") or "_No body_"
    cc_line = f"**CC:** {s['cc']}  \n" if s.get("cc") else ""
    return (
        f"### {s.get('subject')}\n\n"
        f"**From:** {s.get('from')}  \n"
        f"**To:** {s.get('to')}  \n"
        f"{cc_line}"
        f"**Date:** {s.get('date')}  \n\n"
        f"---\n\n{body_section}"
    )


def _gmail_send_message_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "id": raw.get("id"),
        "thread_id": raw.get("threadId"),
        "label_ids": raw.get("labelIds", []),
    }


def _gmail_send_message_disp(raw: Any) -> str:
    s = _gmail_send_message_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    return f"Email sent! Message ID: `{s.get('id')}`"


def _gmail_list_drafts_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    drafts = (raw.get("drafts") or [])[:10]
    return {
        "result_size_estimate": raw.get("resultSizeEstimate"),
        "drafts": [
            {"id": d.get("id"), "message_id": _safe(d, "message", "id")}
            for d in drafts
        ],
    }


def _gmail_list_drafts_disp(raw: Any) -> str:
    s = _gmail_list_drafts_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    drafts = s.get("drafts", [])
    if not drafts:
        return "No drafts found."
    lines = [f"**{len(drafts)} draft(s)**:\n"]
    for d in drafts:
        lines.append(f"- Draft `{d['id']}` → Message `{d['message_id']}`")
    return "\n".join(lines)


def _gmail_get_draft_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    msg = raw.get("message") or {}
    result = _gmail_get_message_exec(msg)
    result["draft_id"] = raw.get("id")
    return result


def _gmail_get_draft_disp(raw: Any) -> str:
    s = _gmail_get_draft_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    body_section = s.get("body") or s.get("snippet") or "_No body_"
    return (
        f"### Draft: {s.get('subject')}\n\n"
        f"**To:** {s.get('to')}  \n"
        f"**Date:** {s.get('date')}  \n\n"
        f"---\n\n{body_section}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Google Drive shapers
# ═══════════════════════════════════════════════════════════════════════════════

def _drive_list_files_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    files = (raw.get("files") or [])[:20]
    return {
        "count": len(files),
        "files": [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "mime_type": f.get("mimeType"),
                "modified": _date(f.get("modifiedTime")),
                "size": f.get("size"),
                "web_url": f.get("webViewLink"),
                "parents": (f.get("parents") or []),
            }
            for f in files
        ],
    }


def _drive_list_files_disp(raw: Any) -> str:
    s = _drive_list_files_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    files = s.get("files", [])
    if not files:
        return "No files found."
    lines = [f"**{len(files)} file(s)**:\n"]
    for f in files:
        is_folder = "folder" in (f.get("mime_type") or "")
        name_part = f"[{f['name']}]({f['web_url']})" if f.get("web_url") else f["name"]
        size_part = f" · {f['size']} bytes" if f.get("size") else ""
        type_part = " (folder)" if is_folder else ""
        lines.append(f"- {name_part}{type_part} · {f.get('modified', '—')}{size_part}")
    return "\n".join(lines)


def _drive_get_file_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "mime_type": raw.get("mimeType"),
        "size": raw.get("size"),
        "modified": _date(raw.get("modifiedTime")),
        "web_url": raw.get("webViewLink"),
        "parents": (raw.get("parents") or []),
        "owner": _safe(raw, "owners", 0, "emailAddress"),
    }


def _drive_get_file_disp(raw: Any) -> str:
    s = _drive_get_file_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    link = f"[{s['name']}]({s['web_url']})" if s.get("web_url") else s["name"]
    size = f"{_num(s.get('size'))} bytes" if s.get("size") else "—"
    return (
        f"**{link}**\n\n"
        f"- Type: `{s.get('mime_type')}`\n"
        f"- Size: {size}\n"
        f"- Modified: {s.get('modified')}\n"
        f"- Owner: {s.get('owner') or '—'}"
    )


def _drive_create_file_exec(raw: Any) -> Dict:
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "web_url": raw.get("webViewLink"),
        "mime_type": raw.get("mimeType"),
    }


def _drive_create_file_disp(raw: Any) -> str:
    s = _drive_create_file_exec(raw)
    if "error" in s:
        return f"{s['error']}"
    link = f"[{s['name']}]({s['web_url']})" if s.get("web_url") else s["name"]
    return f"**{link}** created! File ID: `{s['id']}`"


def _drive_read_file_exec(raw: Any) -> Dict:
    """google_drive_read_file returns text content directly."""
    if isinstance(raw, str):
        return {"content": _trunc(raw, 3000)}
    if isinstance(raw, dict):
        content = raw.get("content") or raw.get("text") or str(raw)
        return {"content": _trunc(content, 3000)}
    return {"content": _trunc(str(raw), 3000)}


def _drive_read_file_disp(raw: Any) -> str:
    s = _drive_read_file_exec(raw)
    content = s.get("content") or "_Empty file_"
    return f"**File contents:**\n\n```\n{content}\n```"



# ── Google Sheets & Docs ──────────────────────────────────────────────────

def _sheets_read_range_exec(raw: Any) -> Dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            pass
    if isinstance(raw, list):
        # raw is already the 2D array
        return {"rows": len(raw), "cols": len(raw[0]) if raw else 0, "preview": raw[:5]}
    return {"content": str(raw)}

def _sheets_read_range_disp(raw: Any) -> str:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return raw

    if not isinstance(raw, list) or not raw:
        return "*No data found or empty range*"

    # Try to make a markdown table
    try:
        md = "| " + " | ".join(str(c).replace('|', '\|') for c in raw[0]) + " |\n"
        md += "|" + "|".join(["---" for _ in raw[0]]) + "|\n"
        for row in raw[1:]:
            md += "| " + " | ".join(str(c).replace('|', '\|') for c in row) + " |\n"
        return md
    except Exception:
        return "```json\n" + json.dumps(raw, indent=2) + "\n```"

def _sheets_update_range_exec(raw: Any) -> Dict:
    return {"result": str(raw)}

def _sheets_update_range_disp(raw: Any) -> str:
    return f"**Spreadsheet updated:** {str(raw)}"

def _docs_read_document_exec(raw: Any) -> Dict:
    if isinstance(raw, str):
        return {"content": _trunc(raw, 3000)}
    return {"content": _trunc(str(raw), 3000)}

def _docs_read_document_disp(raw: Any) -> str:
    s = _docs_read_document_exec(raw)
    content = s.get("content") or "_Empty document_"
    return f"**Document contents:**\n\n```\n{content}\n```"

def _web_scrape_exec(raw: Any) -> Dict:
    """
    Passes text_preview through untouched — app/core/agents/chat.py's
    _execute_web_scrape already bounds it to exactly one _SCRAPE_CHUNK_CHARS
    chunk via `offset`, so re-truncating it here (this used to hard-clip to
    2000 chars regardless of that) would silently shrink what the caller
    deliberately sized. Also, critically, forwards `warnings` and `note` —
    the old version dropped both, which meant the Executor LLM never saw
    the "call again with offset=N to keep reading" continuation instruction
    that makes long-page pagination actually usable.
    """
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    if raw.get("error"):
        out = {"error": raw["error"]}
        if raw.get("note"):
            out["note"] = raw["note"]
        return out
    out = {
        "title": raw.get("title") or "",
        "text_preview": raw.get("text_preview", ""),
    }
    if raw.get("warnings"):
        out["warnings"] = raw["warnings"]
    if raw.get("note"):
        out["note"] = raw["note"]
    return out

def _web_scrape_disp(raw: Any) -> str:
    """Shows the full chunk that was actually fetched — see _web_scrape_exec
    for why re-truncating it here would misrepresent what's available."""
    if not isinstance(raw, dict):
        return _trunc(str(raw), 2000)
    if raw.get("error"):
        note = f"\n\n_{raw['note']}_" if raw.get("note") else ""
        return f"**Scrape failed:** {raw['error']}{note}"
    title = raw.get("title") or "Untitled page"
    preview = raw.get("text_preview", "")
    warnings = raw.get("warnings") or []
    warn_txt = "\n\n" + "\n".join(f"> {w}" for w in warnings) if warnings else ""
    note = f"\n\n_{raw['note']}_" if raw.get("note") else ""
    return f"### {title}\n\n{preview}{warn_txt}{note}"

# ═══════════════════════════════════════════════════════════════════════════════
# Local filesystem tools (search_local_files/list_folder/read_file/write_file —
# app.core.filesystem_tools). Unregistered tools fall back to a raw JSON code
# block in shape_for_display, which is exactly what these must never do.
# ═══════════════════════════════════════════════════════════════════════════════

def _fs_error(raw: Any) -> Optional[str]:
    if isinstance(raw, dict) and raw.get("error"):
        return raw["error"]
    return None

def _fs_search_exec(raw: Any) -> Dict:
    err = _fs_error(raw)
    if err:
        return {"error": err}
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    matches = (raw.get("matches") or [])[:30]
    return {
        "query": raw.get("query"),
        "root": raw.get("root"),
        "count": raw.get("count"),
        "truncated": raw.get("truncated"),
        "matches": [
            {
                "path": m.get("path"),
                "name": m.get("name"),
                "size_bytes": m.get("size_bytes"),
                "modified": _date(m.get("modified")),
            }
            for m in matches
        ],
    }

def _fs_search_disp(raw: Any) -> str:
    s = _fs_search_exec(raw)
    if "error" in s:
        return f"**Search failed:** {s['error']}"
    matches = s.get("matches", [])
    if not matches:
        return f"No files matching `{s.get('query')}` found."
    total = s.get("count", len(matches))
    trunc_note = " _(truncated)_" if s.get("truncated") else ""
    lines = [f"**{_num(total)} file(s) found**{trunc_note}:\n"]
    for m in matches:
        size = f"{_num(m['size_bytes'])} bytes" if m.get("size_bytes") is not None else "—"
        lines.append(f"- `{m['path']}` — {size} · {m.get('modified', '—')}")
    return "\n".join(lines)

def _fs_list_folder_exec(raw: Any) -> Dict:
    err = _fs_error(raw)
    if err:
        return {"error": err}
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    entries = (raw.get("entries") or [])[:50]
    return {
        "path": raw.get("path"),
        "count": raw.get("count"),
        "truncated": raw.get("truncated"),
        "entries": [
            {
                "name": e.get("name"),
                "is_dir": e.get("is_dir"),
                "size_bytes": e.get("size_bytes"),
                "modified": _date(e.get("modified")),
            }
            for e in entries
        ],
    }

def _fs_list_folder_disp(raw: Any) -> str:
    s = _fs_list_folder_exec(raw)
    if "error" in s:
        return f"**Could not list folder:** {s['error']}"
    entries = s.get("entries", [])
    if not entries:
        return f"`{s.get('path')}` is empty."
    trunc_note = " _(truncated)_" if s.get("truncated") else ""
    lines = [f"**`{s.get('path')}`** — {_num(s.get('count', len(entries)))} item(s){trunc_note}:\n"]
    for e in entries:
        kind = " (folder)" if e.get("is_dir") else ""
        size = f" — {_num(e['size_bytes'])} bytes" if e.get("size_bytes") is not None else ""
        lines.append(f"- {e['name']}{kind}{size} · {e.get('modified', '—')}")
    return "\n".join(lines)

def _fs_read_file_exec(raw: Any) -> Dict:
    """Passes text_preview through untouched — same reasoning as
    _web_scrape_exec: chat.py's read_file dispatch already bounds it to one
    _chunk_text chunk via `offset`, so re-truncating here would silently
    shrink what the caller deliberately sized."""
    err = _fs_error(raw)
    if err:
        return {"error": err}
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    out = {
        "path": raw.get("path"),
        "file_type": raw.get("file_type"),
        "text_preview": raw.get("text_preview", ""),
    }
    if raw.get("note"):
        out["note"] = raw["note"]
    return out

def _fs_read_file_disp(raw: Any) -> str:
    if not isinstance(raw, dict):
        return _trunc(str(raw), 2000)
    err = _fs_error(raw)
    if err:
        return f"**Could not read file:** {err}"
    path = raw.get("path") or ""
    file_type = raw.get("file_type") or ""
    preview = raw.get("text_preview", "")
    note = f"\n\n_{raw['note']}_" if raw.get("note") else ""
    return f"**`{path}`** ({file_type})\n\n```\n{preview}\n```{note}"

def _fs_write_file_exec(raw: Any) -> Dict:
    err = _fs_error(raw)
    if err:
        return {"error": err}
    if not isinstance(raw, dict):
        return {"error": _trunc(str(raw), 300)}
    return {
        "path": raw.get("path"),
        "bytes_written": raw.get("bytes_written"),
        "overwritten": raw.get("overwritten"),
    }

def _fs_write_file_disp(raw: Any) -> str:
    s = _fs_write_file_exec(raw)
    if "error" in s:
        return f"**Write failed:** {s['error']}"
    action = "Overwrote" if s.get("overwritten") else "Wrote"
    return f"{action} **`{s.get('path')}`** ({_num(s.get('bytes_written'))} bytes)."


# ═══════════════════════════════════════════════════════════════════════════════
# Persistent browser session (browser_* tools — app.core.browser_session)
#
# browser_navigate and browser_extract_text land in _web_scrape_exec/_disp
# above (registered a second time under those names below) — chat.py's
# execution loop already normalizes all three into the identical
# {title, text_preview, warnings, note} / {error, note} shape before this
# layer ever sees them, so there's nothing tool-specific left to shape.
# ═══════════════════════════════════════════════════════════════════════════════

def _browser_error(raw: Any) -> Optional[str]:
    """Every browser_* action dict is {error: "..."} on failure — see
    ChatAgent's executor loop, which builds this the same way for all of
    them. Returns None when raw isn't an error dict."""
    if isinstance(raw, dict) and raw.get("error"):
        return raw["error"]
    return None

def _browser_click_exec(raw: Any) -> Dict:
    err = _browser_error(raw)
    if err:
        return {"error": err}
    return {"clicked": raw.get("selector"), "url": raw.get("url"), "title": raw.get("title")}

def _browser_click_disp(raw: Any) -> str:
    err = _browser_error(raw)
    if err:
        return f"**Click failed:** {err}"
    return f"Clicked `{raw.get('selector')}` — now on **{raw.get('title') or raw.get('url')}**."

def _browser_fill_exec(raw: Any) -> Dict:
    err = _browser_error(raw)
    return {"error": err} if err else {"filled": raw.get("selector")}

def _browser_fill_disp(raw: Any) -> str:
    err = _browser_error(raw)
    return f"**Fill failed:** {err}" if err else f"Filled `{raw.get('selector')}`."

def _browser_scroll_exec(raw: Any) -> Dict:
    err = _browser_error(raw)
    return {"error": err} if err else {"scrolled": raw.get("direction")}

def _browser_scroll_disp(raw: Any) -> str:
    err = _browser_error(raw)
    return f"**Scroll failed:** {err}" if err else f"Scrolled {raw.get('direction')}."

def _browser_wait_exec(raw: Any) -> Dict:
    err = _browser_error(raw)
    return {"error": err} if err else {"appeared": raw.get("selector")}

def _browser_wait_disp(raw: Any) -> str:
    err = _browser_error(raw)
    return f"**Wait failed:** {err}" if err else f"`{raw.get('selector')}` appeared."

def _browser_nav_history_exec(raw: Any) -> Dict:
    """Shared by browser_go_back/browser_go_forward — same result shape."""
    err = _browser_error(raw)
    if err:
        return {"error": err}
    return {"url": raw.get("url"), "title": raw.get("title")}

def _browser_nav_history_disp(raw: Any) -> str:
    err = _browser_error(raw)
    if err:
        return f"**Navigation failed:** {err}"
    return f"Now on **{raw.get('title') or raw.get('url')}** ({raw.get('url')})."

def _browser_list_tabs_exec(raw: Any) -> Dict:
    err = _browser_error(raw)
    if err:
        return {"error": err}
    return {"tabs": raw.get("tabs") or [], "current": raw.get("current")}

def _browser_list_tabs_disp(raw: Any) -> str:
    err = _browser_error(raw)
    if err:
        return f"**Could not list tabs:** {err}"
    tabs = raw.get("tabs") or []
    current = raw.get("current")
    lines = ["**Open tabs:**"]
    for t in tabs:
        marker = " ← active" if t.get("index") == current else ""
        lines.append(f"- [{t.get('index')}] {t.get('title') or '(untitled)'} — {t.get('url')}{marker}")
    return "\n".join(lines)

def _browser_switch_tab_exec(raw: Any) -> Dict:
    err = _browser_error(raw)
    if err:
        return {"error": err}
    return {"switched_to": raw.get("tabIndex"), "url": raw.get("url"), "title": raw.get("title")}

def _browser_switch_tab_disp(raw: Any) -> str:
    err = _browser_error(raw)
    if err:
        return f"**Switch tab failed:** {err}"
    return f"Switched to tab {raw.get('tabIndex')}: **{raw.get('title') or raw.get('url')}**."

def _browser_close_exec(raw: Any) -> Dict:
    err = _browser_error(raw)
    return {"error": err} if err else {"closed": True}

def _browser_close_disp(raw: Any) -> str:
    err = _browser_error(raw)
    return f"**Close failed:** {err}" if err else "Browser session closed."

# Deliberately drops screenshot_base64 from what the Executor LLM sees —
# the model has no vision capability in this app (see browser_screenshot's
# own tool description), so the raw image data would just be dead weight
# in its context. The display shaper below is what actually shows it, as
# an inline image, to the human.
def _browser_screenshot_exec(raw: Any) -> Dict:
    err = _browser_error(raw)
    if err:
        return {"error": err}
    return {"note": "Screenshot captured and shown to the user above. You cannot see its contents."}

def _browser_screenshot_disp(raw: Any) -> str:
    err = _browser_error(raw)
    if err:
        return f"**Screenshot failed:** {err}"
    b64 = raw.get("screenshot_base64") or ""
    if not b64:
        return "**Screenshot failed:** no image data returned."
    return f"![Screenshot](data:image/jpeg;base64,{b64})"


# ═══════════════════════════════════════════════════════════════════════════════
# Shaper registry
# ═══════════════════════════════════════════════════════════════════════════════

_ShapeEntry = Tuple[Callable[[Any], Dict], Callable[[Any], str]]

_SHAPERS: Dict[str, _ShapeEntry] = {
    # ── GitHub ──────────────────────────────────────────────────────────────
    "search_repositories":    (_gh_search_repositories_exec, _gh_search_repositories_disp),
    "list_commits":           (_gh_list_commits_exec,         _gh_list_commits_disp),
    "get_repository":         (_gh_get_repository_exec,       _gh_get_repository_disp),
    "list_issues":            (_gh_list_issues_exec,          _gh_list_issues_disp),
    "create_repository":      (_gh_create_repository_exec,    _gh_create_repository_disp),
    "fork_repository":        (_gh_fork_repository_exec,      _gh_fork_repository_disp),
    "create_issue":           (_gh_create_issue_exec,         _gh_create_issue_disp),
    "search_code":            (_gh_search_code_exec,          _gh_search_code_disp),
    "get_file_contents":      (_gh_get_file_contents_exec,    _gh_get_file_contents_disp),
    "push_files":             (_gh_push_files_exec,           _gh_push_files_disp),
    "create_pull_request":    (_gh_create_pull_request_exec,  _gh_create_pull_request_disp),

    # ── Gmail ────────────────────────────────────────────────────────────────
    "list_messages":          (_gmail_list_messages_exec,     _gmail_list_messages_disp),
    "search_messages":        (_gmail_list_messages_exec,     _gmail_list_messages_disp),
    "get_message":            (_gmail_get_message_exec,       _gmail_get_message_disp),
    "send_message":           (_gmail_send_message_exec,      _gmail_send_message_disp),
    "list_drafts":            (_gmail_list_drafts_exec,       _gmail_list_drafts_disp),
    "get_draft":              (_gmail_get_draft_exec,         _gmail_get_draft_disp),

    # ── Google Drive ─────────────────────────────────────────────────────────
    "list_files":             (_drive_list_files_exec,        _drive_list_files_disp),
    "search_files":           (_drive_list_files_exec,        _drive_list_files_disp),
    "get_file":               (_drive_get_file_exec,          _drive_get_file_disp),
    "create_file":            (_drive_create_file_exec,       _drive_create_file_disp),
    "upload_file":            (_drive_create_file_exec,       _drive_create_file_disp),
    "drive_read_file":        (_drive_read_file_exec,         _drive_read_file_disp),
    "google_drive_read_file": (_drive_read_file_exec,         _drive_read_file_disp),

    # ── Google Sheets & Docs ─────────────────────────────────────────────────
    "sheets_read_range":      (_sheets_read_range_exec,       _sheets_read_range_disp),
    "sheets_update_range":    (_sheets_update_range_exec,     _sheets_update_range_disp),
    "docs_read_document":     (_docs_read_document_exec,      _docs_read_document_disp),

    # ── Web scrape (Chat/Agent Mode's built-in tool, not an MCP server) ──────
    "web_scrape":             (_web_scrape_exec,              _web_scrape_disp),

    # ── Local filesystem tools (app.core.filesystem_tools) ────────────────────
    "search_local_files":     (_fs_search_exec,               _fs_search_disp),
    "list_folder":            (_fs_list_folder_exec,           _fs_list_folder_disp),
    "read_file":              (_fs_read_file_exec,             _fs_read_file_disp),
    "write_file":             (_fs_write_file_exec,            _fs_write_file_disp),

    # ── Persistent browser session (app.core.browser_session) — also not
    #    MCP servers. browser_navigate and browser_extract_text reuse the
    #    web_scrape shapers directly: chat.py's executor loop already
    #    normalizes all three into the identical result shape.
    "browser_navigate":       (_web_scrape_exec,              _web_scrape_disp),
    "browser_extract_text":   (_web_scrape_exec,              _web_scrape_disp),
    "browser_click":          (_browser_click_exec,           _browser_click_disp),
    "browser_fill":           (_browser_fill_exec,            _browser_fill_disp),
    "browser_scroll":         (_browser_scroll_exec,          _browser_scroll_disp),
    "browser_wait_for_selector": (_browser_wait_exec,         _browser_wait_disp),
    "browser_screenshot":     (_browser_screenshot_exec,      _browser_screenshot_disp),
    "browser_go_back":        (_browser_nav_history_exec,     _browser_nav_history_disp),
    "browser_go_forward":     (_browser_nav_history_exec,     _browser_nav_history_disp),
    "browser_list_tabs":      (_browser_list_tabs_exec,       _browser_list_tabs_disp),
    "browser_switch_tab":     (_browser_switch_tab_exec,      _browser_switch_tab_disp),
    "browser_close":          (_browser_close_exec,           _browser_close_disp),
}


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def shape_for_executor(tool_name: str, raw: Any) -> Dict:
    """
    Returns a compact, structured dict for the Executor LLM.
    Only includes fields relevant for the next step (IDs, key attributes).
    Falls back to a 2500-char truncated raw dump for unregistered tools.
    """
    entry = _SHAPERS.get(tool_name)
    if entry:
        try:
            return entry[0](raw)
        except Exception as exc:
            logger.warning("Response shaper (executor) failed for '%s': %s", tool_name, exc)

    # Fallback — truncate aggressively
    try:
        raw_str = json.dumps(raw) if not isinstance(raw, str) else raw
    except Exception:
        raw_str = str(raw)
    if len(raw_str) > 2500:
        raw_str = raw_str[:2500] + "\n… [TRUNCATED]"
    return {"raw": raw_str}


def shape_for_display(tool_name: str, raw: Any) -> str:
    """
    Returns a human-readable markdown string for the user.
    Falls back to a truncated JSON code block for unregistered tools.
    """
    entry = _SHAPERS.get(tool_name)
    if entry:
        try:
            return entry[1](raw)
        except Exception as exc:
            logger.warning("Response shaper (display) failed for '%s': %s", tool_name, exc)

    # Fallback
    try:
        raw_str = json.dumps(raw, indent=2)
    except Exception:
        raw_str = str(raw)
    if len(raw_str) > 2000:
        raw_str = raw_str[:2000] + "\n… [TRUNCATED]"
    return f"```json\n{raw_str}\n```"


# ═══════════════════════════════════════════════════════════════════════════════
# Accumulated (multi-page) response shaper
# Called ONCE after all pagination pages are fetched.
# Returns a single concise markdown summary that directly answers the user's
# question — e.g. "You have 87 total commits" not a dump of 87 commit objects.
# ═══════════════════════════════════════════════════════════════════════════════

def _merge_list_field(accumulated: list, field: str) -> list:
    """Flatten a repeated list field from multiple shaped exec pages."""
    out = []
    for page in accumulated:
        if isinstance(page, dict):
            out.extend(page.get(field, []))
    return out


def shape_accumulated_response(
    tool_name: str,
    accumulated_items: list,
    pages_fetched: int,
    raw_items: Optional[list] = None,
) -> str:
    """
    Produce a single user-facing markdown card after all pages of an exhaustive
    fetch are done.  Answers the question directly (count, summary) rather than
    dumping every item.

    Args:
        tool_name:        The MCP tool that was called.
        accumulated_items: List of shape_for_executor outputs, one per page.
        pages_fetched:    How many pages were fetched (for the footer note).
        raw_items:        True (unshaped) tool output, one per page — used only
                           by the single-page fallback below. display_fn is
                           written to consume the same raw shape executor_fn
                           does (it re-derives its own view internally), so it
                           must never be handed executor_fn's already-shaped
                           output — that renames/prunes fields and produces an
                           empty-looking card.
    """
    if not accumulated_items:
        return "_No results returned._"

    page_note = f" · {pages_fetched} pages fetched" if pages_fetched > 1 else ""

    # ── GitHub: list_commits ──────────────────────────────────────────────────
    if tool_name == "list_commits":
        commits = _merge_list_field(accumulated_items, "commits")
        total = len(commits)
        lines = [
            f"### Total commits: **{_num(total)}**{page_note}",
            "",
        ]
        if commits:
            lines.append("**Most recent:**")
            for c in commits[:12]:
                sha = c.get("sha", "")
                msg = _trunc(c.get("message", ""), 72)
                author = c.get("author", "")
                date = c.get("date", "")
                lines.append(f"- `{sha}` {msg} — *{author}* · {date}")
            if total > 12:
                lines.append(f"\n*…and {total - 12} more*")
        return "\n".join(lines)

    # ── GitHub: list_issues ───────────────────────────────────────────────────
    if tool_name == "list_issues":
        issues = _merge_list_field(accumulated_items, "issues")
        total = len(issues)
        lines = [f"### Total issues: **{_num(total)}**{page_note}", ""]
        for iss in issues[:12]:
            num = iss.get("number", "")
            title = _trunc(iss.get("title", ""), 72)
            state = iss.get("state", "")
            lines.append(f"- **#{num}** {title} `{state}`")
        if total > 12:
            lines.append(f"\n*…and {total - 12} more*")
        return "\n".join(lines)

    # ── Gmail: list_messages / search_messages ────────────────────────────────
    if tool_name in ("list_messages", "search_messages"):
        messages = _merge_list_field(accumulated_items, "messages")
        total = len(messages)
        lines = [f"### Total emails: **{_num(total)}**{page_note}", ""]
        for m in messages[:10]:
            subj = _trunc(m.get("subject", "(no subject)"), 60)
            sender = m.get("from", "")
            date = m.get("date", "")
            lines.append(f"- **{subj}** — *{sender}* · {date}")
        if total > 10:
            lines.append(f"\n*…and {total - 10} more*")
        return "\n".join(lines)

    # ── Google Drive: list_files / search_files ───────────────────────────────
    if tool_name in ("list_files", "search_files"):
        files = _merge_list_field(accumulated_items, "files")
        total = len(files)
        lines = [f"### Total files: **{_num(total)}**{page_note}", ""]
        for f in files[:12]:
            name = _trunc(f.get("name", ""), 60)
            mime = f.get("mimeType", "")
            modified = f.get("modifiedTime", "")
            lines.append(f"- **{name}** — `{mime}` · {modified}")
        if total > 12:
            lines.append(f"\n*…and {total - 12} more*")
        return "\n".join(lines)

    # ── Generic single-page fallback ──────────────────────────────────────────
    if len(accumulated_items) == 1:
        raw = raw_items[0] if raw_items else accumulated_items[0]
        return shape_for_display(tool_name, raw)

    # ── Generic multi-page fallback: just count items ─────────────────────────
    # Try to find the first list field and count across pages
    total_items = 0
    for page in accumulated_items:
        if isinstance(page, dict):
            for v in page.values():
                if isinstance(v, list):
                    total_items += len(v)
                    break
    if total_items:
        return f"**{_num(total_items)} total items** fetched across {pages_fetched} pages."
    return f"**{pages_fetched} pages** of results fetched successfully."
