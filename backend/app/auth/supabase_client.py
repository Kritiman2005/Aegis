"""
Aegis — Supabase Account Client

Talks directly to Supabase's own REST API (GoTrue auth + PostgREST) for the
Aegis cloud account (sign up / log in / log out / entitlement check) — see
the Aegis-webapp repo's supabase/migrations/0001_profiles.sql for the
`profiles` table this reads from.

The desktop app deliberately never goes through the Next.js website's pages
for this — Supabase's Auth API already returns a clean
{access_token, refresh_token, user} JSON contract, so there's nothing a
custom backend route would add.

SUPABASE_URL / SUPABASE_ANON_KEY are NOT secrets — the anon/publishable key
is meant to be embedded in client code (it's already public in the
website's browser bundle); Row Level Security on the `profiles` table is
what actually protects data, not this key. That's also why these are plain
constants here rather than .env values: .env is dev-only and isn't bundled
into a packaged build (see main.py), but every Aegis install — dev or
packaged — needs to reach the same Supabase project.

Never add SUPABASE_SERVICE_ROLE_KEY here. That key bypasses Row Level
Security entirely and must only ever live server-side (a future Stripe
webhook host), never in anything shipped to a user's machine.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# No explicit verify= on any httpx client in this file — main.py's
# truststore.inject_into_ssl() (called at process startup) already makes
# every SSL connection use the OS's own native trust evaluation instead of
# a fixed bundled CA file, which also correctly handles a locally-installed
# TLS-inspecting VPN/antivirus certificate a fixed certifi bundle wouldn't.

SUPABASE_URL = "https://pkmjkbiwafpqumjrsvfl.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_X-Xt1m5sMJf_h3VJts6tnQ_Yj9dhyzo"

_AUTH_BASE = f"{SUPABASE_URL}/auth/v1"
_REST_BASE = f"{SUPABASE_URL}/rest/v1"


class SupabaseAuthError(Exception):
    """A Supabase API call returned a non-2xx response with a real error message."""
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _error_message(res: httpx.Response) -> str:
    try:
        data = res.json()
        return data.get("error_description") or data.get("msg") or data.get("message") or data.get("error") or res.text
    except Exception:
        return res.text or f"Supabase request failed ({res.status_code})"


async def sign_up(email: str, password: str) -> dict:
    """
    Returns {"session": {...} | None, "user": {...}}. `session` is None when
    the Supabase project requires email confirmation (the default) — the
    caller must treat that as "check your email", not a logged-in state.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            f"{_AUTH_BASE}/signup",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password},
        )
        if res.status_code >= 400:
            raise SupabaseAuthError(_error_message(res), res.status_code)
        return res.json()


async def sign_in(email: str, password: str) -> dict:
    """Returns {"access_token", "refresh_token", "expires_in", "user": {...}}."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            f"{_AUTH_BASE}/token",
            params={"grant_type": "password"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password},
        )
        if res.status_code >= 400:
            raise SupabaseAuthError(_error_message(res), res.status_code)
        return res.json()


async def refresh_session(refresh_token: str) -> dict:
    """Returns a fresh {"access_token", "refresh_token", ...} — Supabase rotates the refresh token too."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            f"{_AUTH_BASE}/token",
            params={"grant_type": "refresh_token"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"refresh_token": refresh_token},
        )
        if res.status_code >= 400:
            raise SupabaseAuthError(_error_message(res), res.status_code)
        return res.json()


async def sign_out(access_token: str) -> None:
    """Best-effort — revokes the session server-side. Callers should clear local state regardless of outcome."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{_AUTH_BASE}/logout",
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
            )
    except Exception as e:
        logger.warning(f"Supabase sign_out call failed (non-fatal, local session is cleared regardless): {e}")


async def get_user(access_token: str) -> dict:
    """
    Returns {"id", "email", ...} for the token's owner, verified by Supabase
    itself — used by the browser-based sign-in handoff (see
    app/api/account_auth.py's /session route) so a client-supplied token is
    never trusted without Supabase confirming it's real first.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(
            f"{_AUTH_BASE}/user",
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
        )
        if res.status_code >= 400:
            raise SupabaseAuthError(_error_message(res), res.status_code)
        return res.json()


async def get_profile_plan(access_token: str, user_id: str) -> Optional[str]:
    """
    Reads this user's own `plan` from the `profiles` table. RLS restricts the
    query to the caller's own row regardless of the `id` filter, so this can
    never leak another user's plan even if user_id were ever wrong.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(
            f"{_REST_BASE}/profiles",
            params={"id": f"eq.{user_id}", "select": "plan"},
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
        )
        if res.status_code >= 400:
            raise SupabaseAuthError(_error_message(res), res.status_code)
        rows = res.json()
        return rows[0]["plan"] if rows else None


async def get_full_catalog(access_token: str) -> list[dict]:
    """
    Reads the entire public connector catalog from `connectors` — not scoped
    to any one user, since which connectors EXIST isn't account-specific
    (whether they're usable is: see catalog.py's `locked` gating, driven by
    the account's own `plan`). The table's RLS policy is `for select using
    (true)` (supabase/migrations/0003_connectors.sql on the webapp side), so
    any valid access token can read it — still passed through rather than
    calling anonymously, same convention as get_profile_plan.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(
            f"{_REST_BASE}/connectors",
            params={"is_active": "eq.true", "select": "*"},
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
        )
        if res.status_code >= 400:
            raise SupabaseAuthError(_error_message(res), res.status_code)
        return res.json()
