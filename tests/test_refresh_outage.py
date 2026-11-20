"""Issue #17: a transient /user/token failure is not a refused session.

Only a 400 or 401 from /user/token means Pocket Casts refused the refresh
token. A 5xx, a 429 or a network error is an outage: the user must not be
told to log in again, because doing so does not help. The library hides the
HTTP status (it raises PocketCastsAuthError without `from`, and `details`
carries the body only), so the JSON-bodied replies below look like the 401
`json={}` refusal to anything that inspects `details` or the message.

Refusals are covered elsewhere and must keep failing as "log in again":
test_token_manager.py::test_invalid_grant_means_log_in_again (400) and
test_auth_cli.py::test_check_with_rejected_token (401).
"""

from __future__ import annotations

import httpx
import pytest

from pocketcasts_mcp import auth, client
from tests import fakes

TOKEN = fakes.REFRESH_ENDPOINT
LIST = "/up_next/list"
PAYLOAD = {"version": 2, "model": "webplayer"}
LOGIN_HINT = "pocketcasts-mcp-auth"
REFUSED = "refused the stored refresh token"
RECOVERED = "tok-recovered"

OUTAGES = [
    httpx.Response(503, text="Service Unavailable"),
    httpx.Response(503, json={"error": "server_error"}),
    httpx.Response(500, json={}),
    httpx.Response(502, text="<html>Bad Gateway</html>"),
    httpx.Response(429, text="Too Many Requests"),
    httpx.Response(429, json={"error": "rate_limited"}),
    lambda req: httpx.ConnectError("connection failed", request=req),
    lambda req: httpx.ReadTimeout("timed out", request=req),
]
OUTAGE_IDS = [
    "503-text",
    "503-json",
    "500-empty-json",
    "502-html",
    "429-text",
    "429-json",
    "connect-error",
    "read-timeout",
]


def _says_log_in_again(message: str) -> bool:
    return LOGIN_HINT in message or REFUSED in message


@pytest.mark.parametrize("reply", OUTAGES, ids=OUTAGE_IDS)
def test_token_manager_outage_is_not_not_logged_in(api, fake_keyring, reply):
    api.route(TOKEN, reply)

    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - type is tested.
        auth.TokenManager().refresh()

    assert not isinstance(exc_info.value, auth.NotLoggedIn)
    assert not _says_log_in_again(str(exc_info.value))
    assert fake_keyring.entries[(auth.SERVICE, auth.KEY_REFRESH)] == (
        fakes.STORED_REFRESH
    )
    assert fake_keyring.writes == []


@pytest.mark.parametrize("reply", OUTAGES, ids=OUTAGE_IDS)
def test_tool_call_during_outage_does_not_say_log_in_again(pc, api, reply):
    api.route(TOKEN, reply)
    api.route(LIST, {"serverModified": 1, "episodes": []})

    with pytest.raises(client.PocketCastsError) as exc_info:
        pc._call(client._post, LIST, PAYLOAD)

    assert not _says_log_in_again(str(exc_info.value))
    # The refresh failed, so the tool itself was never reached.
    assert api.paths() == [TOKEN]


@pytest.mark.parametrize("reply", OUTAGES, ids=OUTAGE_IDS)
def test_check_during_outage_fails_without_log_in_again(api, capsys, reply):
    api.route(TOKEN, reply)

    assert auth.main(["--check"]) != 0

    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip()  # Says why, on stderr, with no traceback.
    assert not _says_log_in_again(out.err)


def test_outage_then_recovery_refreshes_on_next_use(api):
    api.route(
        TOKEN, httpx.Response(503, text="busy"), fakes.token_reply(RECOVERED)
    )
    tokens = auth.TokenManager()

    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - type is tested.
        _ = tokens.access_token
    assert not isinstance(exc_info.value, auth.NotLoggedIn)

    assert tokens.access_token == RECOVERED
    assert len(api.calls_to(TOKEN)) == 2
