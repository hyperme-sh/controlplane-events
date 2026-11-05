#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
unset PYTHONPATH UV_NO_SYNC
uv sync --frozen
check_tmp=$(mktemp -d)
trap 'rm -rf "$check_tmp"' EXIT
uv run --frozen pytest -q --junitxml="$check_tmp/source.xml"
uv run --frozen pytest -q tests_tools
uv build --no-sources --out-dir "$check_tmp/dist"
uv run --frozen python scripts/verify_wheel.py "$check_tmp/dist"
uv run --frozen python scripts/export_release_contract.py "$check_tmp"/dist/*.whl --output "$check_tmp/contract"
for qualified_manifest in releases/qualification/current/release.json releases/candidate/release.json; do
if [[ -f "$qualified_manifest" ]]; then
  uv run --frozen python scripts/verify_release_candidate.py "$qualified_manifest" "$check_tmp/contract/contract.json" "$check_tmp/contract/evidence.json" controlplane-events
fi
done
uv venv "$check_tmp/venv" --python 3.13
uv export --frozen --no-emit-project --format requirements-txt --output-file "$check_tmp/requirements.txt" >/dev/null
uv pip sync --python "$check_tmp/venv/bin/python" --require-hashes "$check_tmp/requirements.txt"
uv pip install --python "$check_tmp/venv/bin/python" --no-deps "$check_tmp"/dist/*.whl
"$check_tmp/venv/bin/python" -I "$root/scripts/verify_installed_contract.py" "$check_tmp/contract/contract.json"
cp -R tests "$check_tmp/tests"
(cd "$check_tmp" && "$check_tmp/venv/bin/python" -I -m pytest -c /dev/null tests -q --junitxml="$check_tmp/wheel.xml")
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
(cd "$check_tmp" && "$check_tmp/venv/bin/python" -I -m pytest -c /dev/null tests -q --junitxml="$check_tmp/sdist.xml")

# Retain the exact validated bytes for the release channel; never rebuild them.
if [[ -n "${RELEASE_ARTIFACT_DIR:-}" ]]; then
  mkdir -p "$RELEASE_ARTIFACT_DIR"
  cp "$check_tmp/dist"/* "$RELEASE_ARTIFACT_DIR/"
fi

python3 scripts/verify-test-results.py "$check_tmp/source.xml" "$check_tmp/wheel.xml" "$check_tmp/sdist.xml"
if [[ -n "${TEST_EVIDENCE_DIR:-}" ]]; then
  mkdir -p "$TEST_EVIDENCE_DIR"
  cp "$check_tmp"/*.xml "$TEST_EVIDENCE_DIR/"
fi
