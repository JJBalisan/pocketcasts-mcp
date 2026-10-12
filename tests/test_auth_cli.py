"""Characterization tests for the pocketcasts-mcp-auth command line."""

from __future__ import annotations

import sys

import httpx
import pytest

from pocketcasts_mcp import auth
from tests import fakes

LOGIN = "/user/login_pocket_casts"
REFRESH_KEY = (auth.SERVICE, auth.KEY_REFRESH)
EMAIL_KEY = (auth.SERVICE, auth.KEY_EMAIL)

HELP = """\
usage: pocketcasts-mcp-auth [-h] [--check | --logout]

Log in to Pocket Casts once and keep only the refresh token in the keychain.

options:
  -h, --help  show this help message and exit
  --check     verify the stored session still works
  --logout    delete the stored session
"""


class FakeTTY:
    """A stdin that claims to be a terminal."""

    def isatty(self):
        return True


@pytest.fixture
def terminal(monkeypatch):
    """Simulates a terminal session; returns the prompts shown."""
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return "  typed@example.invalid  "

    def fake_getpass(prompt):
        prompts.append(prompt)
        return "hunter2"

    monkeypatch.setattr(sys, "stdin", FakeTTY())
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(auth.getpass, "getpass", fake_getpass)
    return prompts


def test_help_text(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "80")

    with pytest.raises(SystemExit) as exc_info:
        auth.main(["--help"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == HELP


def test_check_and_logout_are_exclusive(capsys):
    with pytest.raises(SystemExit) as exc_info:
        auth.main(["--check", "--logout"])

    assert exc_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_check_ok(api, capsys):
    assert auth.main(["--check"]) == 0

    out = capsys.readouterr()
    assert out.out == f"OK: refreshed an access token for {fakes.EMAIL}.\n"
    assert out.err == ""
    assert api.tokens_issued == 1


def test_check_without_email_names_unknown_account(fake_keyring, capsys):
    del fake_keyring.entries[EMAIL_KEY]

    assert auth.main(["--check"]) == 0
    assert capsys.readouterr().out == (
        "OK: refreshed an access token for unknown account.\n"
    )


def test_check_without_session(fake_keyring, api, capsys):
    del fake_keyring.entries[REFRESH_KEY]

    assert auth.main(["--check"]) == 1

    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == auth.AUTH_HINT + "\n"
    assert api.calls == []


def test_check_with_rejected_token(api, capsys):
    api.route(fakes.REFRESH_ENDPOINT, httpx.Response(401, json={}))

    assert auth.main(["--check"]) == 1

    err = capsys.readouterr().err
    assert err.startswith("FAIL: Pocket Casts refused the stored refresh token")
    assert err.endswith("Run `pocketcasts-mcp-auth` again.\n")


def test_logout(fake_keyring, capsys):
    assert auth.main(["--logout"]) == 0

    assert capsys.readouterr().out == "Stored Pocket Casts session removed.\n"
    assert fake_keyring.entries == {}


def test_logout_when_already_logged_out(fake_keyring, capsys):
    fake_keyring.entries.clear()

    assert auth.main(["--logout"]) == 0
    assert capsys.readouterr().out == "Stored Pocket Casts session removed.\n"


def test_login_refuses_without_terminal(api, capsys):
    assert auth.main([]) == 2

    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == (
        "Run this from a real terminal; it prompts for your password.\n"
    )
    assert api.calls == []


def test_login_stores_session(terminal, fake_keyring, api, capsys):
    api.route(LOGIN, fakes.token_reply("tok-login", refresh="rt-login"))

    assert auth.main([]) == 0

    assert terminal == [
        "Pocket Casts email: ",
        "Pocket Casts password (not stored): ",
    ]
    (call,) = api.calls_to(LOGIN)
    assert call.body == {
        "email": "typed@example.invalid",
        "password": "hunter2",
    }
    assert fake_keyring.writes == [
        (auth.SERVICE, auth.KEY_EMAIL, fakes.EMAIL),
        (auth.SERVICE, auth.KEY_REFRESH, "rt-login"),
    ]
    assert capsys.readouterr().out == (
        f"Logged in as {fakes.EMAIL}. Refresh token saved to the keychain"
        " (service 'pocketcasts-mcp').\n"
    )


def test_login_failure(terminal, fake_keyring, api, capsys):
    del terminal  # Only needed for its side effects.
    api.route(LOGIN, httpx.Response(401, json={}))

    assert auth.main([]) == 1

    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith("Login failed: ")
    assert fake_keyring.writes == []


def test_login_without_refresh_token(terminal, fake_keyring, api, capsys):
    del terminal  # Only needed for its side effects.
    api.route(LOGIN, fakes.token_reply("tok-login", refresh=""))

    assert auth.main([]) == 1

    assert capsys.readouterr().err == (
        "Login succeeded but no refresh token was returned; cannot continue.\n"
    )
    assert fake_keyring.writes == []
