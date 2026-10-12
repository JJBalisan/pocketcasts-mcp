"""MCP server exposing the Pocket Casts Up Next queue.

Runs over stdio. All logging goes to stderr because stdout carries JSON-RPC.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Literal

logging.basicConfig(
    stream=sys.stderr, level=logging.INFO, format="%(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from .client import PocketCasts, PocketCastsError  # noqa: E402

server = MCPServer(
    "pocketcasts",
    instructions=(
        "Read and edit the user's Pocket Casts Up Next queue. Episodes are identified by uuid; "
        "find them with list_episodes, list_new_releases, or list_in_progress before adding. "
        "Podcasts can be referenced by title (fuzzy) or uuid."
    ),
    log_level="WARNING",
)
_pc = PocketCasts()


def _guard(fn, *args: Any, **kwargs: Any):
    try:
        return fn(*args, **kwargs)
    except PocketCastsError as e:
        raise ToolError(str(e)) from e
    except Exception as e:  # unexpected response shapes, raw transport errors
        logging.getLogger(__name__).exception(
            "unexpected error in %s", getattr(fn, "__name__", fn)
        )
        raise ToolError(f"{type(e).__name__}: {e}") from e


@server.tool()
def get_up_next() -> dict[str, Any]:
    """Return the Up Next queue in play order. Position 1 is the episode currently playing; position 2 plays next."""
    return _guard(_pc.up_next)


@server.tool()
def list_subscriptions(query: str = "", limit: int = 50) -> dict[str, Any]:
    """List subscribed podcasts (uuid, title, author), newest release first. `query` filters by substring of title or author (empty = all); `limit` caps the rows returned and total_matching reports how many matched."""
    return _guard(_pc.subscriptions, query or None, max(1, min(limit, 500)))


@server.tool()
def list_episodes(podcast: str, limit: int = 20) -> dict[str, Any]:
    """List recent episodes of one subscribed podcast, newest first. `podcast` is a title (fuzzy) or uuid."""
    return _guard(_pc.episodes, podcast, max(1, min(limit, 100)))


@server.tool()
def list_new_releases(limit: int = 20) -> list[dict[str, Any]]:
    """Episodes in the New Releases filter (recent, unplayed) across all subscriptions."""
    return _guard(_pc.new_releases, max(1, min(limit, 100)))


@server.tool()
def list_in_progress(limit: int = 20) -> list[dict[str, Any]]:
    """Episodes that are partly played."""
    return _guard(_pc.in_progress, max(1, min(limit, 100)))


@server.tool()
def add_to_up_next(
    episode_uuid: str, position: Literal["next", "last"] = "next"
) -> dict[str, Any]:
    """Queue an episode. position="next" inserts right after the currently playing episode (position 2); "last" appends. Returns the new queue."""
    return _guard(_pc.add, episode_uuid, position)


@server.tool()
def remove_from_up_next(episode_uuids: list[str]) -> dict[str, Any]:
    """Remove one or more episodes (by uuid) from Up Next. Returns the new queue."""
    if not episode_uuids:
        raise ToolError("episode_uuids must not be empty")
    return _guard(_pc.remove, episode_uuids)


@server.tool()
def move_to_front(episode_uuid: str) -> dict[str, Any]:
    """Move a queued episode to play next (position 2, right after the current episode). Emulated as remove + play-next; the API has no reorder call."""
    return _guard(_pc.move_to_front, episode_uuid)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
