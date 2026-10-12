"""PocketCasts._call / _attempt: 401 retry and error classification."""

from __future__ import annotations

import httpx
import pytest
from api_pocketcasts import exceptions as pc_exceptions

from pocketcasts_mcp import client
from tests import fakes

LIST = "/up_next/list"
TOKEN = fakes.REFRESH_ENDPOINT
PAYLOAD = {"version": 2, "model": "webplayer"}
QUEUE = {"serverModified": 1, "episodes": []}


def test_success_sends_fresh_bearer_token(pc, api):
    api.route(LIST, QUEUE)

    assert pc._call(client._post, LIST, PAYLOAD) == QUEUE
    assert api.paths() == [TOKEN, LIST]
    (call,) = api.calls_to(LIST)
    assert call.authorization == "Bearer tok-1"
    assert call.body == PAYLOAD


def test_access_token_is_reused_across_calls(pc, api):
    api.route(LIST, QUEUE)

    pc._call(client._post, LIST, PAYLOAD)
    pc._call(client._post, LIST, PAYLOAD)

    assert api.tokens_issued == 1


def test_401_refreshes_once_and_retries_with_new_token(pc, api):
    api.route(LIST, httpx.Response(401), QUEUE)

    assert pc._call(client._post, LIST, PAYLOAD) == QUEUE
    assert api.paths() == [TOKEN, LIST, TOKEN, LIST]
    assert [c.authorization for c in api.calls_to(LIST)] == [
        "Bearer tok-1",
        "Bearer tok-2",
    ]


def test_401_after_refresh_gives_up_with_log_in_again(pc, api):
    api.route(LIST, httpx.Response(401))

    with pytest.raises(client.PocketCastsError, match="even after a refresh"):
        pc._call(client._post, LIST, PAYLOAD)
    # Exactly one retry: no loop.
    assert api.paths() == [TOKEN, LIST, TOKEN, LIST]


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_429_and_5xx_fail_fast_without_refresh(pc, api, status):
    api.route(LIST, httpx.Response(status, text="x" * 500))

    with pytest.raises(client.PocketCastsError) as exc_info:
        pc._call(client._post, LIST, PAYLOAD)

    message = str(exc_info.value)
    assert message.startswith(f"Pocket Casts HTTP {status}: ")
    assert "x" * 200 in message
    assert "x" * 201 not in message  # body truncated to 200 chars
    assert "pocketcasts-mcp-auth" not in message
    assert api.paths() == [TOKEN, LIST]


@pytest.mark.parametrize(
    "error",
    [
        lambda req: httpx.ConnectError("refused", request=req),
        lambda req: httpx.ReadTimeout("timed out", request=req),
    ],
    ids=["connect-error", "read-timeout"],
)
def test_network_error_is_reported_without_retry(pc, api, error):
    api.route(LIST, error)

    with pytest.raises(client.PocketCastsError, match="^Network error"):
        pc._call(client._post, LIST, PAYLOAD)
    assert api.paths() == [TOKEN, LIST]


def test_non_json_reply_is_a_readable_error(pc, api):
    api.route(LIST, httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(client.PocketCastsError, match="Non-JSON reply"):
        pc._call(client._post, LIST, PAYLOAD)


def test_non_object_reply_is_a_readable_error(pc, api):
    api.route(LIST, [1, 2, 3])

    with pytest.raises(client.PocketCastsError, match="Unexpected reply"):
        pc._call(client._post, LIST, PAYLOAD)


def test_no_stored_session_fails_before_any_request(pc, api, fake_keyring):
    fake_keyring.entries.clear()
    called = []

    with pytest.raises(client.PocketCastsError, match="No Pocket Casts"):
        pc._call(called.append)
    assert called == []
    assert api.calls == []


@pytest.mark.parametrize(
    "auth_error",
    [
        pc_exceptions.PocketCastsAuthError("expired"),
        pc_exceptions.PocketCastsTokenExpiredError(),
        pc_exceptions.PocketCastsAPIError(
            "http", "wrapped", details={"status_code": 401}
        ),
        pc_exceptions.PocketCastsAPIResponseError(
            "podcast/full wraps 401", details={"status_code": 401}
        ),
    ],
    ids=["auth-error", "token-expired", "api-error-401", "response-error-401"],
)
def test_library_auth_failures_trigger_one_refresh(pc, api, auth_error):
    tokens = []

    def fn(token):
        tokens.append(token)
        if len(tokens) == 1:
            raise auth_error
        return "ok"

    assert pc._call(fn) == "ok"
    assert tokens == ["tok-1", "tok-2"]


def test_library_api_error_other_than_401_is_not_retried(pc):
    tokens = []

    def fn(token):
        tokens.append(token)
        raise pc_exceptions.PocketCastsAPIError(
            "timeout_error", "Timeout", details={"exception": "slow"}
        )

    with pytest.raises(client.PocketCastsError, match="Pocket Casts API error"):
        pc._call(fn)
    assert tokens == ["tok-1"]


def test_bare_401_from_library_endpoint_is_retried(pc, api):
    # get_user_episode lets httpx.HTTPStatusError escape on 401.
    api.route(
        "/user/episode",
        httpx.Response(401),
        fakes.user_episode_reply("ep-1"),
    )

    payload = pc._episode_payload("ep-1")

    assert payload["uuid"] == "ep-1"
    assert api.tokens_issued == 2


def test_podcast_list_401_is_refreshed_and_retried(pc, api):
    api.route(
        "/user/podcast/list",
        httpx.Response(401),
        fakes.podcast_list_reply([("pod-a", "Podcast A")]),
    )

    assert pc.podcast_titles() == {"pod-a": "Podcast A"}
    assert api.tokens_issued == 2


@pytest.mark.xfail(
    strict=True,
    reason=(
        "library wraps 429/5xx from /user/podcast/list as PocketCastsAuthError;"
        " _attempt treats it as a 401, refreshes needlessly and says log in"
        " again"
    ),
)
@pytest.mark.parametrize("status", [429, 503])
def test_podcast_list_outage_is_not_reported_as_log_in_again(pc, api, status):
    api.route("/user/podcast/list", httpx.Response(status, text="busy"))

    with pytest.raises(client.PocketCastsError) as exc_info:
        pc.subscriptions()

    assert "pocketcasts-mcp-auth" not in str(exc_info.value)
    assert api.tokens_issued == 1
