"""MCP server exposing the Pocket Casts Up Next queue.

Runs over stdio. All logging goes to stderr because stdout carries JSON-RPC.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any, Literal

logging.basicConfig(
    stream=sys.stderr, level=logging.INFO, format="%(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)

from mcp.server import mcpserver  # noqa: E402
from mcp.server.mcpserver import exceptions as mcp_exceptions  # noqa: E402

from pocketcasts_mcp import client, version  # noqa: E402

log = logging.getLogger(__name__)

server = mcpserver.MCPServer(
    "pocketcasts",
    instructions=(
        "Read and edit the user's Pocket Casts Up Next queue. Episodes are "
        "identified by uuid; find them with list_episodes, list_new_releases, "
        "or list_in_progress before adding. Podcasts can be referenced by "
        "title (fuzzy) or uuid."
    ),
    log_level="WARNING",
)
_pc = client.PocketCasts()


def _guard[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except client.PocketCastsError as e:
        raise mcp_exceptions.ToolError(str(e)) from e
    except Exception as e:  # unexpected response shapes, raw transport errors
        log.exception("unexpected error in %s", getattr(fn, "__name__", fn))
        raise mcp_exceptions.ToolError(f"{type(e).__name__}: {e}") from e


# A tool's description is what Claude sees. Descriptions too long for one
# docstring line are passed as description= so the text stays on one line;
# the docstring then only summarises the tool.


@server.tool(
    description=(
        "Return the Up Next queue in play order. Position 1 is the episode "
        "currently playing; position 2 plays next."
    )
)
def get_up_next() -> dict[str, Any]:
    """Return the Up Next queue in play order."""
    return _guard(_pc.up_next)


@server.tool(
    description=(
        "List subscribed podcasts (uuid, title, author), newest release "
        "first. `query` filters by substring of title or author (empty = "
        "all); `limit` caps the rows returned and total_matching reports how "
        "many matched."
    )
)
def list_subscriptions(query: str = "", limit: int = 50) -> dict[str, Any]:
    """List subscribed podcasts (uuid, title, author), newest release first."""
    return _guard(_pc.subscriptions, query or None, max(1, min(limit, 500)))


@server.tool(
    description=(
        "List recent episodes of one subscribed podcast, newest first. "
        "`podcast` is a title (fuzzy) or uuid."
    )
)
def list_episodes(podcast: str, limit: int = 20) -> dict[str, Any]:
    """List recent episodes of one subscribed podcast, newest first."""
    return _guard(_pc.episodes, podcast, max(1, min(limit, 100)))


@server.tool(
    description=(
        "Episodes in the New Releases filter (recent, unplayed) across all "
        "subscriptions."
    )
)
def list_new_releases(limit: int = 20) -> list[dict[str, Any]]:
    """Episodes in the New Releases filter across all subscriptions."""
    return _guard(_pc.new_releases, max(1, min(limit, 100)))


@server.tool()
def list_in_progress(limit: int = 20) -> list[dict[str, Any]]:
    """Episodes that are partly played."""
    return _guard(_pc.in_progress, max(1, min(limit, 100)))


@server.tool(
    description=(
        'Queue an episode. position="next" inserts right after the currently '
        'playing episode (position 2); "last" appends. Returns the new queue.'
    )
)
def add_to_up_next(
    episode_uuid: str, position: Literal["next", "last"] = "next"
) -> dict[str, Any]:
    """Queue an episode next (position 2) or last in Up Next."""
    return _guard(_pc.add, episode_uuid, position)


@server.tool(
    description=(
        "Remove one or more episodes (by uuid) from Up Next. Returns the new "
        "queue."
    )
)
def remove_from_up_next(episode_uuids: list[str]) -> dict[str, Any]:
    """Remove one or more episodes (by uuid) from Up Next."""
    if not episode_uuids:
        raise mcp_exceptions.ToolError("episode_uuids must not be empty")
    return _guard(_pc.remove, episode_uuids)


@server.tool(
    description=(
        "Move a queued episode to play next (position 2, right after the "
        "current episode). Emulated as remove + play-next; the API has no "
        "reorder call."
    )
)
def move_to_front(episode_uuid: str) -> dict[str, Any]:
    """Move a queued episode to play next (position 2)."""
    return _guard(_pc.move_to_front, episode_uuid)


def main() -> None:
    """Runs the MCP server over stdio.

    The first line on stderr names the checkout (tag or commit) the server
    runs from, so a client log can be matched to the code behind it.
    """
    log.info("pocketcasts-mcp %s", version.repo_version())
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
