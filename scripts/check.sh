#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
unset PYTHONPATH UV_NO_SYNC
uv sync --frozen
uv run --frozen pytest -q
uv run --frozen pytest -q scripts/test_release_tools.py
check_tmp=$(mktemp -d)
trap 'rm -rf "$check_tmp"' EXIT
uv build --no-sources --out-dir "$check_tmp/dist"
uv run --frozen python scripts/verify_wheel.py "$check_tmp/dist"
uv run --frozen python scripts/export_release_contract.py "$check_tmp"/dist/*.whl --output "$check_tmp/contract"
uv run --frozen python scripts/verify_release_candidate.py releases/candidate/release.json "$check_tmp/contract/contract.json" "$check_tmp/contract/evidence.json" controlplane-events
uv venv "$check_tmp/venv" --python 3.13
uv export --frozen --no-emit-project --format requirements-txt --output-file "$check_tmp/requirements.txt" >/dev/null
uv pip sync --python "$check_tmp/venv/bin/python" --require-hashes "$check_tmp/requirements.txt"
uv pip install --python "$check_tmp/venv/bin/python" --no-deps "$check_tmp"/dist/*.whl
"$check_tmp/venv/bin/python" -I "$root/scripts/verify_installed_contract.py" "$check_tmp/contract/contract.json"
cp -R tests "$check_tmp/tests"
(cd "$check_tmp" && "$check_tmp/venv/bin/python" -I -m pytest -c /dev/null tests -q)
uv run --frozen python - "$check_tmp" <<'PYTHON'
import pathlib, sys, tarfile
root = pathlib.Path(sys.argv[1])
with tarfile.open(next((root / 'dist').glob('*.tar.gz'))) as archive:
    archive.extractall(root / 'sdist', filter='data')
PYTHON
uv build --wheel "$check_tmp"/sdist/* --no-sources --out-dir "$check_tmp/rebuilt"
uv run --frozen python scripts/verify_wheel.py "$check_tmp/rebuilt"
uv run --frozen python scripts/export_release_contract.py "$check_tmp"/rebuilt/*.whl --output "$check_tmp/rebuilt-contract"
cmp "$check_tmp/contract/contract.json" "$check_tmp/rebuilt-contract/contract.json"
uv pip install --python "$check_tmp/venv/bin/python" --no-deps --reinstall "$check_tmp"/rebuilt/*.whl
"$check_tmp/venv/bin/python" -I "$root/scripts/verify_installed_contract.py" "$check_tmp/contract/contract.json"
(cd "$check_tmp" && "$check_tmp/venv/bin/python" -I -m pytest -c /dev/null tests -q)

# Retain the exact validated bytes for the release channel; never rebuild them.
if [[ -n "${RELEASE_ARTIFACT_DIR:-}" ]]; then
  mkdir -p "$RELEASE_ARTIFACT_DIR"
  cp "$check_tmp/dist"/* "$RELEASE_ARTIFACT_DIR/"
fi
