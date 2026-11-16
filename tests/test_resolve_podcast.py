"""PocketCasts.resolve_podcast: uuid, exact, substring and fuzzy lookup."""

from __future__ import annotations

import types

import httpx
import pytest

from pocketcasts_mcp import client
from tests import fakes

PODCAST_LIST = "/user/podcast/list"

DAILY = ("pod-daily", "The Daily")
DAILY_SHOW = ("pod-daily-show", "The Daily Show")
TECH = ("pod-tech", "Daily Tech News")
HISTORY = ("pod-history", "Hardcore History")


def subscribe(api, *podcasts):
    api.route(PODCAST_LIST, fakes.podcast_list_reply(list(podcasts)))


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock, patched into client.py only."""
    now = [1000.0]
    monkeypatch.setattr(
        client, "time", types.SimpleNamespace(monotonic=lambda: now[0])
    )
    return now


def expire_titles(clock):
    """Moves the clock past the title cache's lifetime."""
    clock[0] += client.TITLES_TTL_SECONDS


def test_uuid_is_returned_as_is(pc, api):
    subscribe(api, DAILY, DAILY_SHOW)

    assert pc.resolve_podcast("pod-daily-show") == DAILY_SHOW


@pytest.mark.parametrize(
    "order",
    [(DAILY, DAILY_SHOW), (DAILY_SHOW, DAILY)],
    ids=["exact-first", "superset-first"],
)
@pytest.mark.parametrize("query", ["The Daily", "the daily", "  THE DAILY "])
def test_exact_title_beats_a_longer_title_containing_it(pc, api, order, query):
    subscribe(api, *order)

    assert pc.resolve_podcast(query) == DAILY


def test_single_substring_hit_resolves(pc, api):
    subscribe(api, DAILY, HISTORY)

    assert pc.resolve_podcast("hardcore") == HISTORY


def test_close_misspelling_resolves(pc, api):
    subscribe(api, DAILY, HISTORY)

    assert pc.resolve_podcast("Hardcore Histroy") == HISTORY


def test_unrelated_query_is_refused(pc, api):
    subscribe(api, DAILY, HISTORY)

    with pytest.raises(client.PocketCastsError, match="No subscribed podcast"):
        pc.resolve_podcast("Radiolab")


def test_titles_are_fetched_once(pc, api):
    subscribe(api, DAILY, HISTORY)

    pc.resolve_podcast("hardcore")
    pc.resolve_podcast("the daily")

    assert len(api.calls_to(PODCAST_LIST)) == 1


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ambiguous substring query silently resolves to the first"
        " subscription in list order instead of refusing"
    ),
)
@pytest.mark.parametrize(
    "order",
    [(DAILY_SHOW, TECH), (TECH, DAILY_SHOW)],
    ids=["show-first", "tech-first"],
)
def test_ambiguous_substring_is_refused(pc, api, order):
    subscribe(api, *order)

    with pytest.raises(client.PocketCastsError):
        pc.resolve_podcast("daily")


def test_new_subscription_resolves_after_cache_was_filled(pc, api, clock):
    api.route(
        PODCAST_LIST,
        fakes.podcast_list_reply([DAILY]),
        fakes.podcast_list_reply([DAILY, HISTORY]),
    )
    pc.resolve_podcast("the daily")
    expire_titles(clock)

    assert pc.resolve_podcast("Hardcore History") == HISTORY


def test_new_subscription_beats_a_near_match_in_a_stale_cache(pc, api, clock):
    # "The Daily Beast" scores 0.75 against the cached "The Daily", which
    # used to be accepted as the requested show.
    beast = ("pod-beast", "The Daily Beast")
    api.route(
        PODCAST_LIST,
        fakes.podcast_list_reply([DAILY]),
        fakes.podcast_list_reply([DAILY, beast]),
    )
    pc.resolve_podcast("the daily")
    expire_titles(clock)

    assert pc.resolve_podcast("The Daily Beast") == beast
    assert pc.resolve_podcast("pod-beast") == beast
    assert len(api.calls_to(PODCAST_LIST)) == 2


def test_new_subscription_uuid_resolves_after_cache_was_filled(pc, api, clock):
    api.route(
        PODCAST_LIST,
        fakes.podcast_list_reply([DAILY]),
        fakes.podcast_list_reply([DAILY, HISTORY]),
    )
    pc.resolve_podcast("the daily")
    expire_titles(clock)

    assert pc.resolve_podcast("pod-history") == HISTORY


def test_exact_hit_in_a_warm_cache_does_not_refetch(pc, api):
    subscribe(api, DAILY, HISTORY)
    pc.resolve_podcast("hardcore")

    pc.resolve_podcast("pod-daily")
    pc.resolve_podcast("The Daily")

    assert len(api.calls_to(PODCAST_LIST)) == 1


def test_fuzzy_lookup_within_ttl_does_not_refetch(pc, api, clock):
    api.route(
        PODCAST_LIST,
        fakes.podcast_list_reply([DAILY]),
        fakes.podcast_list_reply([DAILY, HISTORY]),
    )
    pc.resolve_podcast("the daily")
    clock[0] += client.TITLES_TTL_SECONDS - 1

    # A fresh cache is trusted: no refetch, so the new show is not seen yet.
    with pytest.raises(client.PocketCastsError, match="No subscribed podcast"):
        pc.resolve_podcast("Hardcore History")
    assert pc.resolve_podcast("the dialy") == DAILY
    assert len(api.calls_to(PODCAST_LIST)) == 1

    clock[0] += 1
    assert pc.resolve_podcast("Hardcore History") == HISTORY
    assert len(api.calls_to(PODCAST_LIST)) == 2


def test_failed_refetch_falls_back_to_a_good_stale_match(pc, api, clock):
    api.route(
        PODCAST_LIST,
        fakes.podcast_list_reply([DAILY, HISTORY]),
        httpx.Response(503, text="unavailable"),
    )
    pc.resolve_podcast("the daily")
    expire_titles(clock)

    assert pc.resolve_podcast("Hardcore Histroy") == HISTORY
    assert len(api.calls_to(PODCAST_LIST)) > 1


def test_failed_refetch_keeps_the_warm_cache(pc, api, clock):
    # Regression: the cache used to be cleared before the refetch, so an
    # outage left later queue reads with no titles to fall back on.
    api.route(
        PODCAST_LIST,
        fakes.podcast_list_reply([DAILY, HISTORY]),
        httpx.Response(503, text="unavailable"),
    )
    pc.resolve_podcast("the daily")
    expire_titles(clock)
    with pytest.raises(client.PocketCastsError):
        pc.resolve_podcast("Radiolab")
    fetches = len(api.calls_to(PODCAST_LIST))

    assert pc.podcast_titles() == dict([DAILY, HISTORY])
    assert len(api.calls_to(PODCAST_LIST)) == fetches


def test_failed_refetch_with_a_weak_stale_match_reraises(pc, api, clock):
    api.route(
        PODCAST_LIST,
        fakes.podcast_list_reply([DAILY, HISTORY]),
        httpx.Response(503, text="unavailable"),
    )
    pc.resolve_podcast("the daily")
    expire_titles(clock)

    with pytest.raises(client.PocketCastsError) as exc_info:
        pc.resolve_podcast("Radiolab")
    assert "No subscribed podcast" not in str(exc_info.value)
