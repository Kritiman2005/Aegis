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
        return f"⚠️ {shaped['error']}"
    repos = shaped.get("repositories", [])
    total = shaped.get("total_count", len(repos))
    if not repos:
        return "No repositories found."
    lines = [f"**Found {_num(total)} repositories** (showing top {len(repos)}):\n"]
    for r in repos:
        priv = "🔒" if r.get("private") else "🌍"
        desc = r.get("description") or "_No description_"
        lines.append(
            f"{priv} **[{r['full_name']}]({r['html_url']})** — "
            f"⭐ {_num(r.get('stars', 0))} · 🍴 {_num(r.get('forks', 0))}\n"
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
        return f"⚠️ {shaped['error']}"
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
        return f"⚠️ {s['error']}"
    return (
        f"## [{s['full_name']}]({s['html_url']})\n"
        f"{s.get('description') or '_No description_'}\n\n"
        f"- ⭐ **{_num(s.get('stars', 0))}** stars · "
        f"🍴 **{_num(s.get('forks', 0))}** forks · "
        f"🐛 **{_num(s.get('open_issues', 0))}** open issues\n"
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
        return f"⚠️ {s['error']}"
    issues = s.get("issues", [])
    if not issues:
        return "No issues found."
    lines = [f"**{_num(s.get('total_shown', len(issues)))} issues shown**:\n"]
    for issue in issues:
        icon = "🟢" if issue["state"] == "open" else "🔴"
        labels = ", ".join(f"`{l}`" for l in issue.get("labels", [])) or "—"
        lines.append(
            f"{icon} **#{issue['number']} {issue['title']}** "
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
        return f"⚠️ {s['error']}"
    return (
        f"✅ Repository **[{s['full_name']}]({s['html_url']})** created!\n\n"
        f"Clone: `git clone {s['clone_url']}`"
    )


def _gh_fork_repository_exec(raw: Any) -> Dict:
    return _gh_create_repository_exec(raw)


def _gh_fork_repository_disp(raw: Any) -> str:
    s = _gh_fork_repository_exec(raw)
    if "error" in s:
        return f"⚠️ {s['error']}"
    return f"✅ Forked as **[{s['full_name']}]({s['html_url']})**"


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
        return f"⚠️ {s['error']}"
    return f"✅ Issue **#{s['number']}: {s['title']}** created! [View]({s['html_url']})"


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
        return f"⚠️ {s['error']}"
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
        return f"⚠️ {s['error']}"
    content = s.get("content") or "_Binary or empty file_"
    return (
        f"📄 **[{s['path']}]({s['html_url']})** ({s.get('size', '?')} bytes)\n\n"
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
        return f"⚠️ {s['error']}"
    return f"✅ Pushed! Commit `{s['sha']}`: _{s.get('message')}_  [View]({s.get('html_url')})"


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
        return f"⚠️ {s['error']}"
    return (
        f"✅ Pull Request **#{s['number']}: {s['title']}** opened!\n"
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
        return f"⚠️ {s['error']}"
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
        return f"⚠️ {s['error']}"
    body_section = s.get("body") or s.get("snippet") or "_No body_"
    cc_line = f"**CC:** {s['cc']}  \n" if s.get("cc") else ""
    return (
        f"### ✉️ {s.get('subject')}\n\n"
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
        return f"⚠️ {s['error']}"
    return f"✅ Email sent! Message ID: `{s.get('id')}`"


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
        return f"⚠️ {s['error']}"
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
        return f"⚠️ {s['error']}"
    body_section = s.get("body") or s.get("snippet") or "_No body_"
    return (
        f"### 📝 Draft: {s.get('subject')}\n\n"
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
        return f"⚠️ {s['error']}"
    files = s.get("files", [])
    if not files:
        return "No files found."
    lines = [f"**{len(files)} file(s)**:\n"]
    for f in files:
        icon = "📁" if "folder" in (f.get("mime_type") or "") else "📄"
        name_part = f"[{f['name']}]({f['web_url']})" if f.get("web_url") else f["name"]
        size_part = f" · {f['size']} bytes" if f.get("size") else ""
        lines.append(f"- {icon} {name_part} · {f.get('modified', '—')}{size_part}")
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
        return f"⚠️ {s['error']}"
    link = f"[{s['name']}]({s['web_url']})" if s.get("web_url") else s["name"]
    size = f"{_num(s.get('size'))} bytes" if s.get("size") else "—"
    return (
        f"📄 **{link}**\n\n"
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
        return f"⚠️ {s['error']}"
    link = f"[{s['name']}]({s['web_url']})" if s.get("web_url") else s["name"]
    return f"✅ **{link}** created! File ID: `{s['id']}`"


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
    return f"📄 **File contents:**\n\n```\n{content}\n```"


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
