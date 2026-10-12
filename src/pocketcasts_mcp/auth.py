"""Credential handling for the Pocket Casts MCP server.

Only the refresh token and the account email are persisted, in the macOS
Keychain via `keyring` (service "pocketcasts-mcp"). The password is used once,
in the interactive `pocketcasts-mcp-auth` command, and never stored. The
access token lives in memory only and is re-derived from the refresh token.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import time

import keyring
from api_pocketcasts import exceptions as pc_exceptions
from api_pocketcasts.endpoints import auth as pc_auth

SERVICE = "pocketcasts-mcp"
KEY_REFRESH = "refresh_token"
KEY_EMAIL = "email"
AUTH_HINT = (
    "No Pocket Casts session in the keychain. Run `pocketcasts-mcp-auth` "
    "from a terminal to log in."
)

log = logging.getLogger(__name__)


class NotLoggedIn(RuntimeError):
    pass


def stored_refresh_token() -> str | None:
    return keyring.get_password(SERVICE, KEY_REFRESH)


def stored_email() -> str | None:
    return keyring.get_password(SERVICE, KEY_EMAIL)


def store_session(email: str, refresh: str) -> None:
    keyring.set_password(SERVICE, KEY_EMAIL, email)
    keyring.set_password(SERVICE, KEY_REFRESH, refresh)


def forget_session() -> None:
    for key in (KEY_EMAIL, KEY_REFRESH):
        try:
            keyring.delete_password(SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass


class TokenManager:
    """Holds the access token in memory and refreshes it on demand."""

    def __init__(self) -> None:
        self._access: str | None = None
        # Monotonic seconds; refresh proactively past this.
        self._expires_at: float = 0.0

    @property
    def access_token(self) -> str:
        if self._access is None or time.monotonic() >= self._expires_at:
            self.refresh()
        assert self._access is not None
        return self._access

    def refresh(self) -> str:
        stored = stored_refresh_token()
        if not stored:
            raise NotLoggedIn(AUTH_HINT)
        try:
            user = pc_auth.refresh_token(stored)
        except pc_exceptions.PocketCastsAuthError as e:
            raise NotLoggedIn(
                f"Pocket Casts refused the stored refresh token ({e}). "
                "Run `pocketcasts-mcp-auth` again."
            ) from e
        self._access = user.access_token
        ttl = int(user.expires_in or 0)
        self._expires_at = (
            time.monotonic() + max(ttl - 60, 60)
            if ttl
            else time.monotonic() + 3600
        )
        # Pocket Casts may rotate the refresh token; keep the newest one.
        if user.refresh_token and user.refresh_token != stored:
            store_session(
                user.email or stored_email() or "", user.refresh_token
            )
            log.info("stored rotated refresh token")
        return self._access

    def invalidate(self) -> None:
        self._access = None
        self._expires_at = 0.0


def _login_interactive() -> int:
    if not sys.stdin.isatty():
        print(
            "Run this from a real terminal; it prompts for your password.",
            file=sys.stderr,
        )
        return 2
    email = input("Pocket Casts email: ").strip()
    password = getpass.getpass("Pocket Casts password (not stored): ")
    try:
        user = pc_auth.login_pocket_casts(email, password)
    except pc_exceptions.PocketCastsAuthError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    finally:
        del password
    if not user.refresh_token:
        print(
            "Login succeeded but no refresh token was returned; cannot continue.",
            file=sys.stderr,
        )
        return 1
    store_session(user.email or email, user.refresh_token)
    print(
        f"Logged in as {user.email or email}. Refresh token saved to the keychain (service {SERVICE!r})."
    )
    return 0


def _check() -> int:
    email = stored_email()
    if not stored_refresh_token():
        print(AUTH_HINT, file=sys.stderr)
        return 1
    try:
        TokenManager().refresh()
    except NotLoggedIn as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"OK: refreshed an access token for {email or 'unknown account'}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pocketcasts-mcp-auth",
        description="Log in to Pocket Casts once and keep only the refresh token in the keychain.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="verify the stored session still works",
    )
    group.add_argument(
        "--logout", action="store_true", help="delete the stored session"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    if args.check:
        return _check()
    if args.logout:
        forget_session()
        print("Stored Pocket Casts session removed.")
        return 0
    return _login_interactive()


if __name__ == "__main__":
    sys.exit(main())
