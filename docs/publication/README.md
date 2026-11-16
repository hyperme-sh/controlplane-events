# Events publication preparation

The owner remains Apache-2.0, version 0.1.0. Original and rebuilt wheels verify
LICENSE and NOTICE against source. Event contracts and wire bytes are unchanged.

The existing standalone owner image is prepared under
`ghcr.io/hyperme-sh/controlplane-events/runtime` and retains its current entrypoint,
owner migrations and base image. The image carries owner legal texts and installed
dependency license texts indexed by distribution/version and hash. Missing texts
fail preparation. Exact-version psycopg supplements include the GPL text referenced
by its LGPL terms. No third-party source archives are fetched or bundled.

CI scans every final-image layer for hosted implementation and secrets, checks
for source-bundle paths, and verifies installed owner runtime and legal bytes
offline. Existing owner qualification remains in CI; no additional integration
suite or manual run is introduced. Main builds qualify the exact image before publishing the moving GHCR `:test` channel.
Only that channel is enabled; a stale main build skips publication.

Reachable-history review covers fetched origin branches, tags and HEAD, including
bounded ZIP/wheel members, without executing old code. Hidden/deleted refs,
remote LFS bytes and external archives are outside that scan. Findings are review
records, not publication clearance; exact-byte scanner exceptions require review.

GHCR uses moving TEST channels when activated. Integration/e2e remains nightly.
No Actions uploads, visibility changes or history rewrites are introduced.

## Historical content and retained images

Four older payment payload/schema objects retain provider-specific field names.
The exact object and content hashes and their retained disposition are recorded
in `.github/workflows/historical-content-review.json`. CI rejects new or changed
findings. This record does not clear historical content for republication.

Retained-image CI inventories tagged and untagged GHCR versions and inspects
Linux amd64 layers without running old images. It records hosted content, legal
paths, inherited configuration names and source-bundle presence, then scans
secrets. Other platforms, deleted versions and other registries are outside its
scope. An inaccessible package is reported as unavailable, never as empty.
After TEST publication, inventory must succeed and all retained images are
reviewed. No source archives are added and no registry settings are changed.
