"""Characterization tests for server.py: the MCP surface Claude sees.

The tool names, descriptions, input and output schemas and the server
instructions are pinned byte for byte against tests/fixtures/server_tools.json,
which was recorded from the code before the Google-style refactor. Tool
docstrings double as tool descriptions, so a reflowed docstring shows up here.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
from mcp.server.mcpserver import exceptions as mcp_exceptions

from pocketcasts_mcp import client, server

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "server_tools.json"


def _surface() -> dict:
    tools = asyncio.run(server.server.list_tools())
    return {
        "instructions": server.server.instructions,
        "tools": [t.model_dump(mode="json") for t in tools],
    }


def test_tool_surface_matches_golden():
    golden = json.loads(GOLDEN.read_text())

    assert _surface() == golden


class RecordingClient:
    """Stands in for server._pc and records each call."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.error = error

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if self.error is not None:
            raise self.error
        return {"called": name}

    def up_next(self):
        return self._record("up_next")

    def subscriptions(self, query, limit):
        return self._record("subscriptions", query, limit)

    def episodes(self, podcast, limit):
        return self._record("episodes", podcast, limit)

    def new_releases(self, limit):
        return self._record("new_releases", limit)

    def in_progress(self, limit):
        return self._record("in_progress", limit)

    def add(self, episode_uuid, position):
        return self._record("add", episode_uuid, position)

    def remove(self, episode_uuids):
        return self._record("remove", episode_uuids)

    def move_to_front(self, episode_uuid):
        return self._record("move_to_front", episode_uuid)


@pytest.fixture
def recorder(monkeypatch):
    fake = RecordingClient()
    monkeypatch.setattr(server, "_pc", fake)
    return fake


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda: server.get_up_next(), ("up_next",)),
        (lambda: server.list_subscriptions(), ("subscriptions", None, 50)),
        (
            lambda: server.list_subscriptions("abc", 0),
            ("subscriptions", "abc", 1),
        ),
        (
            lambda: server.list_subscriptions("", 9999),
            ("subscriptions", None, 500),
        ),
        (lambda: server.list_episodes("pod"), ("episodes", "pod", 20)),
        (lambda: server.list_episodes("pod", -5), ("episodes", "pod", 1)),
        (lambda: server.list_episodes("pod", 101), ("episodes", "pod", 100)),
        (lambda: server.list_new_releases(), ("new_releases", 20)),
        (lambda: server.list_new_releases(0), ("new_releases", 1)),
        (lambda: server.list_in_progress(500), ("in_progress", 100)),
        (lambda: server.add_to_up_next("ep"), ("add", "ep", "next")),
        (lambda: server.add_to_up_next("ep", "last"), ("add", "ep", "last")),
        (
            lambda: server.remove_from_up_next(["a", "b"]),
            ("remove", ["a", "b"]),
        ),
        (lambda: server.move_to_front("ep"), ("move_to_front", "ep")),
    ],
)
def test_tools_clamp_and_forward_arguments(recorder, call, expected):
    assert call() == {"called": expected[0]}
    assert recorder.calls == [expected]


def test_remove_with_no_uuids_is_refused(recorder):
    with pytest.raises(
        mcp_exceptions.ToolError, match="^episode_uuids must not be empty$"
    ):
        server.remove_from_up_next([])
    assert recorder.calls == []


def test_client_error_becomes_tool_error_with_same_text(monkeypatch):
    fake = RecordingClient(client.PocketCastsError("readable message"))
    monkeypatch.setattr(server, "_pc", fake)

    with pytest.raises(mcp_exceptions.ToolError) as exc_info:
        server.get_up_next()

    assert str(exc_info.value) == "readable message"
    assert isinstance(exc_info.value.__cause__, client.PocketCastsError)


def test_unexpected_error_is_named_and_logged(monkeypatch, caplog):
    fake = RecordingClient(KeyError("episodes"))
    monkeypatch.setattr(server, "_pc", fake)

    with pytest.raises(mcp_exceptions.ToolError) as exc_info:
        server.list_in_progress()

    assert str(exc_info.value) == "KeyError: 'episodes'"
    (record,) = [r for r in caplog.records if r.name == server.__name__]
    assert record.getMessage() == "unexpected error in in_progress"
    assert record.exc_info is not None
