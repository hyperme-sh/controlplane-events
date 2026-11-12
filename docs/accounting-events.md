# Accounting facts v1

Owner: events #42, based on billing #21 / PR #26. Infra #922 owns scheduled
registration. These schemas do not activate hosted payment or consumer behavior.
Existing payment events remain unchanged for unmigrated consumers.

Four reserved contracts are available: accounting.reserved.v1,
accounting.committed.v1, accounting.released.v1 and usage.recorded.v1. The quantity
is a nonnegative signed-64-bit integer, excluding booleans and floats, in tokens
or milliseconds (the v0 smallest units). New units need an agreed schema version;
unknown units are rejected. Reservation facts carry deltas, not cumulative totals.
Usage is a measurement independent of funding. No currency conversion is implied.

Each payload includes tenant_id, subject_id, resource and an opaque event_id. The
bus uses UUID tenant identifiers; the generic accounting Scope permits other
opaque tenant formats in other transports. The producer supplies one event_id
inside the same transaction as the accounting mutation/outbox write. It survives
retries and republishing. The envelope repeats that ID; validation checks it and
the tenant against the payload. Legacy envelopes omit the optional event_id field.
A bus row ID is transport metadata, not a replacement for the producer ID.

Consumers atomically deduplicate on (tenant, subject, event_id) with their own
projection update. Reusing an ID for different payloads is a conflict, never a
second effect. There is no global or cross-resource ordering guarantee. Consumers
must handle release/usage before reserve delivery and reconcile authoritative
scoped state; they must not infer a positive balance from arrival order. An
accounting commit is not an entitlement. Authorization still requires the owner
entitlement bound to subject, tenant and resource. Event subscriptions are not
permission to access a different scope.

Payloads follow this library's immutable, extra-forbidden versioned schema rules.
Undeclared additions fail on v1 payloads; new payload fields require a new event
version and explicit consumer support. Additive envelope fields are tolerated by
older envelope parsers. No automatic downgrade is permitted. Models, registry and
shared positive vectors are packaged/tested together. Private identity/provider,
fee, tax and payout fields are rejected rather than ignored.

Unit contracts use tests/accounting_vectors.json. The billing #25 nightly owner
harness consumes these models from current main alongside a frozen v1 consumer
fixture to test mixed revisions, duplicates and reordered facts without duplicate
usage effects. Infra #922 owns fixture setup and scheduled execution. Missing
prerequisites must be blocked, never counted as green coverage.
