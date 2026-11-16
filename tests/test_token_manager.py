"""TokenManager: refresh, expiry, rotation and failure classification."""

from __future__ import annotations

import json
import threading
import time
import types

import httpx
import pytest

from pocketcasts_mcp import auth
from tests import fakes

TOKEN = fakes.REFRESH_ENDPOINT
FIRST = "tok-1"
SECOND = "tok-2"
ROTATED = "rt-rotated"
REFRESH_KEY = (auth.SERVICE, auth.KEY_REFRESH)
EMAIL_KEY = (auth.SERVICE, auth.KEY_EMAIL)


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock, patched into auth.py only."""
    now = [1000.0]
    monkeypatch.setattr(
        auth, "time", types.SimpleNamespace(monotonic=lambda: now[0])
    )
    return now


def test_refresh_posts_the_stored_refresh_token(api):
    assert auth.TokenManager().refresh() == FIRST

    (call,) = api.calls_to(TOKEN)
    assert call.body == {
        "grantType": "refresh_token",
        "refreshToken": fakes.STORED_REFRESH,
    }


@pytest.mark.parametrize(
    ("expires_in", "window"),
    [(3600, 3540), (30, 60), (0, 3600)],
    ids=["ttl-minus-60s", "floor-60s", "missing-ttl-1h"],
)
def test_access_token_is_cached_until_the_refresh_window(
    api, clock, expires_in, window
):
    api.expires_in = expires_in
    tokens = auth.TokenManager()

    assert tokens.access_token == FIRST
    clock[0] += window - 1
    assert tokens.access_token == FIRST
    clock[0] += 1
    assert tokens.access_token == SECOND


def test_invalidate_forces_a_refresh(api, clock):
    del clock  # Frozen so only invalidate() can expire the token.
    tokens = auth.TokenManager()
    assert tokens.access_token == FIRST

    tokens.invalidate()

    assert tokens.access_token == SECOND
    assert api.tokens_issued == 2


def test_stale_invalidate_keeps_the_newer_token(api, clock):
    del clock  # Frozen so only invalidate() can expire the token.
    tokens = auth.TokenManager()
    assert tokens.access_token == FIRST
    tokens.invalidate(FIRST)
    assert tokens.access_token == SECOND

    # A second caller that was also refused FIRST reports it late.
    tokens.invalidate(FIRST)

    assert tokens.access_token == SECOND
    assert api.tokens_issued == 2


def test_parallel_callers_share_one_refresh(api, fake_keyring):
    # Pocket Casts rotates refresh tokens and refuses a spent one, so a
    # second concurrent refresh would log the user out.
    spent = set()

    def rotating(request):
        refresh = json.loads(request.content)["refreshToken"]
        if refresh in spent:
            return httpx.Response(400, json={"error": "invalid_grant"})
        spent.add(refresh)
        time.sleep(0.2)  # Hold the race window open.
        api.tokens_issued += 1
        n = api.tokens_issued
        return fakes.token_reply(f"tok-{n}", refresh=f"rt-{n}")

    api.route(TOKEN, rotating)
    tokens = auth.TokenManager()
    start = threading.Barrier(4)
    results, errors = [], []

    def worker():
        start.wait()
        try:
            results.append(tokens.access_token)
        except Exception as e:  # noqa: BLE001 - collected for the assert.
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    assert results == [FIRST] * 4
    assert api.tokens_issued == 1
    assert fake_keyring.entries[REFRESH_KEY] == "rt-1"


def test_unchanged_refresh_token_is_not_rewritten(fake_keyring):
    auth.TokenManager().refresh()

    assert fake_keyring.writes == []


def test_rotated_refresh_token_is_stored(api, fake_keyring):
    api.refresh_reply_token = ROTATED

    auth.TokenManager().refresh()

    assert fake_keyring.entries[REFRESH_KEY] == ROTATED
    assert fake_keyring.entries[EMAIL_KEY] == fakes.EMAIL


def test_missing_refresh_token_means_not_logged_in(api, fake_keyring):
    fake_keyring.entries.clear()

    with pytest.raises(auth.NotLoggedIn) as exc_info:
        auth.TokenManager().refresh()

    assert str(exc_info.value) == auth.AUTH_HINT
    assert api.calls == []


def test_invalid_grant_means_log_in_again(api, fake_keyring):
    api.route(
        TOKEN,
        httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Refresh token revoked",
            },
        ),
    )

    with pytest.raises(auth.NotLoggedIn, match="pocketcasts-mcp-auth"):
        auth.TokenManager().refresh()
    # A rejected token is reported, not deleted: logging in again replaces it.
    assert fake_keyring.entries[REFRESH_KEY] == fakes.STORED_REFRESH
    assert fake_keyring.writes == []


TRANSIENT = [
    httpx.Response(503, text="Service Unavailable"),
    httpx.Response(429, text="Too Many Requests"),
    lambda req: httpx.ConnectError("refused", request=req),
]
TRANSIENT_IDS = ["503", "429", "connect-error"]


@pytest.mark.parametrize("reply", TRANSIENT, ids=TRANSIENT_IDS)
def test_transient_refresh_failure_leaves_the_keychain_alone(
    api, fake_keyring, reply
):
    api.route(TOKEN, reply)

    with pytest.raises(auth.NotLoggedIn):
        auth.TokenManager().refresh()

    assert fake_keyring.entries[REFRESH_KEY] == fakes.STORED_REFRESH
    assert fake_keyring.writes == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "TokenManager.refresh turns every PocketCastsAuthError, including"
        " 5xx/429/network failures, into NotLoggedIn ('log in again')"
    ),
)
@pytest.mark.parametrize("reply", TRANSIENT, ids=TRANSIENT_IDS)
def test_transient_refresh_failure_is_not_reported_as_logged_out(api, reply):
    api.route(TOKEN, reply)

    error = None
    try:
        auth.TokenManager().refresh()
    except Exception as e:  # noqa: BLE001 - the type is what is under test.
        error = e

    assert error is not None
    assert not isinstance(error, auth.NotLoggedIn)
    assert "pocketcasts-mcp-auth" not in str(error)
