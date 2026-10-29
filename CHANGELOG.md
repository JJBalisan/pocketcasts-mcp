# Changelog

Every release is an annotated git tag `vX.Y.Z` on `main`; see "Releasing" in
the README. The server logs the tag or commit it runs from as its first line
on stderr at every start, so a Claude Desktop or Claude Code log can always be
matched to an entry here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions: [Semantic Versioning](https://semver.org/spec/v2.0.0.html), where
a MAJOR change alters what a tool does or removes one, MINOR adds a tool or
a behaviour, PATCH fixes one.

## [Unreleased]

### Added

- MIT licence (`LICENSE`); the repository is now public.

## [1.0.1] - 2026-10-29

### Changed

- CI follows dev-toolchain's template: push runs on `main` only and a
  newer push to a PR cancels its older run, so a PR runs once;
  `actions/checkout` v7 and `astral-sh/setup-uv` pinned to v10.3.0 by commit,
  with its cache off on the self-hosted runner.

## [1.0.0] - 2026-10-26

### Added

- Local MCP server over stdio for the Pocket Casts Up Next queue, wrapping
  the unofficial web-player API via `api-pocketcasts`. Tools: `get_up_next`,
  `list_subscriptions`, `list_episodes`, `list_new_releases`,
  `list_in_progress`, `add_to_up_next` (`next` or `last`),
  `remove_from_up_next` and `move_to_front` (emulated as remove + play-next;
  the API has no reorder call). Position 1 of the queue is the episode
  currently playing.
- `pocketcasts-mcp-auth`: logs in once and keeps only the refresh token and
  account email in the macOS Keychain (service `pocketcasts-mcp`); `--check`
  verifies the stored session, `--logout` removes it.
- `scripts/install-desktop-config.sh`: adds the server to Claude Desktop's
  config with a timestamped backup, refusing while the app is running.
- The server logs `pocketcasts-mcp <version>` to stderr at start-up, where
  the version is `git describe --tags --long --dirty --always` on the
  checkout (`REPO_VERSION` in the environment overrides it, `unknown` when
  git cannot answer); the install script logs the same line.
- Hermetic test suite (fakes for the keychain and the Pocket Casts API) and
  `tests/ci.sh`, the whole CI as one script.

[Unreleased]: https://github.com/JJBalisan/pocketcasts-mcp/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/JJBalisan/pocketcasts-mcp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/JJBalisan/pocketcasts-mcp/releases/tag/v1.0.0
