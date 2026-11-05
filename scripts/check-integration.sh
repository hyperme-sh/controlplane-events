#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
unset PYTHONPATH UV_NO_SYNC SKIP_TESTCONTAINERS
docker info >/dev/null
uv sync --frozen --group integration
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
uv run --frozen --group integration pytest -c integration/pytest.ini integration/tests --junitxml="$scratch/source.xml"
uv build --out-dir "$scratch/dist"
uv export --frozen --group integration --no-emit-project --format requirements-txt --output-file "$scratch/requirements.txt" >/dev/null
uv venv --python 3.13 "$scratch/venv"
uv pip sync --python "$scratch/venv/bin/python" "$scratch/requirements.txt"
uv pip install --python "$scratch/venv/bin/python" --no-deps "$scratch"/dist/*.whl
cp -R integration "$scratch/integration"
(cd "$scratch" && "$scratch/venv/bin/python" -I -m pytest -c integration/pytest.ini integration/tests --junitxml="$scratch/wheel.xml")
uv run --frozen --group integration python - "$scratch" <<'PY'
import pathlib, sys, tarfile
root = pathlib.Path(sys.argv[1])
with tarfile.open(next((root / 'dist').glob('*.tar.gz'))) as archive:
    archive.extractall(root / 'sdist', filter='data')
PY
uv build --wheel "$scratch"/sdist/* --out-dir "$scratch/rebuilt"
uv pip install --python "$scratch/venv/bin/python" --no-deps --reinstall "$scratch"/rebuilt/*.whl
(cd "$scratch" && "$scratch/venv/bin/python" -I -m pytest -c integration/pytest.ini integration/tests --junitxml="$scratch/sdist.xml")
python3 scripts/verify-test-results.py "$scratch/source.xml" "$scratch/wheel.xml" "$scratch/sdist.xml"
if [[ -n "${TEST_EVIDENCE_DIR:-}" ]]; then
  mkdir -p "$TEST_EVIDENCE_DIR"
  cp "$scratch"/*.xml "$TEST_EVIDENCE_DIR/"
fi
