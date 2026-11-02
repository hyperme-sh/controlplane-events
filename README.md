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
