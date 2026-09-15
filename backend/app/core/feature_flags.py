"""
Aegis — Feature Flags

Small, explicit on/off switches for features that are temporarily paused at
the product level (not a bug fix, not a config the user should touch).
Single source of truth: anything gated on a flag here should ALWAYS check
it from this module, not re-derive its own copy of the condition.
"""

# Connecting OAuth-based connectors (Slack, Notion, GitHub, Google Drive,
# etc.) requires each user to bring their own OAuth client_id/client_secret,
# registered with the provider under their own account, and paste it into
# the Connectors panel before the first Connect click — see
# app.auth.oauth_service.save_client_credentials / app.auth.google_oauth
# .save_google_credentials. Aegis has no hosted OAuth broker and never runs
# a shared app on anyone's behalf; this flag just gates the connectors/oauth
# routers in main.py, the startup auto-restore of saved connections, and the
# chat-side "you'd need X connected" suggestions in agents/chat.py.
#
# Flip to False only to hide the whole OAuth connector category again (e.g.
# while iterating on it) — existing connected-server rows in SQLite are left
# alone either way, so re-enabling brings old connections back too.
CONNECTORS_ENABLED = True
