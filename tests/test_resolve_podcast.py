"""PocketCasts.resolve_podcast: uuid, exact, substring and fuzzy lookup."""

from __future__ import annotations

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


@pytest.mark.xfail(
    strict=True,
    reason="podcast-title cache is never refreshed on a miss",
)
def test_new_subscription_resolves_after_cache_was_filled(pc, api):
    api.route(
        PODCAST_LIST,
        fakes.podcast_list_reply([DAILY]),
        fakes.podcast_list_reply([DAILY, HISTORY]),
    )
    pc.resolve_podcast("the daily")

    assert pc.resolve_podcast("Hardcore History") == HISTORY
