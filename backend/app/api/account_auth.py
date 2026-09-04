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
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import AegisAccount
from app.auth import token_store
from app.auth.supabase_client import (
    SupabaseAuthError,
    sign_up as supabase_sign_up,
    sign_in as supabase_sign_in,
    sign_out as supabase_sign_out,
    refresh_session as supabase_refresh_session,
    get_profile_plan,
    get_user as supabase_get_user,
)

# The Aegis website — hardcoded rather than env-driven, same reasoning as
# SUPABASE_URL in supabase_client.py: every install (dev or packaged) needs
# to reach the same fixed service, and .env isn't bundled into a packaged
# build.
WEBAPP_URL = "https://aegis-webapp-theta.vercel.app"

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
            logger.info(f"Background plan resync skipped (likely offline): {e}")
        finally:
            db.close()

    asyncio.run(_run())


@router.get("/status")
def status(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Always returns instantly from the local cache — never blocks on a
    network call, so this stays safe to poll on every app launch even
    offline. Kicks a best-effort background resync when the cached plan is
    stale (see _PLAN_RESYNC_INTERVAL).
    """
    account = _account_row(db)
    if not account:
        return {"logged_in": False}

    if datetime.utcnow() - account.plan_synced_at > _PLAN_RESYNC_INTERVAL:
        from app.db.database import SessionLocal
        background_tasks.add_task(_resync_plan_in_background, SessionLocal(), account.id)

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


def _session_error_html(message: str) -> str:
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
                padding: 48px 56px; text-align: center; max-width: 480px;
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
            <p style="margin-top:16px;color:#666">You can close this tab and try again from Aegis.</p>
        </div>
    </body>
    </html>
    """


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
