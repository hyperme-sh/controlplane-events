# Releasing

`controlplane-events` publishes on three paths that stay distinct: Actions artifacts from main validation, prerelease test bundles, and an immutable stable GitHub release. Package qualification, the artifact a consumer binds, and deployment of a running service each have their own record. This repository stores the bytes and the contracts. Deployment of a service that imports the package happens in that service's own release.

Compatibility rules, the candidate manifest, and admission against a baseline are described in [release-policy.md](release-policy.md) and in the [Spanopticon release policy](https://github.com/hyperme-sh/spanopticon/blob/main/docs/releases.md).

## Version

The version embedded in this checkout is `0.1.0`, declared in `pyproject.toml`. [`release-policy.json`](../release-policy.json) sets the release line to `0.1` and names `pyproject.toml` as the version file.

The accepted stable GitHub release is [`v0.24.0`](https://github.com/hyperme-sh/controlplane-events/releases/tag/v0.24.0). Its wheel is `controlplane_events-0.24.0-py3-none-any.whl`. The release id, source commit, and asset digests are recorded in [`releases/accepted/provenance.json`](../releases/accepted/provenance.json). [`releases/accepted/release.json`](../releases/accepted/release.json) binds that release to line `0.24` and to the contract digest for the published bytes.

The `v0.1.0` tag is a prerelease. Its release name records a failed compatibility admission. Integrators take artifacts from `v0.24.0`.

A future stable artifact with different bytes needs a new version and a new candidate. Replacing the bytes already published at an accepted version is rejected.

## Qualification artifacts

[`.github/workflows/check.yml`](../.github/workflows/check.yml) runs `scripts/check.sh` on pull requests, on pushes to `main`, and on manual dispatch. A successful push to `main` uploads the exact wheel and sdist that the job built as the `tested-dist` artifact, retained for 90 days, and uploads the matching JUnit results. Those artifacts are the qualification record for that commit.

Public repositories run the package job, the Postgres job, and the test-bundle, stable-publication, and version-policy jobs on GitHub-hosted runners. A private repository may run those jobs on runners it provides.

A merge to `main` stores the `tested-dist` artifact. Creating a GitHub release or a tag is a later, separate workflow.

## Test snapshots and test bundles

Two prerelease forms exist.

`test-<commit>` tags are development snapshots. Their embedded package version identifies the snapshot contents. The stable version is the one recorded for the accepted GitHub release. Local evidence for those snapshots is kept under [`releases/qualification/`](../releases/qualification/), with a classification that marks them as qualification snapshots. Historical snapshot receipts stay there when newer development artifacts share the same embedded package version.

The coordinated test channel is declared in [`.github/test-channel.json`](../.github/test-channel.json) with `"next": "0.24.0"`. After Component checks succeed for a push to `main`, [`.github/workflows/test-bundles.yml`](../.github/workflows/test-bundles.yml) publishes an immutable prerelease named `0.24.0-test.N` through the pinned bundle publisher. The current bundle is [`0.24.0-test.0`](https://github.com/hyperme-sh/controlplane-events/releases/tag/0.24.0-test.0). Its wheel filename is `controlplane_events-0.1.0-py3-none-any.whl`: the bundle name carries the `0.24.0` train, and the embedded package version stays the checkout version. The publisher checks the workflow run, the source revision, the channel configuration, and the original Actions artifact digest, then attaches those same bytes. Pull-request validation stops at the package checks.

Consumers of a test bundle bind the component commit and the artifact digest in their own manifest. The bundle counter is only the prerelease name. [`releases/accepted/`](../releases/accepted/) changes when a stable release is recorded. A test bundle stays a prerelease for its whole life. Stable publication uses its own tag.

A missing original artifact, or a collision with an existing publication, stops that publication.

## Stable publication

A stable release starts with an explicit tag named `vMAJOR.MINOR.PATCH`. [`.github/workflows/release.yml`](../.github/workflows/release.yml) runs on those tags. It requests admission from the pinned Spanopticon component-release workflow, passing release line `0.1`, the repository variable `SPANOPTICON_RELEASE_BASELINE_SHA`, and the tag name. The variable holds the full commit SHA of the accepted baseline. Admission checks the live release channel, the stable tag's source, and the asset digests. The check accepts a stable tag whose source and assets match that baseline.

When admission succeeds, the pinned release-channel publisher attaches the exact distributions that main validation already produced for the admitted commit. The publisher reads the `tested-dist` artifact from Component checks. Admission is what authorizes that attachment.

`releases/accepted/` and `SPANOPTICON_RELEASE_BASELINE_SHA` refer to that immutable stable release. Admission compares a candidate with the baseline those two records name. Building and testing this repository from source uses the lockfile and the source revision; `scripts/check.sh` runs before any release exists.

The repository variable, the accepted manifest, and the GitHub release are updated together when a new stable release is cut. Existing tags and the qualification snapshots already stored under `releases/qualification/` remain in place.
