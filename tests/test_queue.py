"""Up Next reads (_queue_rows, up_next) and the move_to_front emulation."""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest

from pocketcasts_mcp import client
from tests import fakes

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "up_next_list.json"
PODCAST_LIST = "/user/podcast/list"
UP_NEXT_PATHS = ("/up_next/list", "/up_next/remove", "/up_next/play_next")
SUBSCRIBED = [("pod-aaaa", "Podcast A"), ("pod-bbbb", "Podcast B")]


@pytest.fixture
def up_next_reply():
    """The hand-written /up_next/list reply (version 2, webplayer model)."""
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def subscribed(api):
    api.route(PODCAST_LIST, fakes.podcast_list_reply(SUBSCRIBED))


def writes(api):
    """Returns the queue-touching requests, skipping token and lookups."""
    return [c.path for c in api.calls if c.path in UP_NEXT_PATHS[1:]]


# -- _queue_rows / up_next ---------------------------------------------------


EXPECTED_ROWS = [
    {
        "position": 1,
        "uuid": "ep-0001-playing",
        "title": "Fixture Episode One",
        "podcast_uuid": "pod-aaaa",
        "podcast_title": "Podcast A",
        "published": "2026-09-01T08:00:00Z",
        "played_up_to_seconds": 754,
        "duration_seconds": 3120,
    },
    {
        "position": 2,
        "uuid": "ep-0002",
        "title": "Fixture Episode Two",
        "podcast_uuid": "pod-bbbb",
        "podcast_title": "Podcast B",
        "published": "2026-09-03T05:30:00Z",
        "played_up_to_seconds": 0,
        "duration_seconds": 1800,
    },
    {
        "position": 3,
        "uuid": "ep-0003",
        "title": "Fixture Episode Three",
        "podcast_uuid": "pod-not-subscribed",
        "podcast_title": None,
        "published": "2026-08-15T12:00:00Z",
        "played_up_to_seconds": None,
        "duration_seconds": None,
    },
]


@pytest.mark.usefixtures("subscribed")
def test_queue_rows_from_recorded_shape(pc, up_next_reply):
    assert pc._queue_rows(up_next_reply) == EXPECTED_ROWS


@pytest.mark.usefixtures("subscribed")
def test_queue_rows_skip_junk_and_tolerate_missing_sync(pc, up_next_reply):
    reply = {
        "episodes": [None, "junk", *up_next_reply["episodes"], 7],
        "episodeSync": None,
    }

    rows = pc._queue_rows(reply)

    assert [r["uuid"] for r in rows] == [
        "ep-0001-playing",
        "ep-0002",
        "ep-0003",
    ]
    assert {r["played_up_to_seconds"] for r in rows} == {None}
    assert {r["duration_seconds"] for r in rows} == {None}


@pytest.mark.usefixtures("subscribed")
@pytest.mark.parametrize("episodes", [None, []])
def test_queue_rows_empty_queue(pc, episodes):
    assert pc._queue_rows({"episodes": episodes}) == []


@pytest.mark.usefixtures("subscribed")
def test_up_next_posts_webplayer_request(pc, api, up_next_reply):
    api.route("/up_next/list", up_next_reply)

    result = pc.up_next()

    assert result == {
        "count": 3,
        "server_modified": 1760000000123,
        "episodes": EXPECTED_ROWS,
    }
    (call,) = api.calls_to("/up_next/list")
    assert call.body == {"version": 2, "model": "webplayer"}
    assert call.authorization == "Bearer tok-1"


@pytest.mark.usefixtures("subscribed")
def test_up_next_normalises_the_legacy_order_shape(pc, api, up_next_reply):
    # Legacy replies key episodes by uuid and omit uuid from each value.
    by_uuid = {
        e["uuid"]: {k: v for k, v in e.items() if k != "uuid"}
        for e in up_next_reply["episodes"]
    }
    order = ["ep-0002", "ep-0001-playing", "ep-0003"]
    api.route(
        "/up_next/list",
        {"serverModified": 5, "order": order, "episodes": by_uuid},
    )

    result = pc.up_next()

    assert [r["uuid"] for r in result["episodes"]] == order
    assert [r["position"] for r in result["episodes"]] == [1, 2, 3]
    assert result["episodes"][0]["podcast_title"] == "Podcast B"
    assert result["server_modified"] == 5


# -- move_to_front -----------------------------------------------------------


@pytest.fixture
def queue(api):
    """A four-episode queue; ep-1 is playing."""
    up_next = fakes.FakeUpNext(
        [fakes.queue_entry(f"ep-{i}") for i in range(1, 5)]
    )
    up_next.install(api)
    api.route(PODCAST_LIST, fakes.podcast_list_reply(SUBSCRIBED))
    return up_next


def lookup(api, uuid):
    api.route("/user/episode", fakes.user_episode_reply(uuid))


def test_move_to_front_moves_a_queued_episode_to_position_2(pc, api, queue):
    lookup(api, "ep-4")

    result = pc.move_to_front("ep-4")

    assert queue.uuids() == ["ep-1", "ep-4", "ep-2", "ep-3"]
    assert [r["uuid"] for r in result["episodes"]] == queue.uuids()
    assert result["moved_to_front"] == "Title ep-4"
    assert writes(api) == ["/up_next/remove", "/up_next/play_next"]
    (remove,) = api.calls_to("/up_next/remove")
    assert remove.body == {"version": 2, "uuids": ["ep-4"]}
    (play_next,) = api.calls_to("/up_next/play_next")
    assert play_next.body == {
        "version": 2,
        "episode": fakes.queue_entry("ep-4"),
    }


def test_move_to_front_of_absent_episode_adds_it_at_position_2(pc, api, queue):
    # Pins current behaviour. Whether an unqueued uuid should be refused
    # instead is an open question in the 5 Oct audit, not a known defect.
    lookup(api, "ep-new")

    result = pc.move_to_front("ep-new")

    assert queue.uuids() == ["ep-1", "ep-new", "ep-2", "ep-3", "ep-4"]
    assert result["count"] == 5
    assert writes(api) == ["/up_next/remove", "/up_next/play_next"]


def test_move_to_front_ignores_a_failed_remove(pc, api, queue):
    lookup(api, "ep-new")
    api.route("/up_next/remove", httpx.Response(400, text="not queued"))

    result = pc.move_to_front("ep-new")

    assert result["episodes"][1]["uuid"] == "ep-new"
    assert writes(api) == ["/up_next/remove", "/up_next/play_next"]


def test_move_to_front_of_playing_episode_demotes_it(pc, api, queue):
    # Pins current behaviour: remove + play_next takes the playing episode
    # out of position 1, so ep-2 becomes the playing episode. Flagged in the
    # report as a semantics question for JJ rather than xfailed.
    lookup(api, "ep-1")

    pc.move_to_front("ep-1")

    assert queue.uuids() == ["ep-2", "ep-1", "ep-3", "ep-4"]


def test_move_to_front_unknown_episode_touches_nothing(pc, api, queue):
    api.route("/user/episode", httpx.Response(404, text=""))

    with pytest.raises(client.PocketCastsError, match="not found"):
        pc.move_to_front("ep-missing")
    assert writes(api) == []
    assert queue.uuids() == ["ep-1", "ep-2", "ep-3", "ep-4"]


def test_move_to_front_refuses_episode_missing_fields(pc, api, queue):
    reply = fakes.user_episode_reply("ep-3")
    reply["title"] = ""
    api.route("/user/episode", reply)

    with pytest.raises(client.PocketCastsError, match="missing title"):
        pc.move_to_front("ep-3")
    assert writes(api) == []
