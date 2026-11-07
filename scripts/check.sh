#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
unset PYTHONPATH UV_NO_SYNC
python3 scripts/check_open_boundary.py src pyproject.toml
uv sync --frozen
check_tmp=$(mktemp -d)
trap 'rm -rf "$check_tmp"' EXIT
uv run --frozen pytest -q --junitxml="$check_tmp/source.xml"
uv run --frozen pytest -q tests_tools
uv build --no-sources --out-dir "$check_tmp/dist"
uv run --frozen python scripts/verify_wheel.py "$check_tmp/dist"
uv run --frozen python scripts/export_release_contract.py "$check_tmp"/dist/*.whl --output "$check_tmp/contract"
# Historical qualification records bind their own immutable wheel bytes, not
# every future source checkout. Release preparation may explicitly select a
# candidate, which still must match this exact build or fail closed.
if [[ -n "${RELEASE_CANDIDATE_MANIFEST:-}" ]]; then
  uv run --frozen python scripts/verify_release_candidate.py "$RELEASE_CANDIDATE_MANIFEST" "$check_tmp/contract/contract.json" "$check_tmp/contract/evidence.json" controlplane-events
fi
uv venv "$check_tmp/venv" --python 3.13
uv export --frozen --no-emit-project --format requirements-txt --output-file "$check_tmp/requirements.txt" >/dev/null
# Normal resolution retains hash enforcement for every registry and local artifact.
write_installer_input() {
  python3 - "$check_tmp/requirements.txt" "$1" "$check_tmp/install.txt" <<'PYTHON'
import hashlib, pathlib, sys
requirements, wheel, output = map(pathlib.Path, sys.argv[1:])
output.write_text(requirements.read_text() + "\n" + wheel.resolve().as_uri()
                  + " --hash=sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest() + "\n")
PYTHON
}
write_installer_input "$check_tmp"/dist/*.whl
uv pip install --python "$check_tmp/venv/bin/python" --require-hashes -r "$check_tmp/install.txt"
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
write_installer_input "$check_tmp"/rebuilt/*.whl
uv pip install --python "$check_tmp/venv/bin/python" --require-hashes --reinstall -r "$check_tmp/install.txt"
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
