import asyncio
import uuid
import pytest
from controlplane_events import http_inbox
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.http_consumer import producer_owns_event
from controlplane_events.inbox import receive


def test_authenticated_producer_cannot_submit_another_owners_event():
    assert producer_owns_event("hypermesh-plane", "hypermesh.lease.outcome.v1")
    assert producer_owns_event("payment-service", "payment.obligation.recorded.v1")
    assert not producer_owns_event("payment-service", "hypermesh.lease.outcome.v1")
    assert not producer_owns_event("hypermesh-plane", "payment.obligation.recorded.v1")
    assert not producer_owns_event("payment-service", "not.a.registered.event")
    assert not producer_owns_event("", "payment.obligation.recorded.v1")


def test_inbox_refuses_another_owners_event_before_insert():
    class Engine:
        def begin(self):
            raise AssertionError("cross-owner event reached the inbox")

    tenant = uuid.uuid4()
    observed = "2026-11-06T00:00:00+00:00"
    envelope = ControlPlaneEventEnvelope(
        event_type="hypermesh.lease.outcome.v1", service="payment-service", tenant_id=tenant,
        payload={"tenant_id": str(tenant), "lease_id": str(uuid.uuid4()),
                 "billing_record_id": str(uuid.uuid4()), "host_org_id": str(uuid.uuid4()),
                 "host_wallet": "wallet", "kind": "p1_hardware", "outcome": "ended",
                 "certification": "passed", "delivered_cents": 100, "observed_at": observed})
    with pytest.raises(ValueError, match="another service"):
        receive(Engine(), uuid.uuid4(), envelope, producer="payment-service")


@pytest.mark.parametrize("limit, pool", [
    (None, {}),
    ("2", {"pool_size": 2, "max_overflow": 0}),
])
def test_http_inbox_honors_the_process_connection_allowance(monkeypatch, tmp_path, limit, pool):
    url = "postgresql+psycopg://archive_runtime@database/archive"
    database_file = tmp_path / "database.url"
    database_file.write_text(url)
    monkeypatch.setenv("DATABASE_URL_FILE", str(database_file))
    monkeypatch.delenv("EVENTS_LEGACY_LOG_FORWARD", raising=False)
    if limit is None:
        monkeypatch.delenv("EVENTS_HTTP_DB_CONNECTION_LIMIT", raising=False)
    else:
        monkeypatch.setenv("EVENTS_HTTP_DB_CONNECTION_LIMIT", limit)

    class Engine:
        disposed = False

        def dispose(self):
            self.disposed = True

    engine = Engine()

    def connect(url_value, **options):
        assert url_value == url
        assert options == {"pool_pre_ping": True, **pool}
        return engine

    monkeypatch.setattr(http_inbox, "create_engine", connect)
    app = http_inbox.create_app(producers={"payment-service": "test-token"})

    async def run():
        async with app.router.lifespan_context(app):
            assert not engine.disposed

    asyncio.run(run())
    assert engine.disposed


@pytest.mark.parametrize("limit", ["", "0", "-2", " 2", "2.0"])
def test_http_inbox_rejects_invalid_connection_allowance(monkeypatch, limit):
    monkeypatch.setenv("EVENTS_HTTP_DB_CONNECTION_LIMIT", limit)
    app = http_inbox.create_app(producers={"payment-service": "test-token"})

    async def run():
        async with app.router.lifespan_context(app):
            pytest.fail("Invalid limit allowed the HTTP inbox to start")

    with pytest.raises(ValueError, match="must be a positive integer"):
        asyncio.run(run())
