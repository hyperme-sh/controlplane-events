# controlplane-events

## Current version authority

Current development version: `0.1.0`. The services remain before the unreleased
0.24.0 milestone. Earlier 0.24/1.0 preparation and publication notes below are
historical and do not authorize those versions. Existing tags and historical
artifacts remain untouched. Development uses exact source pins and rebuilt
artifacts; no prior release is required. Version changes, tags and publication
require the owner's explicit authorization to the agent.

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

Main validation retains its exact qualified files in Actions artifacts. It does
not create GitHub releases or tags. Historical `test-<commit>` prereleases remain
qualification evidence and do not establish an accepted stable release.

Stable publication requires an explicit `vMAJOR.MINOR.PATCH` tag, the exact
successful main validation artifact, compatible declared contracts and an actual
immutable stable baseline. The publisher independently verifies that evidence;
successful caller jobs alone cannot establish acceptance. Missing evidence and
first-stable bootstrap remain closed. Package qualification, coordinated consumer
acceptance and runtime deployment remain separate.

## Test qualification and stable acceptance

GitHub `test-<commit>` prereleases are development snapshots. Their embedded
package version does not reserve a stable version. Artifact and consumer evidence
lives under `releases/qualification`; the current wheel binding is still checked
by package CI, alongside any future explicit stable candidate. Earlier test
snapshot receipts are preserved there with an explicit non-stable classification.

`releases/accepted` and `SPANOPTICON_RELEASE_BASELINE_SHA` are reserved for an
actual immutable stable GitHub release. The pinned shared guard verifies the live
release channel, stable tag source and asset digests; a test snapshot cannot
become accepted through a provenance label. No stable baseline is recorded until
that release exists. Stable publication remains closed without a valid baseline;
first stable bootstrap and coordinated release qualification remain separate.
No stable version, approval prerequisite or deployment is introduced here.

## License

Copyright 2026 controlplane-events contributors.

This project's code and schemas are licensed under [Apache-2.0](LICENSE).
Separately installed dependencies retain their own licenses. The wheel and
source distribution include the license text and SPDX package metadata.

Development artifact records live under `releases/qualification`: they retain
tested bytes and contracts without reserving a stable version. A test prerelease
is not an accepted stable baseline. Stable promotion still requires independent
live stable-release provenance and an explicit candidate.

The accepted baseline now records the actual immutable `v0.24.0` stable release,
including its release ID, source commit and published asset digests. Historical
test snapshots remain under qualification, including newer development artifacts
at the same embedded package version. They do not replace the released bytes or
reserve another stable version; a future changed stable artifact needs an explicit
new version and candidate. This record does not publish or deploy anything.

### Actions qualification and stable publication

Main merges and workflow completion do not publish GitHub test releases. Exact
qualification files remain in Actions artifacts with finite retention. The pinned
publisher independently verifies a real immutable stable baseline and refuses
qualification snapshots, even if a caller job reports success. Existing valid
stable records are retained; no release, version change, deployment or human
approval prerequisite is introduced by this correction.
