"""Shared fixtures: tests run against fakes, never the network or keychain.

Three guards keep the suite hermetic:

* keyring is switched to its always-failing backend for the whole session, and
  auth.py's keyring reference is swapped for an in-memory FakeKeyring per test.
* httpx.post and httpx.get (used by the client and by api-pocketcasts) are
  routed through an httpx.MockTransport backed by FakePocketCastsAPI.
* httpx.HTTPTransport.handle_request refuses to run, so a stray httpx.Client
  cannot reach a real host either.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import keyring
import keyring.backends.fail
import pytest

from pocketcasts_mcp import auth, client
from tests import fakes

keyring.set_keyring(keyring.backends.fail.Keyring())


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> fakes.FakeKeyring:
    """Replaces the keychain with an in-memory store holding one session."""
    fake = fakes.FakeKeyring(
        {
            (auth.SERVICE, auth.KEY_REFRESH): fakes.STORED_REFRESH,
            (auth.SERVICE, auth.KEY_EMAIL): fakes.EMAIL,
        }
    )
    monkeypatch.setattr(auth, "keyring", fake)
    return fake


@pytest.fixture(autouse=True)
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[fakes.FakePocketCastsAPI]:
    """Routes all module-level httpx calls to a fresh FakePocketCastsAPI."""
    fake = fakes.FakePocketCastsAPI()

    def sender(method: str) -> Any:
        def send(url: str, **kwargs: Any) -> httpx.Response:
            transport = httpx.MockTransport(fake.handle)
            with httpx.Client(transport=transport) as http:
                return http.request(method, url, **kwargs)

        return send

    def refuse(self: httpx.HTTPTransport, request: httpx.Request) -> None:
        del self  # Unused.
        fake.unrouted.append(f"real transport: {request.url}")
        raise AssertionError(f"real network access to {request.url}")

    monkeypatch.setattr(httpx, "post", sender("POST"))
    monkeypatch.setattr(httpx, "get", sender("GET"))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    yield fake
    assert not fake.unrouted, f"unexpected requests: {fake.unrouted}"


@pytest.fixture
def pc() -> client.PocketCasts:
    """A fresh client; its TokenManager reads the fake keyring."""
    return client.PocketCasts()
