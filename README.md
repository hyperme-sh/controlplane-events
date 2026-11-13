# controlplane-events

`controlplane-events` is the shared Python package for domain events on the Hypermesh control plane. It provides the typed `ControlPlaneEventEnvelope`, the JSON Schema registry for `controlplane.events.v1`, payload validation, a Postgres publisher, and helpers for the event log and its delivery rows.

## Why

Control-plane services have to record the same kind of fact: a user was created, a lease started, a payment succeeded, a host finished certification. Each fact needs a tenant, a type, a producer name, and a payload whose bytes other services can validate without importing the producer. This package is that contract. Producers write one row per event. Consumers claim deliveries, check the payload, and dispatch on `event_type`. Table definitions and migrations live in [controlplane-db](https://github.com/hyperme-sh/controlplane-db); this repository owns the envelope, the schemas, and the client that talks to those tables.

## How it works

Cross-service delivery uses each owner's transactional PostgreSQL outbox, an authenticated HTTP relay and a consumer-owned inbox. `controlplane.events.v1` (`CONTROLPLANE_EVENTS_V1`) remains the envelope contract topic. The legacy shared log helpers are compatibility/receiver-side APIs, not an alternative state authority or a new producer path.

`ControlPlaneEventEnvelope` is a Pydantic model. On the wire it carries `schema_version`, `event_type`, `service`, `tenant_id`, `occurred_at`, `correlation_id`, and `payload`. `tenant_id` is null for host-only or platform scope. `transport_headers()` repeats `event_type`, `service`, `schema_version`, and either `tenant_id` or `context=host`, plus `correlation_id` when set. `bus_event_id` is the `controlplane_events.id` of a delivery. The poller sets it. It is excluded from the JSON stored in the row.

Schemas live in `src/controlplane_events/schemas/`. `registry.json` names every event type, its owning service, its schema file, and `existing` or `reserved` status. `controlplane_events.contracts` loads those files from the installed package. `validate_event_payload` checks the payload with JSON Schema draft 2020-12 and format assertions. `validate_event_envelope` also requires the shared topic and, when the payload carries `tenant_id`, requires that value to match the envelope. Unknown event types raise `KeyError`. A subset of event types also has a Pydantic payload model under `controlplane_events.payloads` for constructing and parsing those payloads in process.

The legacy `PostgresEventPublisher` inserts one `controlplane_events` row per `publish()`. It refuses payloads that contain `prompt`, `messages`, `device_secret`, or `enroll_token`. `publish_in_session` and `bind_outbox_session` insert on the caller's transaction and leave the commit to that caller, so a rollback drops the event. A publish with no bound session opens its own transaction on an async SQLAlchemy engine.

`RoutingControlPlaneConsumer` registers a consumer group for its event types, polls `controlplane_event_deliveries`, and wakes on PostgreSQL notifications on the `controlplane_events` channel. Registration with `auto_offset_reset="earliest"` backfills committed rows. A group filter such as `source_service` stays fixed for that group id; a different filter uses a new group id. The consumer claims a row, parses the envelope, validates the payload with the route's Pydantic model, checks that the envelope tenant and the payload tenant agree, and runs the handler. A payload that fails validation, or whose tenant disagrees with the envelope, is logged and skipped, and that delivery is acknowledged. A handler that raises is recorded as a failed attempt, delayed with backoff, and marked `dead` once `attempts` reaches `max_attempts`. `reset_cursor` replays deliveries after a given event id.

Event-store helpers in `controlplane_events.store` insert envelopes, normalize Postgres URLs to the asyncpg driver, prune through the database function `prune_controlplane_events`, and report pending-or-claimed depth plus dead deliveries via `delivery_counts_sync`.

Consumers that must retain invalid deliveries can opt into
`RoutingControlPlaneConsumer(..., strict_delivery=True)`. Invalid envelopes,
unrouted types, invalid typed payloads and tenant mismatches then raise a
sanitized error through the existing retry/dead-letter path instead of being
acknowledged as skipped. The default routing behavior remains unchanged.

## Practical use

The accepted stable package is `controlplane-events` 0.24.0, published as a wheel on the [`v0.24.0`](https://github.com/hyperme-sh/controlplane-events/releases/tag/v0.24.0) release. Python 3.13 or newer is required.

```sh
python3.13 -m pip install \
  https://github.com/hyperme-sh/controlplane-events/releases/download/v0.24.0/controlplane_events-0.24.0-py3-none-any.whl
```

The examples in this README match the source in this repository, whose package version is `0.1.0`. Run them from a checkout so the import matches the files you are reading:

```sh
git clone https://github.com/hyperme-sh/controlplane-events.git
cd controlplane-events
# CI pins uv 0.11.26. The lockfile is resolved for that release.
uv sync --frozen
```

Build a `user.created.v1` envelope and validate it against the bundled schema. This step reads only the installed package:

```python
import uuid

from controlplane_events import UserCreatedEvent
from controlplane_events.contracts import event_schema, validate_event_envelope

tenant_id = uuid.uuid4()
envelope = UserCreatedEvent(
    user_id=uuid.uuid4(),
    email="ada@example.com",
    tenant_id=str(tenant_id),
    display_name="Ada",
).wrap_envelope(service="user-service", tenant_uuid=tenant_id)

validate_event_envelope(envelope)
print(event_schema("user.created.v1")["x-event-type"])
print(envelope.model_dump(mode="json"))
```

Publishing needs a Postgres database whose `events` migrations have been applied from [controlplane-db](https://github.com/hyperme-sh/controlplane-db) (the revision pinned in `pyproject.toml`). Point the publisher at that database:

```python
import asyncio
import uuid

from controlplane_events import PostgresEventPublisher, UserCreatedEvent

DATABASE_URL = "postgresql://controlplane:controlplane@localhost:5432/controlplane"


async def main() -> None:
    tenant_id = uuid.uuid4()
    payload = UserCreatedEvent(
        user_id=uuid.uuid4(),
        email="ada@example.com",
        tenant_id=str(tenant_id),
    ).to_envelope_payload()
    publisher = PostgresEventPublisher(DATABASE_URL, "user-service")
    await publisher.start()
    try:
        await publisher.publish(
            tenant_id=tenant_id,
            event_type="user.created.v1",
            payload=payload,
            correlation_id=str(uuid.uuid4()),
        )
    finally:
        await publisher.stop()


asyncio.run(main())
```

`postgresql://` and `postgres://` URLs are rewritten to the asyncpg SQLAlchemy driver. To keep the event in the same transaction as your own writes, call `publish_in_session` or bind the session:

```python
from controlplane_events import bind_outbox_session

# `session` is the caller's SQLAlchemy Session. `publisher`, `tenant_id`,
# and `payload` are the objects from the publish example above.
with bind_outbox_session(session):
    await publisher.publish(
        tenant_id=tenant_id,
        event_type="user.created.v1",
        payload=payload,
    )
session.commit()
```

A consumer registers the event types it handles. The group id is chosen by the caller and then stays attached to that filter:

```python
import asyncio

from controlplane_events import (
    CONTROLPLANE_EVENTS_V1,
    RoutingControlPlaneConsumer,
    UserCreatedEvent,
)

DATABASE_URL = "postgresql://controlplane:controlplane@localhost:5432/controlplane"


async def on_user_created(envelope, payload: UserCreatedEvent) -> None:
    print(envelope.service, payload.email)


async def main() -> None:
    consumer = RoutingControlPlaneConsumer(
        DATABASE_URL,
        [CONTROLPLANE_EVENTS_V1],
        "example-user-created",
        {"user.created.v1": (UserCreatedEvent, on_user_created)},
    )
    await consumer.start()
    try:
        await asyncio.sleep(30)
    finally:
        await consumer.stop()


asyncio.run(main())
```

Delivery depth visible to the caller's database role. The two numbers are pending-or-claimed deliveries and dead deliveries:

```python
from controlplane_events.store import delivery_counts_sync

pending_or_claimed, dead = delivery_counts_sync(connection)
```

`prune_events(engine, retention="30 days")` calls `prune_controlplane_events` and returns the number of rows removed.

## How Hypermesh uses it

The registry is authoritative for every event, status, owner and subscription.
Plane uses the canonical owner `hypermesh`; `hypermesh-plane` remains an explicit
producer alias for independently updated peers. Wire claims and strict payload
schemas are unchanged. Payment-success currently uses v3; v4 is reserved.
Payout facts travel in `hypermesh.lease.outcome.v1`; there is no separate
payout-due subscription. Owner reads and local transactions decide payment and
lease state; events only wake reconciliation.

<!-- event-registry:start -->
| Event type | Owner | Status | Schema |
| --- | --- | --- | --- |
| `accounting.committed.v1` | `controlplane-accounting` | reserved | `accounting.committed.v1.json` |
| `accounting.committed.v2` | `controlplane-accounting` | reserved | `accounting.committed.v2.json` |
| `accounting.released.v1` | `controlplane-accounting` | reserved | `accounting.released.v1.json` |
| `accounting.released.v2` | `controlplane-accounting` | reserved | `accounting.released.v2.json` |
| `accounting.reserved.v1` | `controlplane-accounting` | reserved | `accounting.reserved.v1.json` |
| `accounting.reserved.v2` | `controlplane-accounting` | reserved | `accounting.reserved.v2.json` |
| `acl.policy.updated.v1` | `acl-service` | existing | `acl.policy.updated.v1.json` |
| `aiuditor.admission.resolved.v1` | `aiuditor` | existing | `aiuditor.admission.resolved.v1.json` |
| `aiuditor.admission.submitted.v1` | `aiuditor` | existing | `aiuditor.admission.submitted.v1.json` |
| `aiuditor.approval.held.v1` | `aiuditor` | existing | `aiuditor.approval.held.v1.json` |
| `aiuditor.approval.resolved.v1` | `aiuditor` | existing | `aiuditor.approval.resolved.v1.json` |
| `aiuditor.catalog.service.allocated.v1` | `aiuditor` | existing | `aiuditor.catalog.service.allocated.v1.json` |
| `aiuditor.catalog.service.created.v1` | `aiuditor` | existing | `aiuditor.catalog.service.created.v1.json` |
| `aiuditor.connection.assigned.v1` | `aiuditor` | existing | `aiuditor.connection.assigned.v1.json` |
| `aiuditor.connection.created.v1` | `aiuditor` | existing | `aiuditor.connection.created.v1.json` |
| `aiuditor.contract.bound.v1` | `aiuditor` | existing | `aiuditor.contract.bound.v1.json` |
| `aiuditor.contract.bound.v2` | `aiuditor` | existing | `aiuditor.contract.bound.v2.json` |
| `aiuditor.contract.created.v1` | `aiuditor` | existing | `aiuditor.contract.created.v1.json` |
| `aiuditor.contract.created.v2` | `aiuditor` | existing | `aiuditor.contract.created.v2.json` |
| `aiuditor.contract.fulfilled.v1` | `aiuditor` | existing | `aiuditor.contract.fulfilled.v1.json` |
| `aiuditor.contract.fulfilled.v2` | `aiuditor` | existing | `aiuditor.contract.fulfilled.v2.json` |
| `aiuditor.contract.signed.v1` | `aiuditor` | existing | `aiuditor.contract.signed.v1.json` |
| `aiuditor.contract.signed.v2` | `aiuditor` | existing | `aiuditor.contract.signed.v2.json` |
| `aiuditor.contract.violated.v1` | `aiuditor` | existing | `aiuditor.contract.violated.v1.json` |
| `aiuditor.envelope.recorded.v1` | `aiuditor` | existing | `aiuditor.envelope.recorded.v1.json` |
| `aiuditor.policy.created.v1` | `aiuditor` | existing | `aiuditor.policy.created.v1.json` |
| `aiuditor.policy.deleted.v1` | `aiuditor` | existing | `aiuditor.policy.deleted.v1.json` |
| `aiuditor.suite.member.recorded.v1` | `aiuditor` | existing | `aiuditor.suite.member.recorded.v1.json` |
| `allocation.ended.v1` | `hypermesh` | reserved | `allocation.ended.v1.json` |
| `allocation.requested.v1` | `hypermesh` | reserved | `allocation.requested.v1.json` |
| `auth.login.success.v1` | `auth-service` | existing | `auth.login.success.v1.json` |
| `auth.token.issued.v1` | `auth-service` | existing | `auth.token.issued.v1.json` |
| `billing.record.created.v2` | `payment-service` | existing | `billing.record.created.v2.json` |
| `budget.allocated.v1` | `payment-service` | existing | `budget.allocated.v1.json` |
| `budget.depleted.v1` | `ai-service` | existing | `budget.depleted.v1.json` |
| `experiment.conversion.v1` | `controlplane-events` | reserved | `experiment.conversion.v1.json` |
| `experiment.conversion.v2` | `controlplane-events` | reserved | `experiment.conversion.v2.json` |
| `experiment.exposure.v1` | `controlplane-events` | reserved | `experiment.exposure.v1.json` |
| `gateway_api_access_v1` | `controlplane-edge` | existing | `gateway_api_access_v1.json` |
| `group.member.added.v1` | `user-service` | existing | `group.member.added.v1.json` |
| `group.member.removed.v1` | `user-service` | existing | `group.member.removed.v1.json` |
| `host.enrolled.v1` | `hypermesh` | reserved | `host.enrolled.v1.json` |
| `hypermesh.cert.path_b.failed.v1` | `hypermesh` | existing | `hypermesh.cert.path_b.failed.v1.json` |
| `hypermesh.cert.path_b.passed.v1` | `hypermesh` | existing | `hypermesh.cert.path_b.passed.v1.json` |
| `hypermesh.device.deenrolled.v1` | `hypermesh` | existing | `hypermesh.device.deenrolled.v1.json` |
| `hypermesh.device.enrolled.v1` | `hypermesh` | existing | `hypermesh.device.enrolled.v1.json` |
| `hypermesh.instance.check.failed.v1` | `hypermesh` | existing | `hypermesh.instance.check.failed.v1.json` |
| `hypermesh.lease.ended.v1` | `hypermesh` | existing | `hypermesh.lease.ended.v1.json` |
| `hypermesh.lease.failed.v1` | `hypermesh` | existing | `hypermesh.lease.failed.v1.json` |
| `hypermesh.lease.outcome.v1` | `hypermesh` | existing | `hypermesh.lease.outcome.v1.json` |
| `hypermesh.lease.started.v1` | `hypermesh` | existing | `hypermesh.lease.started.v1.json` |
| `listing.created.v1` | `marketplace-service` | existing | `listing.created.v1.json` |
| `payment.connect.access.changed.v1` | `payment-service` | existing | `payment.connect.access.changed.v1.json` |
| `payment.obligation.recorded.v1` | `payment-service` | existing | `payment.obligation.recorded.v1.json` |
| `payment.succeeded.v3` | `payment-service` | existing | `payment.succeeded.v3.json` |
| `payment.succeeded.v4` | `payment-service` | reserved | `payment.succeeded.v4.json` |
| `purchase.completed.v1` | `marketplace-service` | existing | `purchase.completed.v1.json` |
| `sociacl.access.decided.v1` | `acl-service` | existing | `sociacl.access.decided.v1.json` |
| `sociacl.recovery.session.started.v1` | `acl-service` | existing | `sociacl.recovery.session.started.v1.json` |
| `sociacl.share.created.v1` | `acl-service` | existing | `sociacl.share.created.v1.json` |
| `sociacl.share.resolved.v1` | `acl-service` | existing | `sociacl.share.resolved.v1.json` |
| `template.sample.created.v1` | `template` | existing | `template.sample.created.v1.json` |
| `tenant.created.v1` | `platform` | existing | `tenant.created.v1.json` |
| `tokens.used.v1` | `ai-service` | existing | `tokens.used.v1.json` |
| `turn.allocate.v1` | `turn` | existing | `turn.allocate.v1.json` |
| `tw.iff.friend_hit.v1` | `tennessee-windage` | existing | `tw.iff.friend_hit.v1.json` |
| `tw.iff.identity.assigned.v1` | `tennessee-windage` | existing | `tw.iff.identity.assigned.v1.json` |
| `tw.mission.started.v1` | `tennessee-windage` | existing | `tw.mission.started.v1.json` |
| `usage.recorded.v1` | `controlplane-accounting` | reserved | `usage.recorded.v1.json` |
| `usage.recorded.v2` | `controlplane-accounting` | reserved | `usage.recorded.v2.json` |
| `user.created.v1` | `user-service` | existing | `user.created.v1.json` |

| Subscription | Consumer | Group | Event types |
| --- | --- | --- | --- |
| `hypermesh.payment.succeeded` | `hypermesh` | `hypermesh-service-controlplane-events` | `payment.succeeded.v3` |
| `payment.hypermesh.lease.outcome` | `payment-service` | `payment-service-lease-outcomes` | `hypermesh.lease.outcome.v1` |
<!-- event-registry:end -->

## Develop and test

Development uses Python 3.13+ and the locked dependencies. CI installs [uv](https://docs.astral.sh/uv/) 0.11.26.

Package checks run from source, from the installed wheel in a fresh environment, and from a wheel rebuilt from the source distribution. Both wheels must contain the same runtime and schema bytes as the checkout. The package check uses only the checkout and the lockfile:

```sh
bash scripts/check.sh
```

The Postgres suites apply the `events` migration chain from the pinned `controlplane-db` revision to disposable PostgreSQL 16. They cover delivery, idempotent claims, source subscriptions, migration preservation, search-path hardening, and temporary-table shadowing, against source, the installed wheel, and the rebuilt sdist. Docker is required:

```sh
bash scripts/check-integration.sh
```

[`.github/workflows/check.yml`](.github/workflows/check.yml) runs the package checks on pull requests, on pushes to `main`, and on manual dispatch. Public repositories run those jobs on GitHub-hosted runners. A private repository may run the same jobs on runners it provides. The Postgres job runs when `main` is validated. A push to `main` keeps the exact tested distributions as the `tested-dist` artifact for 90 days.

Preserve schema identifiers and wire bytes when changing an event that other services already consume. Compatibility rules for a release are in [docs/release-policy.md](docs/release-policy.md).

## Release and versioning

This checkout's package version is `0.1.0` (`pyproject.toml`, release line `0.1`). The accepted stable release is [`v0.24.0`](https://github.com/hyperme-sh/controlplane-events/releases/tag/v0.24.0). Main validation archives the tested wheel and sdist. Test bundles named `0.24.0-test.N` and stable `vMAJOR.MINOR.PATCH` tags are published separately from that archive. The `v0.1.0` tag is an unqualified prerelease from a failed admission. Channel rules, artifact records, and tag meanings are in [Releasing](docs/releasing.md).

## License

Copyright 2026 controlplane-events contributors.

This project's code and schemas are licensed under [Apache-2.0](LICENSE). Separately installed dependencies retain their own licenses. The wheel and source distribution include the license text and SPDX package metadata.

## Service-owned outbox

`outbox.enqueue` uses the caller's transaction. Apply `OUTBOX_DDL` from each
service's own migration chain and connect using that owner's role/search path.
`relay_batch` claims tenant-scoped rows using `FOR UPDATE SKIP LOCKED` and
acknowledges only after the supplied bounded HTTP sender succeeds. Failed sends
roll back acknowledgements; receivers must deduplicate event IDs transactionally.
No foreign database connection or external broker is needed.

`hypermesh.lease.outcome.v1` carries execution and certification facts plus delivered
integer cents. It carries no host entitlement or fee decision. Payment owns those
decisions. The native `integration/tests/owned_outbox` suite proves rollback, retry identity,
concurrent claims, tenant isolation and foreign-schema read/write refusal.


Outbox relays can select repeatable `--event-type` values or exclude repeatable
`--exclude-event-type` values. Only matching pending rows are locked, delivered
and acknowledged; unrelated destinations retain their pending rows. The Python
`relay_batch` API exposes the same optional filters and retains its unfiltered
default.

An events inbox may be constructed with an operator-supplied `forward` callback.
It receives the existing database connection, event UUID and validated envelope
only for a newly inserted receipt. The callback and receipt commit together;
callback failure rolls both back, and identical retries never repeat the callback.
This supports a compatibility append without granting producers SQL access or
letting HTTP requests select a callback. No forwarding is enabled by default.

`billing.record.created.v2` is owned by `payment-service`; its wire payload is
unchanged. Credentials from another producer cannot submit this event.


For events that several owners must receive, repeat `--destination` and
`--token-file` in matching order (at most eight). Each receiver has its own
producer credential. A source outbox row is acknowledged only after every
receiver commits. If a later destination fails, earlier committed receivers see
the identical event UUID and facts on retry and must acknowledge their existing
receipt without repeating domain effects. Changing topology is an explicit
operator action; it does not reset any historical subscription or inbox.

## Experiment events

`experiment.exposure.v1` and `experiment.conversion.v1` are reserved first-party
contracts on `controlplane.events.v1`. They record an actually shown variant or
the conversion defined by that experiment's configuration. Registering these
contracts does not enable experiments or add a consumer subscription.

Use `ExperimentExposurePayload` or `ExperimentConversionPayload` with
`experiment_key`, `variant`, `assignment_hash`, and `surface` (`s3r.ch`,
`hyperme.sh`, or `portal`). `release_tag` is optional. Experiment and variant
labels are lowercase configuration slugs, limited to 64 characters; release
tags are bounded ASCII labels. These fields must never hold customer content.

Create `assignment_hash` using `hash_experiment_assignment(identifier,
secret=server_secret)`. The secret must be at least 32 random bytes, kept only on
the server. This helper uses domain-separated HMAC-SHA256. Keep the secret
stable for the intended assignment window; changing it changes the resulting
hashes. Never send the original assignment cookie, user ID or SEA key. A schema
can check hash format but cannot prove how a caller produced it.

Both the typed models and bundled JSON schemas reject extra fields, including
email, name, wallet, SEA public key, raw user ID, cookie, IP, user agent, date of
birth, age, prompts and message bodies. There is no free-form metadata field.
The publisher validates these two types before persistence even when callers
supply an untyped dictionary. Existing event contracts keep their behavior.

PostgreSQL and owner transaction cases run only in the central infra nightly
`events-postgres` suite through `scripts/check-integration.sh`. This includes
the source, wheel and rebuilt-source runs. PR and main checks retain package
unit/contract validation and the scoped standalone runtime smoke.

Owner transaction cases use a separate empty database inside the nightly
disposable PostgreSQL instance. Their legacy table creation and teardown cannot
modify the migrated event-store database used by the other cases. The harness
drops that database after the cases finish, including on failure.

Neutral accounting v1 quantities remain frozen to tokens and milliseconds.
The parallel `accounting.reserved.v2`, `accounting.committed.v2`,
`accounting.released.v2` and `usage.recorded.v2` payloads also support integer
`access` counts. Publishers keep v1 for existing units and use v2 for access;
consumers explicitly subscribe to the version they understand. No duplicate
v1 approximation or unit conversion is emitted for access. The existing event
identity, tenant binding, strict payload privacy and atomic dedup rules apply.
Organization principals use the same opaque subject field, while private actor
records stay in the serving owner. Events never grant access by themselves.
