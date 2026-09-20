"""
Aegis — Account Auth API (/api/account)

Sign up / log in / log out / status for the Aegis cloud account (Supabase-
backed — see app/auth/supabase_client.py for why the desktop app talks to
Supabase directly instead of a custom backend route). Deliberately under
/api/account rather than /auth, which is already used by the MCP connector
OAuth routes (a different concept: authorizing a third-party service, not
signing into Aegis itself).
"""

import logging
import ssl
from datetime import datetime, timedelta
from typing import List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import AegisAccount, RemoteConnector, MCPServer
from app.auth import token_store
from app.auth.supabase_client import (
    SupabaseAuthError,
    sign_up as supabase_sign_up,
    sign_in as supabase_sign_in,
    sign_out as supabase_sign_out,
    refresh_session as supabase_refresh_session,
    get_profile_plan,
    get_full_catalog,
    get_user as supabase_get_user,
)

# The Aegis website — hardcoded rather than env-driven, same reasoning as
# SUPABASE_URL in supabase_client.py: every install (dev or packaged) needs
# to reach the same fixed service, and .env isn't bundled into a packaged
# build.
WEBAPP_URL = "https://aegisaistudio.online"

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/account", tags=["account"])

# The short-lived access token isn't persisted (see AegisAccount's docstring)
# — kept here instead, re-derived from the refresh token whenever missing or
# expired. Fine for a single-user desktop app; there's only ever one session.
_access_token: str | None = None
_access_token_expires_at: datetime | None = None

# Re-sync the cached plan from Supabase at most this often — keeps /status
# instant and offline-safe while still catching a plan change (e.g. after
# upgrading) within a reasonable window.
_PLAN_RESYNC_INTERVAL = timedelta(hours=6)

# Same reasoning, for the local cache of the full public connector catalog
# (see RemoteConnector and _resync_catalog_in_background below) —
# deliberately the same interval as the plan resync so both land in the
# same background pass off the same /status call, not two separate timers.
_CONNECTORS_RESYNC_INTERVAL = timedelta(hours=6)


class SignUpRequest(BaseModel):
    email: str
    password: str
    model_config = {"defer_build": True}


class LoginRequest(BaseModel):
    email: str
    password: str
    model_config = {"defer_build": True}


def _account_row(db: Session) -> AegisAccount | None:
    return db.query(AegisAccount).filter(AegisAccount.id == 1).first()


def _real_refresh_token(account: AegisAccount) -> str:
    """
    Resolves the actual refresh_token value for this account — from the OS
    keychain if that's where it's stored (the normal case), or straight
    from the column if the keychain wasn't available when it was written
    (see token_store's fallback). Never returns the sentinel itself.
    """
    if token_store.is_keychain_sentinel(account.refresh_token):
        value = token_store.get_refresh_token()
        if not value:
            # The DB says "it's in the keychain" but the keychain doesn't have
            # it — e.g. a different OS user account, or it was cleared
            # outside the app. Treat like any other invalid refresh token.
            raise SupabaseAuthError("Local session credential is missing — please sign in again.", 401)
        return value
    return account.refresh_token


def _store_refresh_token(account: AegisAccount, value: str) -> None:
    """
    Persists `value` as this account's refresh_token — in the OS keychain
    when available (account.refresh_token then holds only the sentinel, so
    aegis.db never has the real bearer credential in plaintext), or
    directly in the column otherwise. Caller still needs to db.commit().
    """
    if token_store.set_refresh_token(value):
        account.refresh_token = token_store.KEYCHAIN_SENTINEL
    else:
        account.refresh_token = value


async def _ensure_access_token(db: Session, account: AegisAccount) -> str:
    """Returns a valid access token, refreshing via the stored refresh_token if needed."""
    global _access_token, _access_token_expires_at
    if _access_token and _access_token_expires_at and datetime.utcnow() < _access_token_expires_at:
        return _access_token

    session = await supabase_refresh_session(_real_refresh_token(account))
    _access_token = session["access_token"]
    _access_token_expires_at = datetime.utcnow() + timedelta(seconds=session.get("expires_in", 3600) - 60)
    # Supabase rotates the refresh token on every use — the old one stops working.
    _store_refresh_token(account, session["refresh_token"])
    db.commit()
    return _access_token


async def _persist_session(db: Session, session: dict) -> AegisAccount:
    """
    Shared by login and signup-with-an-immediate-session: caches the access
    token in memory, fetches the entitlement plan, and upserts the single
    local account row. Whichever caller ends up with a usable Supabase
    session must go through this — a session that's acknowledged but never
    persisted would leave /status reporting logged out.
    """
    global _access_token, _access_token_expires_at
    user = session["user"]
    _access_token = session["access_token"]
    _access_token_expires_at = datetime.utcnow() + timedelta(seconds=session.get("expires_in", 3600) - 60)

    plan = "free"
    try:
        plan = await get_profile_plan(_access_token, user["id"]) or "free"
    except SupabaseAuthError as e:
        logger.warning(f"Could not fetch plan right after auth (defaulting to 'free'): {e.message}")

    existing = _account_row(db)
    if existing:
        db.delete(existing)
        db.flush()
    account = AegisAccount(
        id=1,
        supabase_user_id=user["id"],
        email=user["email"],
        refresh_token="",  # set below via _store_refresh_token
        cached_plan=plan,
        plan_synced_at=datetime.utcnow(),
    )
    _store_refresh_token(account, session["refresh_token"])
    db.add(account)
    db.commit()
    return account


@router.post("/signup")
async def signup(req: SignUpRequest, db: Session = Depends(get_db)):
    try:
        result = await supabase_sign_up(req.email, req.password)
    except SupabaseAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    if not result.get("session"):
        # Email confirmation is required (the default) — no usable session yet.
        return {"status": "confirm_email", "email": req.email}

    # Confirmation disabled on the Supabase project — session is usable immediately.
    account = await _persist_session(db, result["session"])
    return {"status": "logged_in", "email": account.email, "plan": account.cached_plan}


@router.post("/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    try:
        session = await supabase_sign_in(req.email, req.password)
    except SupabaseAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    account = await _persist_session(db, session)
    return {"email": account.email, "plan": account.cached_plan}


@router.post("/logout")
async def logout(db: Session = Depends(get_db)):
    global _access_token, _access_token_expires_at
    account = _account_row(db)
    if account:
        try:
            token = await _ensure_access_token(db, account)
            await supabase_sign_out(token)
        except Exception as e:
            logger.warning(f"Supabase logout call failed (clearing local session anyway): {e}")
        db.delete(account)
        db.commit()
        token_store.delete_refresh_token()

    _access_token = None
    _access_token_expires_at = None
    return {"status": "logged_out"}


def _resync_plan_in_background(db: Session, account_id: int):
    """
    Runs after the response is sent — refreshes the session and re-fetches
    the plan, then updates the cached row. Best-effort: any failure (offline,
    Supabase hiccup, revoked session) is logged and silently dropped so a
    background sync can never turn a working /status call into an error.
    """
    import asyncio

    async def _run():
        try:
            account = _account_row(db)
            if not account:
                return
            token = await _ensure_access_token(db, account)
            plan = await get_profile_plan(token, account.supabase_user_id)
            if plan:
                account.cached_plan = plan
            account.plan_synced_at = datetime.utcnow()
            db.commit()
        except Exception as e:
            reason = "a TLS-inspecting VPN/antivirus/firewall on this network" if _is_cert_trust_error(e) else "likely offline"
            logger.info(f"Background plan resync skipped ({reason}): {e}")
        finally:
            db.close()

    asyncio.run(_run())


def _resync_catalog_in_background(db: Session, account_id: int):
    """
    Sibling to _resync_plan_in_background, same best-effort/never-raise
    contract: pulls the FULL public connector catalog from
    aegisaistudio.online (see get_full_catalog) and upserts every row into
    the local RemoteConnector cache that app/mcp/catalog.py's
    get_merged_catalog folds into the Connectors panel. There's no more
    per-connector "selection" step — whether a cached entry is actually
    usable is decided at merge time by the account's plan (see
    remote_entry_to_catalog_dict's `locked` field), not by what's synced
    here. Syncing everything regardless of plan means a free account still
    SEES the full catalog (locked) rather than nothing.

    A version bump on a connector that's currently actively connected
    (a matching MCPServer row with status=="connected") sets
    needs_reconnect instead of overwriting command_json/env_schema_json out
    from under a running subprocess — the frontend surfaces that flag as a
    "this connector was updated, reconnect to apply" prompt rather than the
    change silently taking hold (or not) on an already-running server.
    """
    import asyncio
    import json

    async def _run():
        try:
            account = _account_row(db)
            if not account:
                return
            token = await _ensure_access_token(db, account)
            catalog_rows = await get_full_catalog(token)

            seen_ids = set()
            for remote in catalog_rows:
                connector_id = remote["id"]
                seen_ids.add(connector_id)
                incoming_version = remote.get("version", 1)

                existing = db.query(RemoteConnector).filter(RemoteConnector.id == connector_id).first()
                is_connected = db.query(MCPServer).filter(
                    MCPServer.name == connector_id, MCPServer.status == "connected"
                ).first() is not None

                if existing and incoming_version > existing.version and is_connected:
                    # Don't rewrite a live server's config — just flag it.
                    existing.needs_reconnect = True
                    existing.version = incoming_version
                    existing.synced_at = datetime.utcnow()
                    continue

                fields = dict(
                    display_name=remote["display_name"],
                    category=remote["category"],
                    description=remote["description"],
                    icon=remote["icon"],
                    auth_type=remote["auth_type"],
                    command_json=json.dumps(remote.get("command")) if remote.get("command") else None,
                    env_schema_json=json.dumps(remote.get("env_schema") or []),
                    input_schema_json=json.dumps(remote.get("input_schema") or []),
                    oauth_service=remote.get("oauth_service"),
                    setup_guide=remote.get("setup_guide") or "",
                    version=incoming_version,
                    synced_at=datetime.utcnow(),
                )
                if existing:
                    for key, value in fields.items():
                        setattr(existing, key, value)
                else:
                    db.add(RemoteConnector(id=connector_id, needs_reconnect=False, **fields))

            # A connector removed from the website's catalog entirely is
            # dropped from the local cache too — unless it's still actively
            # connected, in which case leave it running and visible until
            # the user disconnects it themselves.
            stale = db.query(RemoteConnector).filter(~RemoteConnector.id.in_(seen_ids)).all() if seen_ids else db.query(RemoteConnector).all()
            for rc in stale:
                still_connected = db.query(MCPServer).filter(
                    MCPServer.name == rc.id, MCPServer.status == "connected"
                ).first() is not None
                if not still_connected:
                    db.delete(rc)

            account.connectors_synced_at = datetime.utcnow()
            db.commit()
        except Exception as e:
            reason = "a TLS-inspecting VPN/antivirus/firewall on this network" if _is_cert_trust_error(e) else "likely offline"
            logger.info(f"Background connector resync skipped ({reason}): {e}")
        finally:
            db.close()

    asyncio.run(_run())


@router.get("/status")
def status(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Always returns instantly from the local cache — never blocks on a
    network call, so this stays safe to poll on every app launch even
    offline. Kicks best-effort background resyncs when the cached plan
    and/or catalog cache is stale (see _PLAN_RESYNC_INTERVAL /
    _CONNECTORS_RESYNC_INTERVAL).
    """
    account = _account_row(db)
    if not account:
        return {"logged_in": False}

    from app.db.database import SessionLocal
    if datetime.utcnow() - account.plan_synced_at > _PLAN_RESYNC_INTERVAL:
        background_tasks.add_task(_resync_plan_in_background, SessionLocal(), account.id)
    if not account.connectors_synced_at or datetime.utcnow() - account.connectors_synced_at > _CONNECTORS_RESYNC_INTERVAL:
        background_tasks.add_task(_resync_catalog_in_background, SessionLocal(), account.id)

    return {"logged_in": True, "email": account.email, "plan": account.cached_plan}


@router.post("/resync")
def force_resync(db: Session = Depends(get_db)):
    """
    Immediately refreshes both the cached plan and the connector catalog
    against Supabase, bypassing /status's normal 6-hour staleness gate —
    for the one moment that actually needs to be instant: the Connectors
    panel opening (it calls this on mount) or a manual "Refresh" click,
    either of which may follow seconds after a real payment. Waiting up to
    6 hours for the lazy /status resync to notice would make the app look
    broken right after someone pays.

    Unlike /status's own resync (deferred onto BackgroundTasks so an
    offline poll never blocks), this calls the exact same
    _resync_plan_in_background / _resync_catalog_in_background functions
    directly instead — a deliberate, user-initiated action can reasonably
    block on one real Supabase round trip, and the caller needs to know
    the resync actually finished before it re-fetches the catalog,
    not merely that it was scheduled.
    """
    account = _account_row(db)
    if not account:
        return {"logged_in": False}

    from app.db.database import SessionLocal
    _resync_plan_in_background(SessionLocal(), account.id)
    _resync_catalog_in_background(SessionLocal(), account.id)

    db.refresh(account)

    return {"logged_in": True, "email": account.email, "plan": account.cached_plan}


# ─── Browser-based sign-in handoff ─────────────────────────────────────────
#
# The desktop app never shows its own password field — "Sign In" opens the
# Aegis website's real login page (2FA, password managers, social login all
# work there) in the system browser. Once that page has a Supabase session,
# it redirects the browser straight here — the same pattern this codebase
# already uses for connector OAuth callbacks (see app/api/oauth_routes.py),
# just for the Aegis account itself instead of a third-party service.
#
# The browser is only ever handed a one-time `code`, never the real tokens —
# see the website's src/app/api/desktop-handoff/*. Putting the real
# access/refresh tokens directly in a URL a browser navigates to (an earlier
# version of this route did exactly that) means they end up sitting in two
# places outside this app's control indefinitely: the browser's own
# address-bar history, and this backend's own request logs (uvicorn's
# default access logger prints the full request line, query string
# included). The exchange call below is server-to-server — the tokens never
# touch a URL or a browser at any point after the website mints the code.

def _session_success_html() -> str:
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <title>Signed In — Aegis</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: #0d0d0d; color: #f0f0f0;
                display: flex; justify-content: center; align-items: center; min-height: 100vh;
            }
            .card {
                background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 16px;
                padding: 48px 56px; text-align: center; max-width: 420px;
            }
            .icon { font-size: 56px; margin-bottom: 20px; }
            h1 { font-size: 22px; font-weight: 600; margin-bottom: 10px; color: #fff; }
            p { font-size: 14px; color: #888; line-height: 1.6; }
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">✅</div>
            <h1>Signed in to Aegis</h1>
            <p>You can close this tab and return to the app.</p>
        </div>
    </body>
    </html>
    """


def _session_error_html(message: str, steps: Optional[List[str]] = None) -> str:
    steps_html = ""
    if steps:
        items = "".join(f"<li>{step}</li>" for step in steps)
        steps_html = f'<ul style="text-align:left;font-size:13px;color:#aaa;margin-top:16px;padding-left:20px;line-height:1.7">{items}</ul>'
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <title>Sign In Failed — Aegis</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: #0d0d0d; color: #f0f0f0;
                display: flex; justify-content: center; align-items: center; min-height: 100vh;
            }}
            .card {{
                background: #1a1a1a; border: 1px solid #3a1a1a; border-radius: 16px;
                padding: 48px 56px; text-align: center; max-width: 520px;
            }}
            h1 {{ color: #f87171; margin: 16px 0 8px; font-size: 20px; }}
            p {{ font-size: 13px; color: #888; margin-top: 8px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div style="font-size:48px">❌</div>
            <h1>Could not sign in</h1>
            <p>{message}</p>
            {steps_html}
            <p style="margin-top:16px;color:#666">You can close this tab and try again from Aegis.</p>
        </div>
    </body>
    </html>
    """


def _is_cert_trust_error(e: BaseException) -> bool:
    """True for a TLS certificate-verification failure specifically — as
    opposed to a plain offline/DNS/timeout error — which almost always
    means something on THIS network is intercepting HTTPS traffic with its
    own certificate (a corporate VPN, antivirus, or firewall product doing
    TLS inspection) rather than the Aegis website actually being
    unreachable. Checked by walking the exception's __cause__ chain (httpx
    wraps the underlying ssl.SSLCertVerificationError in its own
    ConnectError) and falling back to a substring match on the message,
    since not every Python/OpenSSL build raises the same exact exception
    type for this."""
    seen = set()
    current: Optional[BaseException] = e
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(current) or "certificate verify failed" in str(current).lower():
            return True
        current = current.__cause__
    return False


@router.get("/session")
async def browser_session_handoff(
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    The redirect target for the website's /desktop/callback page — never
    called by the desktop app's own UI directly. `code` is a one-time,
    2-minute handoff code (see WEBAPP_URL's docstring above); this exchanges
    it server-to-server for the real tokens, then re-verifies the returned
    access_token against Supabase itself (never trusts a client-supplied
    identity) before persisting the session, exactly like login/signup do.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{WEBAPP_URL}/api/desktop-handoff/exchange",
                json={"code": code},
            )
        if res.status_code != 200:
            detail = "This sign-in link expired or was already used — please try signing in again from Aegis."
            try:
                detail = res.json().get("error", detail)
            except Exception:
                pass
            return HTMLResponse(content=_session_error_html(detail), status_code=res.status_code)
        payload = res.json()
    except httpx.HTTPError as e:
        logger.error(f"Desktop handoff exchange failed: {e}")
        if _is_cert_trust_error(e):
            return HTMLResponse(
                content=_session_error_html(
                    "Your network or security software is blocking a secure connection to aegisaistudio.online.",
                    steps=[
                        "This usually means a VPN, antivirus, or corporate firewall on this network is inspecting HTTPS traffic with its own certificate.",
                        "If you're on a VPN, try turning it off and signing in again.",
                        "If you can't turn it off, check its settings for an HTTPS-scanning exception for aegisaistudio.online.",
                        "Otherwise, try a different network (e.g. a phone hotspot) and sign in again from there.",
                    ],
                ),
                status_code=502,
            )
        return HTMLResponse(
            content=_session_error_html("Could not reach the Aegis website to complete sign-in."),
            status_code=502,
        )

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in", 3600)
    if not access_token or not refresh_token:
        return HTMLResponse(content=_session_error_html("Malformed handoff response."), status_code=502)

    try:
        user = await supabase_get_user(access_token)
    except SupabaseAuthError as e:
        return HTMLResponse(content=_session_error_html(e.message), status_code=e.status_code)

    session = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "user": user,
    }
    await _persist_session(db, session)
    return HTMLResponse(content=_session_success_html())
