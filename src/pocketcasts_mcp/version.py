"""Names the checkout the server runs from.

Claude Code and Claude Desktop launch the server from this checkout and
"deploy" is a git pull, so the only honest version is what git says about
the working tree at start-up. The server logs it as its first line, on
stderr, so a log can always be matched to the code that wrote it, including
an unmerged branch left checked out or local edits (`-dirty`).

Releases are annotated tags vX.Y.Z (see README "Releasing"), so the string
is `v1.0.0-0-gabc1234` on a release, `v1.0.0-3-gdef5678` three commits past
it, `abc1234` before the first tag, with `-dirty` appended while the tree
has local changes, or `unknown` when git cannot answer.

git is called by absolute path: Claude Desktop starts the server with a
minimal PATH. /usr/bin/git resolves through a full Xcode on this Mac, so
there is no command-line-tools dialog for a background process to hang on.

The module has no imports from the package, so scripts/ can run it as a
file (`python3 -I .../version.py`) and read the same string.
"""

from __future__ import annotations

import os
import subprocess

# src/pocketcasts_mcp/version.py -> the checkout. realpath follows the
# editable install's link, so the installed copy still names this tree.
REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)


def repo_version() -> str:
    """Returns the tag or commit of the checkout this module lives in.

    REPO_VERSION in the environment overrides it, so tests can pin the
    line.

    Returns:
        The git describe string (`v1.0.0-0-gabc1234`, `abc1234`, either
        with `-dirty`), or 'unknown' when git cannot answer.
    """
    pinned = os.environ.get("REPO_VERSION")
    if pinned:
        return pinned
    try:
        described = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            [
                "/usr/bin/git",
                "-C",
                REPO,
                "describe",
                "--tags",
                "--long",
                "--dirty",
                "--always",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return described or "unknown"


if __name__ == "__main__":
    print(repo_version())
