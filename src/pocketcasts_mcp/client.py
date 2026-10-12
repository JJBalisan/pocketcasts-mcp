"""Pocket Casts client used by the MCP server.

Thin wrapper over api-pocketcasts that adds token refresh, retry on 401,
podcast-title lookup, and plain-dict results for the MCP layer.
"""

from __future__ import annotations

import difflib
import logging
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
from api_pocketcasts import exceptions as pc_exceptions
from api_pocketcasts.endpoints import (
    podcasts as pc_podcasts,
    user_episodes as pc_user_episodes,
)

from pocketcasts_mcp import auth

log = logging.getLogger(__name__)
T = TypeVar("T")

API = "https://api.pocketcasts.com"
# The web player's request shape; returns the ordered list format.
UP_NEXT_LIST_PAYLOAD = {"version": 2, "model": "webplayer"}
QUEUE_FIELDS = ("uuid", "title", "url", "podcast", "published")


def _post(token: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to api.pocketcasts.com and return the JSON body as a plain dict.

    Used for the up_next endpoints instead of the library's typed wrappers,
    which reject fields Pocket Casts has since added (e.g. alternateEnclosures).
    Raises httpx.HTTPStatusError on 401 so _attempt can refresh and retry.
    """
    r = httpx.post(
        f"{API}{path}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError as e:
        raise PocketCastsError(
            f"Non-JSON reply from {path}: {r.text[:200]}"
        ) from e
    if not isinstance(data, dict):
        raise PocketCastsError(
            f"Unexpected reply shape from {path}: {str(data)[:200]}"
        )
    return data


class PocketCastsError(RuntimeError):
    """Readable error for the MCP layer."""


class _AuthFailure(Exception):
    """Internal: the access token was rejected; caller may refresh and retry."""


class PocketCasts:
    """Reads and edits one account's Up Next queue and subscriptions.

    Every call goes through _call, which supplies a fresh access token and
    retries once after a refresh when Pocket Casts rejects it. Errors reach
    the caller as PocketCastsError with a readable message.
    """

    def __init__(self) -> None:
        self._tokens = auth.TokenManager()
        self._podcast_titles: dict[str, str] | None = None

    # -- plumbing -----------------------------------------------------------

    def _call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Call fn(access_token, ...) and retry once after refreshing on 401.

        The library is inconsistent: the up_next endpoints raise
        PocketCastsAuthError on 401, but user/episode and the user lists raise
        a bare httpx error, and podcast/full wraps everything as a response
        error. Normalise all of them.
        """
        try:
            return self._attempt(fn, *args, **kwargs)
        except _AuthFailure as first:
            log.info("access token rejected (%s); refreshing once", first)
            self._tokens.invalidate()
            try:
                return self._attempt(fn, *args, **kwargs)
            except _AuthFailure as e:
                raise PocketCastsError(
                    "Pocket Casts rejected the session even after a refresh "
                    f"({e}). Run `pocketcasts-mcp-auth` to log in again."
                ) from e

    def _attempt(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        try:
            token = self._tokens.access_token
        except auth.NotLoggedIn as e:
            raise PocketCastsError(str(e)) from e
        try:
            return fn(token, *args, **kwargs)
        except pc_exceptions.PocketCastsAuthError as e:
            raise _AuthFailure(str(e)) from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise _AuthFailure("HTTP 401") from e
            raise PocketCastsError(
                f"Pocket Casts HTTP {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.HTTPError as e:
            raise PocketCastsError(
                f"Network error talking to Pocket Casts: {e}"
            ) from e
        except pc_exceptions.PocketCastsAPIError as e:
            details = getattr(e, "details", None) or {}
            if details.get("status_code") == 401:
                raise _AuthFailure(str(e)) from e
            raise PocketCastsError(f"Pocket Casts API error: {e}") from e

    def podcast_titles(self, refresh: bool = False) -> dict[str, str]:
        """Returns subscribed podcast titles by uuid, cached after first use.

        Args:
            refresh: Fetch the subscription list again even if cached.

        Returns:
            A dict mapping podcast uuid to title.
        """
        if self._podcast_titles is None or refresh:
            listing = self._call(pc_podcasts.get_subscribed_podcasts)
            self._podcast_titles = {p.uuid: p.title for p in listing.podcasts}
        return self._podcast_titles

    # -- reads --------------------------------------------------------------

    def subscriptions(
        self, query: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """Lists subscribed podcasts, newest release first.

        Also refreshes the podcast-title cache.

        Args:
            query: Case-insensitive substring of the title or author; None
                or empty keeps every podcast.
            limit: Maximum number of rows returned.

        Returns:
            A dict with total_matching, returned and the podcasts rows.
        """
        listing = self._call(pc_podcasts.get_subscribed_podcasts)
        self._podcast_titles = {p.uuid: p.title for p in listing.podcasts}
        rows = [
            {
                "uuid": p.uuid,
                "title": p.title,
                "author": p.author,
                "last_episode_published": p.lastEpisodePublished,
            }
            for p in listing.podcasts
        ]
        if query:
            q = query.lower()
            rows = [
                r
                for r in rows
                if q in (r["title"] or "").lower()
                or q in (r["author"] or "").lower()
            ]
        rows.sort(key=lambda r: r["last_episode_published"] or "", reverse=True)
        total = len(rows)
        rows = rows[:limit]
        return {
            "total_matching": total,
            "returned": len(rows),
            "podcasts": rows,
        }

    def resolve_podcast(self, podcast: str) -> tuple[str, str]:
        """Accept a podcast uuid or a (fuzzy) title; return (uuid, title).

        Args:
            podcast: A subscribed podcast's uuid, or its title or part of it.

        Returns:
            The podcast's (uuid, title).

        Raises:
            PocketCastsError: No subscribed podcast matches well enough.
        """
        titles = self.podcast_titles()
        if podcast in titles:
            return podcast, titles[podcast]
        needle = podcast.lower().strip()
        best: tuple[float, str, str] | None = None
        for uuid, title in titles.items():
            t = (title or "").lower()
            score = (
                1.0
                if needle == t
                else (
                    0.95
                    if needle in t
                    else difflib.SequenceMatcher(None, needle, t).ratio()
                )
            )
            if best is None or score > best[0]:
                best = (score, uuid, title)
        if best is None or best[0] < 0.6:
            raise PocketCastsError(
                f"No subscribed podcast matches {podcast!r}. Use "
                "list_subscriptions to see titles, or pass the podcast uuid."
            )
        return best[1], best[2]

    def episodes(self, podcast: str, limit: int = 20) -> dict[str, Any]:
        """Lists a subscribed podcast's episodes, newest first.

        Args:
            podcast: Podcast uuid or title, resolved by resolve_podcast.
            limit: Maximum number of episodes returned.

        Returns:
            A dict with the podcast's uuid, title, episode_count and the
            episodes rows.
        """
        uuid, title = self.resolve_podcast(podcast)
        full = self._call(
            lambda tok, u: pc_podcasts.get_podcast_full(u, access_token=tok),
            uuid,
        )
        eps = full.episodes or []
        eps = sorted(eps, key=lambda e: e.get("published") or "", reverse=True)[
            :limit
        ]
        return {
            "podcast_uuid": uuid,
            "podcast_title": title,
            "episode_count": full.episode_count,
            "episodes": [
                {
                    "uuid": e.get("uuid"),
                    "title": e.get("title"),
                    "published": e.get("published"),
                    "duration_seconds": e.get("duration"),
                    "type": e.get("type"),
                }
                for e in eps
            ],
        }

    def _user_list(self, endpoint: str, limit: int) -> list[dict[str, Any]]:
        listing = self._call(pc_user_episodes.fetch_user_episode_list, endpoint)
        return [
            {
                "uuid": e.uuid,
                "title": e.title,
                "podcast_uuid": e.podcast_uuid,
                "podcast_title": e.podcast_title,
                "published": e.published,
                "duration_seconds": e.duration,
                "played_up_to_seconds": e.played_up_to,
            }
            for e in listing.episodes[:limit]
        ]

    def new_releases(self, limit: int = 20) -> list[dict[str, Any]]:
        """Lists episodes in the New Releases filter.

        Args:
            limit: Maximum number of episodes returned.

        Returns:
            One row per episode.
        """
        return self._user_list("/user/new_releases", limit)

    def in_progress(self, limit: int = 20) -> list[dict[str, Any]]:
        """Lists partly played episodes.

        Args:
            limit: Maximum number of episodes returned.

        Returns:
            One row per episode.
        """
        return self._user_list("/user/in_progress", limit)

    def _queue_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        titles = self.podcast_titles()
        sync = {
            str(x.get("uuid")): x
            for x in data.get("episodeSync") or []
            if isinstance(x, dict)
        }
        rows = []
        for i, ep in enumerate(data.get("episodes") or [], start=1):
            if not isinstance(ep, dict):
                continue
            s = sync.get(str(ep.get("uuid")), {})
            rows.append(
                {
                    "position": i,
                    "uuid": ep.get("uuid"),
                    "title": ep.get("title"),
                    "podcast_uuid": ep.get("podcast"),
                    "podcast_title": titles.get(str(ep.get("podcast"))),
                    "published": ep.get("published"),
                    "played_up_to_seconds": s.get("playedUpTo"),
                    "duration_seconds": s.get("duration"),
                }
            )
        return rows

    def _queue_result(
        self, data: dict[str, Any], **extra: Any
    ) -> dict[str, Any]:
        rows = self._queue_rows(data)
        return {
            **extra,
            "count": len(rows),
            "server_modified": data.get("serverModified"),
            "episodes": rows,
        }

    def up_next(self) -> dict[str, Any]:
        """Returns the Up Next queue in play order.

        Returns:
            A dict with count, server_modified and the episodes rows, where
            position 1 is the episode currently playing.
        """
        data = self._call(_post, "/up_next/list", dict(UP_NEXT_LIST_PAYLOAD))
        if "order" in data and isinstance(data.get("episodes"), dict):
            # Legacy shape: order list + episodes keyed by uuid. Normalise it.
            eps = data["episodes"]
            data = {
                "serverModified": data.get("serverModified"),
                "episodes": [
                    {"uuid": u, **(eps.get(u) or {})} for u in data["order"]
                ],
            }
        return self._queue_result(data)

    # -- writes -------------------------------------------------------------

    def _episode_payload(self, episode_uuid: str) -> dict[str, Any]:
        ep = self._call(pc_podcasts.get_user_episode, episode_uuid)
        payload = {
            "uuid": ep.uuid,
            "title": ep.title,
            "url": ep.url,
            "podcast": ep.podcast_uuid,
            "published": ep.published,
        }
        missing = [k for k, v in payload.items() if not v]
        if missing:
            raise PocketCastsError(
                f"Episode {episode_uuid} is missing {', '.join(missing)}; "
                "cannot queue it."
            )
        return payload

    def add(self, episode_uuid: str, position: str = "next") -> dict[str, Any]:
        """Adds an episode to Up Next.

        Args:
            episode_uuid: The episode to queue.
            position: "next" to play after the current episode; any other
                value appends it.

        Returns:
            The new queue, with the added title and position.

        Raises:
            PocketCastsError: The episode lacks a field the queue needs, or
                the request failed.
        """
        payload = self._episode_payload(episode_uuid)
        path = (
            "/up_next/play_next" if position == "next" else "/up_next/play_last"
        )
        data = self._call(_post, path, {"version": 2, "episode": payload})
        return self._queue_result(
            data, added=payload["title"], position=position
        )

    def remove(self, episode_uuids: list[str]) -> dict[str, Any]:
        """Removes episodes from Up Next.

        Args:
            episode_uuids: The episodes to remove.

        Returns:
            The new queue, with the removed uuids.
        """
        data = self._call(
            _post,
            "/up_next/remove",
            {"version": 2, "uuids": list(episode_uuids)},
        )
        if not data.get("episodes"):
            data = self._call(
                _post, "/up_next/list", dict(UP_NEXT_LIST_PAYLOAD)
            )
        return self._queue_result(data, removed=list(episode_uuids))

    def move_to_front(self, episode_uuid: str) -> dict[str, Any]:
        """Moves an episode to play next, right after the current episode.

        The API has no reorder call, so this removes the episode and queues
        it again with play_next. A failed remove is logged and ignored, so
        an episode that was not queued gets queued.

        Args:
            episode_uuid: The episode to move.

        Returns:
            The new queue, with the moved title.
        """
        # Emulated reorder: the API has no move; remove then re-add at the
        # front.
        payload = self._episode_payload(episode_uuid)
        try:
            self._call(
                _post,
                "/up_next/remove",
                {"version": 2, "uuids": [episode_uuid]},
            )
        except PocketCastsError as e:
            log.info(
                "remove before move failed (episode may not be queued): %s", e
            )
        data = self._call(
            _post, "/up_next/play_next", {"version": 2, "episode": payload}
        )
        return self._queue_result(data, moved_to_front=payload["title"])
