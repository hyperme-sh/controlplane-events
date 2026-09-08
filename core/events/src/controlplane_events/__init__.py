"""Kafka event envelopes and aiokafka helpers for the control plane."""

from controlplane_events.consumer import AioKafkaEventConsumerBase
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.envelope_parse import (
    extract_payload_tenant_id,
    parse_envelope_bytes,
    payload_matches_envelope_tenant,
    tenant_ids_consistent,
)
from controlplane_events.payloads import (
    AclPolicyUpdatedPayload,
    AuthLoginSuccessPayload,
    AuthTokenIssuedPayload,
    GroupMemberAddedPayload,
    GroupMemberRemovedPayload,
    PaymentSucceededPayload,
    UserCreatedEvent,
)
from controlplane_events.producer import AioKafkaEventProducer
from controlplane_events.routing_consumer import (
    RoutingControlPlaneConsumer,
    dispatch_controlplane_envelope,
)
from controlplane_events.topics import CONTROLPLANE_EVENTS_V1

__all__ = [
    "AclPolicyUpdatedPayload",
    "AioKafkaEventConsumerBase",
    "AioKafkaEventProducer",
    "AuthLoginSuccessPayload",
    "AuthTokenIssuedPayload",
    "GroupMemberAddedPayload",
    "GroupMemberRemovedPayload",
    "PaymentSucceededPayload",
    "CONTROLPLANE_EVENTS_V1",
    "ControlPlaneEventEnvelope",
    "RoutingControlPlaneConsumer",
    "dispatch_controlplane_envelope",
    "UserCreatedEvent",
    "extract_payload_tenant_id",
    "parse_envelope_bytes",
    "payload_matches_envelope_tenant",
    "tenant_ids_consistent",
]
