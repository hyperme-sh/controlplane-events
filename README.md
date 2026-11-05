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

The event-store and source subscription PostgreSQL suites run on trusted main:

```sh
bash scripts/check-integration.sh
```

This requires Docker and read access to the pinned controlplane-db dependency.
CI uses DEPENDENCY_READ_TOKEN only for this main-only job; pull-request package
checks remain credential-free. All 31 integration cases run against source,
installed wheel and rebuilt sdist with matching JUnit identities and no skips.
Fixtures apply the event owner's migrations to digest-pinned PostgreSQL 16.
Tests cover delivery, idempotency, source subscriptions, migration preservation,
search-path hardening and temporary-table shadowing. No live provider is used.

These suites and the user-created payload regressions were transferred from
Panopticon `d6d9541b72db2eb3f86cb8aedc071794eba116e3`. Fixtures use published
controlplane-db migrations; the function contract checks both current registration
overloads explicitly. Each consuming service's behavior still requires its own
integration suite. Package checks do not
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

## License

Copyright 2026 controlplane-events contributors.

This project's code and schemas are licensed under [Apache-2.0](LICENSE).
Separately installed dependencies retain their own licenses. The wheel and
source distribution include the license text and SPDX package metadata.

Development artifact records live under `releases/qualification`: they retain
tested bytes and contracts without reserving a stable version. A test prerelease
is not an accepted stable baseline. Stable promotion still requires independent
live stable-release provenance and an explicit candidate.
