"""
Aegis — MCP tool-result content flattening

Shared by stdio_client.py and http_client.py's call_tool — previously each
client duplicated this logic, which is exactly how a bug (image/audio bytes
silently discarded) ended up in both places at once. One function now.

MCP tool results carry a list of typed content blocks (text/resource/image/
audio). Aegis's whole pipeline downstream of call_tool (engine.py's
_dispatch_tool, the chat agent, workflow node outputs) expects one flat
string, not a structured content array, so this still flattens to text —
but an image/audio block with real data is now embedded as a markdown data
URI instead of being replaced with a lossy placeholder string. Any renderer
that already displays this text as markdown (ChatView's MarkdownContent)
shows the image inline for free, with no separate plumbing; text-only
consumers (e.g. WorkflowsView's raw node-output view) still get a globally
unique, greppable marker instead of losing the data outright.
"""

import json


def flatten_content_item(item: dict) -> str:
    item_type = item.get("type", "")

    if item_type == "text":
        return item.get("text", "")

    if item_type == "resource":
        return json.dumps(item.get("resource", {}))

    if item_type == "image":
        data = item.get("data")
        mime_type = item.get("mimeType") or "image/png"
        if data:
            return f"![Tool result image](data:{mime_type};base64,{data})"
        if item.get("url"):
            return f"![Tool result image]({item['url']})"
        return "[image result: no data or url provided]"

    if item_type == "audio":
        data = item.get("data")
        mime_type = item.get("mimeType") or "audio/mpeg"
        if data:
            return f"[\U0001F50A Play audio result](data:{mime_type};base64,{data})"
        return "[audio result: no data provided]"

    # Any content type not in the 2024-11-05 baseline (or a server-specific
    # extension) — name it instead of silently dropping it.
    return f"[unsupported content type: {item_type or 'unknown'}]"
