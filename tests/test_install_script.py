"""Characterization tests for scripts/install-desktop-config.sh.

The script runs by path, so its shebang picks the interpreter. HOME points at
a temporary directory and a stub `ps` comes first on PATH, so nothing outside
tmp_path is read or written and the running-app check is controlled.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts"
    / "install-desktop-config.sh"
)
INTERPRETER = SCRIPT.read_text().splitlines()[0].removeprefix("#!").split()[0]
CONFIG_REL = "Library/Application Support/Claude/claude_desktop_config.json"
BIN_REL = "Developer/active/pocketcasts-mcp/.venv/bin/pocketcasts-mcp"
ORIGINAL = {"mcpServers": {"other": {"command": "/bin/other", "args": ["x"]}}}

pytestmark = pytest.mark.skipif(
    shutil.which(INTERPRETER) is None,
    reason=f"{INTERPRETER} is not installed",
)


@pytest.fixture
def home(tmp_path):
    """A fake HOME holding a Claude Desktop config and a server binary."""
    home = tmp_path / "home"
    config = home / CONFIG_REL
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(ORIGINAL))
    binary = home / BIN_REL
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return home


def run(home, tmp_path, processes=""):
    """Runs the script with a stub ps that prints processes."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir(exist_ok=True)
    ps = stub_dir / "ps"
    ps.write_text(f"#!/bin/sh\nprintf '%s' '{processes}'\n")
    ps.chmod(0o755)
    env = {
        "HOME": str(home),
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
    }
    return subprocess.run(  # noqa: S603 - runs this repo's own script.
        [str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def backups(home):
    return sorted((home / CONFIG_REL).parent.glob("*.bak.*"))


def test_adds_server_and_keeps_backup(home, tmp_path):
    result = run(home, tmp_path, "/usr/sbin/cfprefsd\nlogin\n")

    assert result.returncode == 0
    assert result.stderr == ""
    config = home / CONFIG_REL
    binary = home / BIN_REL
    lines = result.stdout.splitlines()
    assert lines[0] == f"Added mcpServers.pocketcasts -> {binary}"
    assert re.fullmatch(
        rf"Backup: {re.escape(str(config))}\.bak\.\d{{8}}-\d{{6}}", lines[1]
    )
    assert lines[2:] == ["Now relaunch Claude Desktop."]
    assert len(lines) == 3

    (backup,) = backups(home)
    assert lines[1] == f"Backup: {backup}"
    assert json.loads(backup.read_text()) == ORIGINAL
    expected = {
        "mcpServers": {
            "other": ORIGINAL["mcpServers"]["other"],
            "pocketcasts": {"command": str(binary), "args": []},
        }
    }
    assert config.read_text() == json.dumps(expected, indent=2) + "\n"
    leftovers = [
        p for p in tmp_path.iterdir() if p.name not in ("home", "stubs")
    ]
    assert leftovers == []


def test_adds_mcp_servers_key_when_missing(home, tmp_path):
    config = home / CONFIG_REL
    config.write_text('{"theme": "dark"}')

    result = run(home, tmp_path)

    assert result.returncode == 0
    assert json.loads(config.read_text()) == {
        "theme": "dark",
        "mcpServers": {
            "pocketcasts": {"command": str(home / BIN_REL), "args": []}
        },
    }


@pytest.mark.parametrize(
    "processes",
    ["Claude\n", "/Applications/Claude.app/Contents/MacOS/Claude\n"],
)
def test_refuses_while_claude_desktop_runs(home, tmp_path, processes):
    result = run(home, tmp_path, f"login\n{processes}")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Claude Desktop is running. Quit it (Cmd-Q), run this again, then"
        " relaunch.\n"
    )
    assert json.loads((home / CONFIG_REL).read_text()) == ORIGINAL
    assert backups(home) == []


def test_other_claude_processes_do_not_block(home, tmp_path):
    result = run(
        home, tmp_path, "/usr/local/bin/claude\nClaude Helper\nClaudeX\n"
    )

    assert result.returncode == 0


def test_refuses_without_server_binary(home, tmp_path):
    (home / BIN_REL).unlink()

    result = run(home, tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    project = home / "Developer/active/pocketcasts-mcp"
    assert result.stderr == (
        f"Server binary missing at {home / BIN_REL}; run 'uv sync' in"
        f" {project} first.\n"
    )
    assert backups(home) == []


def test_refuses_without_config(home, tmp_path):
    (home / CONFIG_REL).unlink()

    result = run(home, tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Config not found at {home / CONFIG_REL}\n"
