"""Neutral accounting facts. They never authorize unrelated resources."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

OpaqueId = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$', max_length=128)]
TenantId = Annotated[str, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', max_length=36)]


class AccountingQuantity(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    amount: int = Field(ge=0, le=2**63-1)
    unit: Literal['tokens','milliseconds']


class _AccountingPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    event_id: OpaqueId
    tenant_id: TenantId
    subject_id: OpaqueId
    resource: OpaqueId
    quantity: AccountingQuantity

    def to_envelope_payload(self) -> dict[str, object]:
        return self.model_dump(mode='json')


class AccountingReservedPayload(_AccountingPayload):
    quote_id: OpaqueId
    reservation_id: OpaqueId


class AccountingCommittedPayload(_AccountingPayload):
    reservation_id: OpaqueId


class AccountingReleasedPayload(_AccountingPayload):
    reservation_id: OpaqueId


class UsageRecordedPayload(_AccountingPayload):
    usage_id: OpaqueId
    meter: OpaqueId


ACCOUNTING_MODELS = {
    'accounting.reserved.v1':AccountingReservedPayload,
    'accounting.committed.v1':AccountingCommittedPayload,
    'accounting.released.v1':AccountingReleasedPayload,
    'usage.recorded.v1':UsageRecordedPayload,
}


def accounting_event_key(envelope):
    """Durable consumer dedup key; process it atomically with any projection write.

    No order is implied. Never derive an entitlement solely from an event.
    Producers retain the original event_id on retries, including republishing.
    """
    from controlplane_events.contracts import validate_event_envelope
    if envelope.event_type not in ACCOUNTING_MODELS:
        raise ValueError('not an accounting event')
    validate_event_envelope(envelope)
    return (str(envelope.tenant_id), envelope.payload['subject_id'], envelope.event_id)


class AccountingQuantityV2(AccountingQuantity):
    """Access counts add a new event version; frozen v1 units stay unchanged."""
    unit: Literal['tokens', 'milliseconds', 'access']


class AccountingReservedPayloadV2(AccountingReservedPayload):
    quantity: AccountingQuantityV2


class AccountingCommittedPayloadV2(AccountingCommittedPayload):
    quantity: AccountingQuantityV2


class AccountingReleasedPayloadV2(AccountingReleasedPayload):
    quantity: AccountingQuantityV2


class UsageRecordedPayloadV2(UsageRecordedPayload):
    quantity: AccountingQuantityV2


ACCOUNTING_MODELS.update({
    'accounting.reserved.v2': AccountingReservedPayloadV2,
    'accounting.committed.v2': AccountingCommittedPayloadV2,
    'accounting.released.v2': AccountingReleasedPayloadV2,
    'usage.recorded.v2': UsageRecordedPayloadV2,
})
