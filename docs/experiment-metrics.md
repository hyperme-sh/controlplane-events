# Named experiment metrics

`experiment.conversion.v2` adds a required `conversion_key`. The v1 schema
and model stay unchanged: v1 is a legacy single conversion, never an inferred
named metric. Exposure remains `experiment.exposure.v1`.

Each destination advertises its accepted event types in trusted producer
configuration. Default an absent advertisement to an empty set. Stage v2
consumer handlers and metric configuration first, then add v2 to that
advertisement. A package upgrade alone does not advertise handler support.
Use `named_conversion_payload` with the destination's advertised types and the
experiment's configured metric allowlist before sending or counting a named
conversion. Unsupported destinations are skipped or queued for later delivery;
never strip the metric and relabel the event as v1. Retain v1 handlers during
adoption. Each destination can update independently.

The typed model and JSON schema enforce the wire shape; the trusted
configuration supplies the dynamic allowlist. Consumers also call
`require_metric` before counting. A syntactically valid metric is not by itself
permission to count it. Do not accept allowlists or capability advertisements
from an event body or browser request.

Create one UUID and one envelope per action and persist both before sending to
any telemetry sink. Reuse the outer delivery `event_id`, the envelope timestamp,
and the complete payload on every retry and on both sinks. `delivery_body`
serializes this existing transport identity; no inner envelope or v1 field is
added. The existing inbox accepts an identical repeated ID only once and
rejects reuse with different facts. Other sinks must deduplicate on the same
ID. Calling a publisher anew for each retry is not a retry of the same action.

An active experiment retains its persisted assignment hash and variant through
sign-in and hash-key rotation. Keep the assignment identifier and hash-key
version in the producer's private assignment store. Use a new identity or hash
key only for a new experiment. `hash_experiment_assignment` remains unchanged;
recomputing with a different key produces a different hash and must not replace
an active assignment. Retention and deletion rules still apply.

Payloads contain only configured labels and the pseudonymous assignment hash.
Raw identities, cookies, addresses, demographic data, customer text and arbitrary
metadata are rejected. The existing registered producer ownership checks remain
in force; this contract does not grant another service publishing authority.
