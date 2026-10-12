"""Hermetic stand-ins for the Pocket Casts HTTP API and the macOS keychain.

FakePocketCastsAPI is the handler behind an httpx.MockTransport. conftest.py
routes httpx.post and httpx.get through it, so the client's own _post and the
api-pocketcasts library calls (token refresh, podcast list, user episode) all
hit this fake instead of api.pocketcasts.com.

FakeUpNext models the server side of the Up Next queue closely enough to check
what move_to_front does to queue order: position 1 is the playing episode and
play_next inserts at position 2.

Every value here is invented. No recorded user data is used.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from typing import Any

import httpx
import keyring.errors

API_HOST = "api.pocketcasts.com"
REFRESH_ENDPOINT = "/user/token"
STORED_REFRESH = "rt-stored"
EMAIL = "listener@example.invalid"

Reply = httpx.Response | dict | list | Exception | Callable[..., Any]


@dataclasses.dataclass
class Call:
    """One request the fake API received."""

    method: str
    path: str
    body: Any
    authorization: str | None


class FakePocketCastsAPI:
    """Routes requests by URL path to canned or computed replies.

    A route holds one or more replies. Each request consumes the next reply;
    the last reply repeats. A reply can be an httpx.Response, a JSON-able
    dict or list (sent as 200), an exception instance (raised, as a transport
    error would be), or a callable taking the request and returning any of
    those.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.unrouted: list[str] = []
        self.tokens_issued = 0
        self.refresh_reply_token = STORED_REFRESH
        self.expires_in = 3600
        self._routes: dict[str, list[Reply]] = {}
        self.route(REFRESH_ENDPOINT, self._issue_token)

    def route(self, path: str, *replies: Reply) -> None:
        """Replaces the replies served for path."""
        self._routes[path] = list(replies)

    def paths(self) -> list[str]:
        """Returns the request paths in the order they arrived."""
        return [c.path for c in self.calls]

    def calls_to(self, path: str) -> list[Call]:
        """Returns the requests made to path."""
        return [c for c in self.calls if c.path == path]

    def handle(self, request: httpx.Request) -> httpx.Response:
        """MockTransport handler."""
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append(
            Call(
                request.method,
                path,
                body,
                request.headers.get("authorization"),
            )
        )
        replies = self._routes.get(path)
        if request.url.host != API_HOST or not replies:
            # Recorded as well as raised: the library wraps every exception,
            # so the raise alone could be swallowed. conftest checks the list.
            self.unrouted.append(f"{request.method} {request.url}")
            raise AssertionError(f"unrouted request {request.url}")
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def _issue_token(self, request: httpx.Request) -> dict[str, Any]:
        del request  # Unused.
        self.tokens_issued += 1
        return token_reply(
            f"tok-{self.tokens_issued}",
            refresh=self.refresh_reply_token,
            expires_in=self.expires_in,
        )


class FakeKeyring:
    """In-memory replacement for the keyring module used by auth.py."""

    errors = keyring.errors

    def __init__(self, entries: dict[tuple[str, str], str]) -> None:
        self.entries = dict(entries)
        self.writes: list[tuple[str, str, str]] = []

    def get_password(self, service: str, key: str) -> str | None:
        """Returns the stored value or None."""
        return self.entries.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        """Stores a value and records the write."""
        self.writes.append((service, key, value))
        self.entries[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        """Deletes a value; raises like keyring when it is absent."""
        if (service, key) not in self.entries:
            raise keyring.errors.PasswordDeleteError(key)
        del self.entries[(service, key)]


class FakeUpNext:
    """Server-side Up Next queue for the /up_next/* endpoints.

    Position 1 is the playing episode. play_next inserts at position 2 (or 1
    on an empty queue) and first drops any existing copy of the episode, as
    the live API was verified not to create duplicates.
    """

    def __init__(self, episodes: list[dict[str, Any]]) -> None:
        self.episodes = [dict(e) for e in episodes]
        self.modified = 1_760_000_000_000

    def uuids(self) -> list[str]:
        """Returns the queued uuids in play order."""
        return [e["uuid"] for e in self.episodes]

    def install(self, api: FakePocketCastsAPI) -> None:
        """Routes the three up_next endpoints on api to this queue."""
        api.route("/up_next/list", lambda req: self._reply())
        api.route("/up_next/remove", self._remove)
        api.route("/up_next/play_next", self._play_next)

    def _reply(self) -> dict[str, Any]:
        self.modified += 1
        return {
            "serverModified": self.modified,
            "episodes": [dict(e) for e in self.episodes],
            "episodeSync": [],
        }

    def _remove(self, request: httpx.Request) -> dict[str, Any]:
        gone = set(json.loads(request.content)["uuids"])
        self.episodes = [e for e in self.episodes if e["uuid"] not in gone]
        return self._reply()

    def _play_next(self, request: httpx.Request) -> dict[str, Any]:
        episode = json.loads(request.content)["episode"]
        self.episodes = [
            e for e in self.episodes if e["uuid"] != episode["uuid"]
        ]
        self.episodes.insert(min(1, len(self.episodes)), dict(episode))
        return self._reply()


def token_reply(
    access: str,
    refresh: str = STORED_REFRESH,
    expires_in: int = 3600,
) -> dict[str, Any]:
    """Returns a /user/token body with every field the library requires."""
    return {
        "email": EMAIL,
        "uuid": "user-0000",
        "isNew": False,
        "accessToken": access,
        "tokenType": "Bearer",
        "expiresIn": expires_in,
        "refreshToken": refresh,
    }


def podcast_list_reply(podcasts: list[tuple[str, str]]) -> dict[str, Any]:
    """Returns a /user/podcast/list body for (uuid, title) pairs."""
    return {
        "podcasts": [
            {"uuid": uuid, "title": title, "author": f"Author of {title}"}
            for uuid, title in podcasts
        ],
        "folders": [],
    }


def queue_entry(uuid: str, podcast: str = "pod-aaaa") -> dict[str, Any]:
    """Returns one Up Next episode in the five-field queue shape."""
    return {
        "uuid": uuid,
        "title": f"Title {uuid}",
        "url": f"https://media.example.invalid/{uuid}.mp3",
        "podcast": podcast,
        "published": "2026-09-01T08:00:00Z",
    }


def user_episode_reply(uuid: str, podcast: str = "pod-aaaa") -> dict[str, Any]:
    """Returns a /user/episode body that passes the library's validation."""
    entry = queue_entry(uuid, podcast)
    return {
        "uuid": uuid,
        "url": entry["url"],
        "published": entry["published"],
        "duration": 1800,
        "fileType": "audio/mpeg",
        "title": entry["title"],
        "podcastUuid": podcast,
        "podcastTitle": "Podcast A",
        "playingStatus": 1,
        "playedUpTo": 0,
    }
