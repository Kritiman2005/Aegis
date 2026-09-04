"""
Aegis — Feature Flags

Small, explicit on/off switches for features that are temporarily paused at
the product level (not a bug fix, not a config the user should touch).
Single source of truth: anything gated on a flag here should ALWAYS check
it from this module, not re-derive its own copy of the condition.
"""

# Connecting MCP servers / OAuth services (Slack, Notion, GitHub, Google
# Drive, etc.) currently requires the user to bring their own OAuth
# client_id/client_secret — fine for testing, not something a non-coder can
# do. This is paused while Aegis builds a hosted OAuth broker, gets each
# provider's app verified, and ships the marketing site/demos for it.
#
# Flip back to True to restore: this single flag gates the connectors/oauth
# routers in main.py, the startup auto-restore of saved connections, and the
# chat-side "you'd need X connected" suggestions in agents/chat.py — nothing
# else needs to change. Existing connected-server rows in SQLite are left
# alone (not deleted), so re-enabling brings old test connections back too.
CONNECTORS_ENABLED = False
