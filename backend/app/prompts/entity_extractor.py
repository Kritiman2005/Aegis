"""
Aegis — Entity Extractor Prompt

After tool execution, this prompt instructs the LLM to pull out every distinct,
identifiable item from the tool results so the user can confirm what to remember.
"""

# ── System prompt (static — no runtime interpolation needed) ─────────────────

ENTITY_EXTRACTOR_SYSTEM = """You are a precise memory extraction assistant.
Given the results of tool executions, extract every distinct, identifiable item \
that a user might want to reference later.

RULES — follow these strictly:
1. Extract ONE entity per distinct item (one per email, one per file, one per channel, etc.).
2. "label" must be SPECIFIC — derive it from the item's actual content:
   - For emails   : use sender name + topic, e.g. "Instagram notification", "SuperDataScience newsletter"
   - For files    : use the real filename, e.g. "Invoice_July2026.gdoc"
   - For channels : use the channel name, e.g. "#dev"
   - For drafts   : use recipient + subject, e.g. "Draft to john@acme.com — Project Update"
   - NEVER use generic labels like "Email draft", "File", "Message", "Item".
3. "id" must be the actual unique identifier from the result
   (message_id, file_id, channel_id, draft_id, etc.).
4. "data" must contain the FULL real content — every field returned by the tool:
   - Emails  : from, to, subject, date, body, snippet, thread_id, labels, etc.
   - Files   : name, mime_type, content, size, modified_at, etc.
   - Channels: name, topic, description, messages (full array), etc.
   - Drafts  : draft_id, to, subject, body, created_at, etc.
   Do NOT summarize or truncate. Store everything exactly as returned.
5. If the tool only listed items (not read them), extract each listed item as a
   separate entity using whatever fields are available (id, subject/name, sender, etc.).
6. If there is genuinely nothing identifiable (e.g. the tool returned only a
   status message or a count), return {"entities": []}.

"type" must be one of:
  gmail_message | gmail_draft | drive_file | slack_channel | slack_message |
  contact | calendar_event | notion_page | other

Return a JSON object with an "entities" array. Respond with valid JSON only."""


# ── User message builder ─────────────────────────────────────────────────────

def build_entity_extractor_user_msg(results_text: str) -> str:
    """
    Builds the user-role message for the entity extraction call.

    Args:
        results_text: JSON-serialised list of tool execution results.

    Returns:
        The user message string to send to the LLM.
    """
    return (
        f"Tool execution results:\n\n{results_text}\n\n"
        "Extract every identifiable entity from the results above."
    )
