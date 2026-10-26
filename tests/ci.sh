#!/bin/bash
#
# CI for wt-pocketcasts-mcp, drafted 2026-10-26 by dev-toolchain's
# ci/gen_ci_sh.py from ci.yml.
# Review it, then make the workflow's one step `bash tests/ci.sh`; from then
# on edit this file, not the workflow.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# job: test (ci.yml)
# runs-on: ubuntu-latest

# step: actions/checkout@v5
# uses: actions/checkout@v5 (nothing needed: the repo is already checked out)

# step: astral-sh/setup-uv@v6
# uses: astral-sh/setup-uv@v6 (the local environment must provide: uv on PATH)

# step: Install (locked)
uv sync --locked

# step: Tests
uv run pytest -q
