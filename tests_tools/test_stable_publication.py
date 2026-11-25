"""Run the stable OCI action using only local GitHub and registry fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/publish-stable-runtime.yml"

MOCK = r'''
import base64
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
repo = os.environ["GITHUB_REPOSITORY"]
registry = "ghcr.io/" + repo + "/runtime"
revision = "a" * 40
wheel = "b" * 64
digest = os.environ["DIGEST"]
version = os.environ["VERSION"]
change = os.environ["CHANGE"]

if name == "gh":
    if args[0] == "run":
        row = {"published": registry + ":test", "revision": revision,
               "registry_digests": [registry + "@" + (digest if change != "digest" else "sha256:" + "d" * 64)]}
        print("package\tPublish\t2026-11-25T16:00:00.000Z " + json.dumps(row))
    elif "/commits/" in args[1]:
        print(revision)
    elif "/contents/" in args[1]:
        manifest = {"line": "0.1", "purpose": "stable-release", "components": {"controlplane-events": {
            "version": version[1:], "artifact": {"kind": "package", "sha256": wheel}}}}
        print(base64.b64encode(json.dumps(manifest).encode()).decode())
    elif "/attempts/" in args[-1]:
        admission = "/71/" in args[-1]
        print(json.dumps({"jobs": [{"id": 73, "conclusion": "success",
              "name": ("unexpected" if change == "admission" else "Admit the exact 0.1 runtime wheel") if admission else "package",
              "steps": [{"name": "Publish qualified current main to moving TEST", "conclusion": "success"}]}]}))
    else:
        admission = args[1].endswith("/71")
        print(json.dumps({"head_sha": "c" * 40 if change == "revision" and not admission else revision,
              "head_branch": version if admission else "main", "event": "push", "run_attempt": 1,
              "path": ".github/workflows/" + ("release-admission.yml" if admission else "ghcr-test.yml"),
              "conclusion": "success", "head_repository": {"full_name": repo}, "repository": {"full_name": repo}}))
elif args[0] == "login":
    assert sys.stdin.read() == os.environ["GH_TOKEN"]
elif args[:2] == ["manifest", "inspect"]:
    if change not in {"existing", "same-existing"}:
        print("manifest unknown", file=sys.stderr)
        sys.exit(1)
elif args[:3] == ["buildx", "imagetools", "create"]:
    assert args[3:] == ["--prefer-index=false", "--tag", registry + ":" + version, registry + "@" + digest]
    Path(os.environ["COPIED"]).touch()
elif args[-1] == "{{json .Image}}":
    print(json.dumps({"config": {"Labels": {"org.opencontainers.image.revision": revision,
           "org.opencontainers.image.version": version[1:],
           "io.hyperme.events.wheel-sha256": "c" * 64 if change == "wheel" else wheel}}}))
else:
    assert args[-1] == "{{json .Manifest}}"
    print(json.dumps({"digest": "sha256:" + "e" * 64 if change == "existing" else digest}))
'''


@pytest.mark.parametrize("change", ["", "same-existing", "admission", "revision", "digest", "wheel", "existing"])
def test_stable_publisher_binds_the_exact_qualified_image(tmp_path, change):
    script = dedent(WORKFLOW.read_text().split("        run: |\n", 1)[1])
    for command in ("gh", "docker"):
        fixture = tmp_path / command
        fixture.write_text(f"#!{sys.executable}\n{MOCK}")
        fixture.chmod(0o700)
    summary, copied = tmp_path / "summary", tmp_path / "copied"
    digest = "sha256:" + "f" * 64
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"], RUNNER_TEMP=str(tmp_path),
               GITHUB_REPOSITORY="hyperme-sh/controlplane-events", GITHUB_ACTOR="fixture",
               GH_TOKEN="test-token", VERSION="v0.1.1", DIGEST=digest, QUALIFICATION_RUN="72", ADMISSION_RUN="71",
               GITHUB_STEP_SUMMARY=str(summary), COPIED=str(copied), CHANGE=change)
    result = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=10)
    if change in {"", "same-existing"}:
        assert result.returncode == 0, result.stderr
        assert json.loads(summary.read_text()) == {"version": "v0.1.1", "revision": "a" * 40, "digest": digest}
        assert copied.exists() is (change == "")
    else:
        assert result.returncode != 0
        assert not copied.exists() and not summary.exists()


@pytest.mark.parametrize("requested,expected", [("v0.1.1", "v0.1.1"), ("0.1.1", "v0.1.1"),
                                                  ("v0.2.1", None), ("refs/heads/main", None)])
def test_manual_admission_resolves_only_the_requested_release_tag(tmp_path, requested, expected):
    workflow = (ROOT / ".github/workflows/release-admission.yml").read_text()
    resolver = workflow.split("- id: tag", 1)[1].split("- uses: actions/checkout", 1)[0]
    script = dedent(resolver.split("run: |\n", 1)[1])
    assert "ref: ${{ steps.tag.outputs.ref || github.sha }}" in workflow
    output = tmp_path / "output"
    result = subprocess.run(["bash", "-c", script], env=dict(os.environ, REQUESTED_VERSION=requested,
                            GITHUB_OUTPUT=str(output)), capture_output=True, text=True, timeout=10)
    if expected:
        assert result.returncode == 0, result.stderr
        assert output.read_text() == f"ref=refs/tags/{expected}\n"
    else:
        assert result.returncode != 0
        assert not output.exists()
