"""Tests for version.py: the line the server logs first.

Three cases, as in launchd-jobs' tests/version.sh: REPO_VERSION pins the
output (how other tests keep transcripts stable), the real checkout gives a
git describe string, and a copy of the module outside any repository says
"unknown" instead of failing. The last test starts the real server with a
closed stdin: stdout must stay empty, because it carries JSON-RPC.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

from pocketcasts_mcp import version

MODULE = pathlib.Path(version.__file__).resolve()
PINNED = "v9.9.9-test"
# A tag gives v1.2.0-3-gabc1234; before the first tag git describe --always
# falls back to the bare abbreviated commit. Either may carry -dirty.
DESCRIBE = re.compile(r"^(v\d+\.\d+\.\d+-\d+-g)?[0-9a-f]{7,}(-dirty)?$")


def run_script(path, env):
    """Runs a copy of version.py the way scripts/ does: `python3 -I file`."""
    return subprocess.run(  # noqa: S603 - runs this repo's own module.
        [sys.executable, "-I", str(path)],
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_repo_version_is_pinned_by_environment(monkeypatch):
    monkeypatch.setenv("REPO_VERSION", PINNED)

    assert version.repo_version() == PINNED


def test_empty_pin_is_ignored(monkeypatch):
    monkeypatch.setenv("REPO_VERSION", "")

    assert DESCRIBE.match(version.repo_version())


def test_real_checkout_gives_a_describe_string(monkeypatch):
    monkeypatch.delenv("REPO_VERSION", raising=False)

    assert DESCRIBE.match(version.repo_version())


def test_git_failure_gives_unknown(monkeypatch):
    monkeypatch.delenv("REPO_VERSION", raising=False)

    def refuse(*args, **kwargs):
        del args, kwargs  # Unused.
        raise OSError("no git")

    monkeypatch.setattr(version.subprocess, "run", refuse)

    assert version.repo_version() == "unknown"


@pytest.fixture
def copy_outside_any_repo(tmp_path):
    """A copy of version.py at the same depth in a directory without .git."""
    copy = tmp_path / "not-a-repo" / "src" / "pocketcasts_mcp" / "version.py"
    copy.parent.mkdir(parents=True)
    shutil.copyfile(MODULE, copy)
    return copy


def test_outside_a_repository_prints_unknown(copy_outside_any_repo, tmp_path):
    # The ceiling keeps git from finding a repository above tmp_path, in case
    # the temporary directory ever lives inside one.
    result = run_script(
        copy_outside_any_repo, {"GIT_CEILING_DIRECTORIES": str(tmp_path)}
    )

    assert result.returncode == 0
    assert result.stdout == "unknown\n"
    assert result.stderr == ""


def test_script_prints_the_pin(copy_outside_any_repo):
    result = run_script(copy_outside_any_repo, {"REPO_VERSION": PINNED})

    assert result.returncode == 0
    assert result.stdout == f"{PINNED}\n"


def test_server_logs_version_to_stderr_and_keeps_stdout_clean():
    # What the pocketcasts-mcp console script does, so the logger is named
    # as in Claude Desktop's log rather than __main__.
    result = subprocess.run(  # noqa: S603 - runs this repo's own server.
        [
            sys.executable,
            "-c",
            "from pocketcasts_mcp import server; server.main()",
        ],
        env=dict(os.environ, REPO_VERSION=PINNED),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.stdout == b""
    assert result.stderr.decode() == (
        f"pocketcasts_mcp.server: pocketcasts-mcp {PINNED}\n"
    )
    assert result.returncode == 0
