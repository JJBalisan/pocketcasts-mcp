# pocketcasts-mcp

Local MCP server that lets Claude read and edit the Pocket Casts **Up Next**
queue. Wraps the unofficial web-player API via
[`api-pocketcasts`](https://pypi.org/project/api-pocketcasts/). Pocket Casts has
no official API, so Automattic can change this without notice.

## Tools

| Tool | What it does |
|---|---|
| `get_up_next` | Queue in play order with podcast titles and progress |
| `list_subscriptions(query, limit)` | Subscribed podcasts, uuid + title; filter by substring, default 50 rows |
| `list_episodes(podcast, limit)` | Recent episodes of one podcast (title or uuid) |
| `list_new_releases(limit)` | New Releases filter |
| `list_in_progress(limit)` | Partly played episodes |
| `add_to_up_next(episode_uuid, position)` | `next` = right after the current episode, `last` = end |
| `remove_from_up_next(episode_uuids)` | Remove by uuid |
| `move_to_front(episode_uuid)` | Emulated: remove + play-next |

Position 1 of the queue is the episode currently playing, so "play next" lands
at position 2. There is no true reorder: the apps push the whole list to
`/up_next/sync`. Moving one episode is done by removing and re-adding it, which
was verified live not to create duplicates.

The four `up_next/*` calls bypass the library's typed models on purpose: Pocket
Casts added an `alternateEnclosures` field in 2026 that the frozen `attrs`
models reject. Raw dicts are parsed leniently instead.

## Setup

```zsh
cd ~/Developer/active/pocketcasts-mcp
/opt/homebrew/bin/uv venv --python ~/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12
/opt/homebrew/bin/uv sync
.venv/bin/pocketcasts-mcp-auth          # prompts for email + password, stores only the refresh token
.venv/bin/pocketcasts-mcp-auth --check  # verifies the stored session
```

Then quit Claude Desktop and run `scripts/install-desktop-config.sh`, or paste
this into `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
"pocketcasts": {
  "command": "/Users/jjbalisan/Developer/active/pocketcasts-mcp/.venv/bin/pocketcasts-mcp",
  "args": []
}
```

## Credentials

Only the refresh token and account email are stored, in the macOS Keychain
under service `pocketcasts-mcp` (via `keyring`). The password is used once by
the auth command and never written anywhere. The first time the server or the
auth command touches the keychain, macOS may ask to allow access for the venv's
python; choose Always Allow. Both commands share that interpreter, so one
approval covers both.

If every call fails with an auth error, run `pocketcasts-mcp-auth` again.
`pocketcasts-mcp-auth --logout` removes the stored session.

## Releasing

Deploying is `git pull` in this checkout, so a release is just a name for
what is deployed. The server logs `pocketcasts-mcp <version>` to stderr as
its first line at every start (Claude Desktop and Claude Code keep the
server's stderr in their MCP logs), where the version is
`git describe --tags --long --dirty --always` on the checkout:
`v1.0.0-0-g<sha>` on a release, `v1.0.0-3-g<sha>` three commits past it,
`-dirty` while the tree has local edits, `unknown` when git cannot answer.
`REPO_VERSION` in the environment overrides it, which is how the tests pin
the line.

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new
   `[X.Y.Z] - date` heading and update the compare links at the bottom; set
   the same version in `pyproject.toml` and `src/pocketcasts_mcp/__init__.py`
   and run `/opt/homebrew/bin/uv lock` so `uv sync --locked` still passes.
2. Merge to `main`, pull, then tag the merge commit and push the tag over
   SSH:

   ```bash
   git tag -a vX.Y.Z -m vX.Y.Z && git push git@github.com:JJBalisan/pocketcasts-mcp.git vX.Y.Z
   ```

3. The next start of the server logs `pocketcasts-mcp vX.Y.Z-0-g<sha>`.

MAJOR when a tool changes what it does or is removed, MINOR for a new tool or
behaviour, PATCH for a fix.
