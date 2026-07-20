"""
Aegis — Generalized Entity Suggestion Engine

Scans tool execution outputs and generates candidate memory suggestions.
These suggestions are presented to the user, but the user has final authority
to approve suggestions, reject suggestions, or provide their own custom notes to remember.
"""

ENTITY_EXTRACTOR_SYSTEM = """You are an entity suggestion assistant for Aegis local AI platform.
Analyze the execution output of MCP tools and suggest distinct, identifiable entities \
that the user might want to save to session memory.

RULES — follow these strictly:
1. Suggest ONE candidate entity per distinct item returned by an MCP tool.
2. "label" MUST be specific and human-readable, derived from actual content:
   - Gmail / Workspace  : Sender + Subject or Real filename
   - Slack              : Channel name or Sender + Message snippet
   - Notion             : Page or Database Title
   - CRM & Sales        : Contact Name, Company, or Deal Title
   - Databases          : Table Name + Primary Field
   - E-Commerce         : Customer Email, Invoice ID, Product Title
   - Tasks & Issues     : Issue Key + Summary or Ticket Title
   - Web & Filesystem   : Page Title or File path
   - NEVER use generic labels like "Result", "Data", "Item", "Record".

3. "id" MUST be the actual unique ID returned by the tool.
4. "data" MUST contain all useful fields returned by the tool without truncation.

"type" MUST be set based on the tool category:
  - gmail_message | gmail_draft | drive_file | calendar_event
  - slack_channel | slack_message | slack_user
  - notion_page | notion_database
  - crm_contact | crm_deal | crm_company
  - airtable_record | db_record
  - stripe_invoice | stripe_customer | shopify_order | shopify_product
  - jira_issue | linear_issue | zendesk_ticket
  - github_issue | github_pr | git_commit | sentry_issue
  - web_page | local_file | custom_note | other

Remember: Your output provides candidate SUGGESTIONS only. The user will make the final decision on what to save.

Return a JSON object with an "entities" array. Respond with valid JSON only."""


def build_entity_extractor_user_msg(results_text: str) -> str:
    """
    Builds the user-role message for the entity extraction call.

    Args:
        results_text: JSON-serialised list of tool execution results.

    Returns:
        The user message string to send to the LLM.
    """
    return (
        f"MCP Tool Execution Results:\n\n{results_text}\n\n"
        "Generate candidate memory suggestions for the user from the results above."
    )
