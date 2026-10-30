#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
uv sync --frozen
uv run --frozen python scripts/verify_source.py
uv run --frozen pytest -q
uv build --no-sources
check_tmp=$(mktemp -d)
trap 'rm -rf "$check_tmp"' EXIT
uv venv "$check_tmp/venv" --python 3.13
uv export --frozen --no-emit-project --format requirements-txt --output-file "$check_tmp/requirements.txt" > /dev/null
uv pip sync --python "$check_tmp/venv/bin/python" --require-hashes "$check_tmp/requirements.txt"
uv pip install --python "$check_tmp/venv/bin/python" --no-deps dist/*.whl
cd "$check_tmp"
"$check_tmp/venv/bin/python" -I -m pytest "$root/tests" -q
