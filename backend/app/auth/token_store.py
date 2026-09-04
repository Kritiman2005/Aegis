"""
Aegis — Refresh Token Storage

Wraps the OS's own credential store (macOS Keychain / Windows Credential
Manager / Linux Secret Service, via the `keyring` package) so the Supabase
refresh_token — a long-lived bearer credential — isn't sitting in plaintext
in aegis.db, readable by anything with local file access.

Deliberately falls back to plaintext storage in the caller-provided SQLite
column if the OS keychain is unavailable for any reason (unsupported
platform, no secret service running on a headless Linux box, a packaging
issue on a platform this couldn't be tested against) — a security
hardening step must never be the reason sign-in stops working entirely.
Callers persist AegisAccount.refresh_token as either the literal sentinel
_KEYCHAIN_SENTINEL (real value lives in the OS keychain) or the actual
token (fallback path) — see is_keychain_sentinel().
"""

import logging

logger = logging.getLogger(__name__)

_SERVICE_NAME = "Aegis"
_KEY_NAME = "refresh_token"

# Stored in the DB column in place of the real token when the OS keychain
# holds it — lets readers tell "look in the keychain" apart from "this
# literal value is the fallback-path token" without a second DB column.
KEYCHAIN_SENTINEL = "__stored_in_os_keychain__"


def is_keychain_sentinel(value: str | None) -> bool:
    return value == KEYCHAIN_SENTINEL


def _get_working_keyring():
    """
    Returns the keyring module if a real backend is available, else None.
    keyring transparently falls back to its own "fail" backend (raises on
    every call) when no real OS backend is usable — checked explicitly so
    callers get a clean None instead of a surprise exception.
    """
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
        return keyring
    except Exception as e:
        logger.warning(f"[token_store] keyring unavailable, will fall back to local storage: {e}")
        return None


def set_refresh_token(value: str) -> bool:
    """
    Stores `value` in the OS keychain. Returns True on success (caller
    should persist KEYCHAIN_SENTINEL in its own DB column instead of the
    real value); False means the keychain isn't usable right now and the
    caller should fall back to storing `value` directly.
    """
    kr = _get_working_keyring()
    if not kr:
        return False
    try:
        kr.set_password(_SERVICE_NAME, _KEY_NAME, value)
        return True
    except Exception as e:
        logger.warning(f"[token_store] Failed to write to OS keychain, falling back: {e}")
        return False


def get_refresh_token() -> str | None:
    """Reads the token back from the OS keychain. None if unavailable or not set."""
    kr = _get_working_keyring()
    if not kr:
        return None
    try:
        return kr.get_password(_SERVICE_NAME, _KEY_NAME)
    except Exception as e:
        logger.warning(f"[token_store] Failed to read from OS keychain: {e}")
        return None


def delete_refresh_token() -> None:
    """Best-effort cleanup on logout — never raises."""
    kr = _get_working_keyring()
    if not kr:
        return
    try:
        kr.delete_password(_SERVICE_NAME, _KEY_NAME)
    except Exception:
        pass
