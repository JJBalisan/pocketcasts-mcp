"""Boundary checks for /user/token failure classification (issue #17).

Adapted from the sweep's s3 solution. These also guard the reliance on
api-pocketcasts raising its PocketCastsAuthError inside its handler for
httpx.HTTPStatusError: if an upgrade changes that, the 400/401 cases stop
meaning "log in again" and fail here.
"""

from __future__ import annotations

import httpx
import pytest

from pocketcasts_mcp import auth, client
from tests import fakes

TOKEN = fakes.REFRESH_ENDPOINT


@pytest.mark.parametrize("status", [400, 401])
def test_refusal_statuses_mean_log_in_again(api, fake_keyring, status):
    api.route(TOKEN, httpx.Response(status, json={}))

    with pytest.raises(auth.NotLoggedIn, match="pocketcasts-mcp-auth"):
        auth.TokenManager().refresh()
    assert fake_keyring.writes == []


@pytest.mark.parametrize("status", [403, 404, 408, 418, 500, 504])
def test_other_statuses_are_temporary(api, fake_keyring, status):
    api.route(TOKEN, httpx.Response(status, json={"error": "invalid_grant"}))

    with pytest.raises(auth.RefreshUnavailableError) as exc_info:
        auth.TokenManager().refresh()
    assert f"HTTP {status}" in str(exc_info.value)
    assert "pocketcasts-mcp-auth" not in str(exc_info.value)
    assert fake_keyring.writes == []


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={}),
        httpx.Response(200, json=["x"]),
        httpx.Response(200, json=None),
    ],
    ids=["not-json", "empty-object", "list", "null"],
)
def test_malformed_success_reply_is_temporary(api, fake_keyring, reply):
    api.route(TOKEN, reply)

    with pytest.raises(auth.RefreshUnavailableError):
        auth.TokenManager().refresh()
    assert fake_keyring.writes == []


def test_tool_call_reports_unavailable_with_the_status(pc, api):
    api.route(TOKEN, httpx.Response(500, json={}))

    with pytest.raises(client.PocketCastsError, match="HTTP 500"):
        pc._call(client._post, "/up_next/list", {"version": 2})
