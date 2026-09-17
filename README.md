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
