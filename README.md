# controlplane-events

Shared typed event envelopes, JSON schemas, validation, publishing and event-store
helpers. The package provides the `controlplane.events.v1` topic contract and the
schema registry under `controlplane_events/schemas/`.

## Development

Requires Python 3.13+ and uv 0.11.26:

```sh
bash scripts/check.sh
```

Checks use locked dependencies and validate the event contracts from source, an
installed wheel in a fresh environment, and a wheel rebuilt from the source
distribution. Both wheels must contain every runtime and schema file with the
same contents as the checkout. Tests do not require a live event bus or database.

CI runs the same checks for pull requests, main and manual runs on isolated
GitHub-hosted Ubuntu workers with read-only contents and pinned actions.

Database-backed event-store/owner-projection integration and each consuming
service's behavior require their own integration suites. Package checks do not
establish database migration or service deployment readiness. Preserve existing
schema identifiers and wire compatibility when changing event definitions.

## Release channels

Successful main-push validation retains the validated Python wheel and source distribution and publishes
an immutable `test-<commit>` GitHub prerelease. Push a `vMAJOR.MINOR.PATCH`
tag at that tested commit to promote the exact files to a stable release,
without rebuilding. Repository visibility applies to all package assets.
Package metadata versions are preserved; these are GitHub distribution snapshots,
not automatic PyPI publication or service deployment.

Promotion requires the original Actions artifact (90-day requested retention;
organization policy may shorten it). Expired evidence fails closed: validate a
new main revision. The publisher verifies the run, source, workflow and artifact
digest independently of release metadata. Repository immutable releases must be
enabled. See the [shared release contract](https://github.com/hyperme-sh/.github/blob/7749591fccd30d46ba9b0adabe82114001c9d0de/actions/release-channel/README.md).

## Artifact-backed release baseline

The initial accepted contract is bound to the real immutable 0.1.0 **test** wheel,
its successful main validation run, and the matching live Actions artifact.
`releases/accepted/provenance.json` records those identities. The independent
baseline reference is `d24ca143a4ccc457a3578076e1d4a16b2d5ea63a`; it does not use
the unqualified `v0.1.0` release as evidence.

The 0.24.0 candidate changes package metadata only. Its complete packaged runtime
and event schemas match the accepted contract byte for byte. Package CI exports
the built wheel's contract and rejects mismatched candidate versions, wheel
hashes, contract paths or contract hashes before retaining publication artifacts.
Fresh and sdist-rebuilt installed wheels also exercise tenant-bound consumer
dispatch, malformed-input refusal and every packaged export/schema. No live
payment, identity, database or deployment operation is part of these checks.

The snapshot intentionally includes every packaged runtime file. It is
conservative and may reject compatible implementation changes; it must not be
silently narrowed to admit a patch. Database-backed event-store/projection and
service deployment acceptance remain separate. After main publishes the actual
0.24 artifact, verify it before advancing the accepted 0.24 baseline; this PR
alone does not claim a production deployment or stable release.
