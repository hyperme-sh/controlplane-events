# Events HTTP inbox reference

The standalone Events runtime accepts authenticated events and stores them in its
owner database. Archive uses this API as the private destination for Payment
relays.

## Routes

| Method and path | Purpose | Successful response |
| --- | --- | --- |
| `GET /health/ready` | Checks that the runtime can query `event_inbox`. This route requires no producer credential. | `200` with `{"ready":true}` |
| `POST /events` | Validates a delivery and commits it to the owner inbox. | `200` with `event_id`, `committed`, and `duplicate` |

Use the receiver's HTTPS origin. Archive makes these routes available inside its
private deployment network. The HTTP inbox has no event-listing or event-reading
endpoint.

## Deliver an event

Supply `Authorization: Bearer <producer-token>` and `Idempotency-Key: <event-id>`.
The receiver's `EVENT_PRODUCER_TOKENS_FILE` holds a JSON map from producer names
to their tokens. The credential for `envelope.service` must match, and that
producer must own the selected event type. The body is JSON, with a maximum of
256 KiB. The only top-level fields are `event_id` and `envelope`.

| Field | Type | Meaning |
| --- | --- | --- |
| `event_id` | UUID | Required delivery ID. Send its canonical UUID string in `Idempotency-Key`. Keep this ID stable across retries. |
| `envelope.event_type` | String | Required registered event name with its version suffix. |
| `envelope.service` | String | Required producer name. Payment uses `payment-service`. |
| `envelope.tenant_id` | UUID | Required for inbox delivery. Scopes storage and lookup to this tenant. |
| `envelope.occurred_at` | Date-time | Time the event happened. Supply it explicitly and preserve it across retries. The envelope defaults to the current time when the field is omitted. |
| `envelope.schema_version` | Integer | Version of the envelope format. Defaults to `1`. |
| `envelope.correlation_id` | String or null | Optional trace correlation. |
| `envelope.event_id` | String or null | Optional producer-supplied event ID. The outer UUID controls delivery deduplication. |
| `envelope.payload` | Object | Facts for the exact schema selected by `event_type`. |

The [event registry](../src/controlplane_events/schemas/registry.json) maps event
names to producer owners and [payload schemas](../src/controlplane_events/schemas/).
For example, this is the shape of a Payment obligation delivery. The UUIDs below
are examples.

```json
{
  "event_id": "00000000-0000-4000-8000-000000000001",
  "envelope": {
    "event_type": "payment.obligation.recorded.v1",
    "service": "payment-service",
    "tenant_id": "00000000-0000-4000-8000-000000000002",
    "occurred_at": "2026-11-25T15:00:00Z",
    "payload": {
      "tenant_id": "00000000-0000-4000-8000-000000000002",
      "lease_id": "00000000-0000-4000-8000-000000000003",
      "billing_record_id": "00000000-0000-4000-8000-000000000004",
      "source_event_id": "00000000-0000-4000-8000-000000000005",
      "net_usdc_atoms": 0
    }
  }
}
```

The [obligation schema](../src/controlplane_events/schemas/payment.obligation.recorded.v1.json)
also defines the USD form. Use `null` for `net_usdc_atoms` and include the integer
`net_cents` and `payout_rail: "usd"` fields for that form.

## Acknowledgment and retries

A new event returns `200` only after the database transaction commits:

```json
{"event_id":"00000000-0000-4000-8000-000000000001","committed":true,"duplicate":false}
```

A retry with the same ID and matching event data returns the same acknowledgment
with `duplicate: true`. Keep the complete envelope stable, including timestamps
and any optional fields. A timeout may happen after the commit, so retry with
the original ID and body.

| Status | Meaning |
| --- | --- |
| `400` | Invalid request shape, invalid `Content-Length`, or the idempotency header does not match the delivery UUID. |
| `401` | Producer token missing, unknown, or bound to a different producer. |
| `403` | The authenticated producer does not own the event type. |
| `409` | The envelope fails the event contract, or the delivery ID already exists with different event data. |
| `413` | The delivery exceeds the body limit. |

## Stored fields

`event_inbox` stores `event_id` as the UUID primary key, `tenant_id` as the UUID
used by PostgreSQL row-level security, and `envelope` as JSONB. `received_at` defaults
to the PostgreSQL transaction timestamp on insert. It records the inbox arrival time.
`envelope.occurred_at` records when the source event happened. A duplicate
acknowledgment leaves the original row intact.

The durable inbox acknowledges its own commit. If the producer fans the event
out to multiple owners, the producer tracks their acknowledgments separately.
