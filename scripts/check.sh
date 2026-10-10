#!/bin/sh
# Quality gates. Ralph and humans both run this; all must pass before commit.
set -e
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
