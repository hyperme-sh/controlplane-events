"""Run with OWNED_OUTBOX_DATABASE_URL against disposable PostgreSQL only."""
import os
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.outbox import OUTBOX_DDL, enqueue, relay_batch


@pytest.fixture
def database():
    admin = create_engine(os.environ["OWNED_OUTBOX_DATABASE_URL"])
    suffix = uuid.uuid4().hex
    owner, foreign = "owner_" + suffix, "foreign_" + suffix
    with admin.begin() as c:
        for role in (owner, foreign):
            c.exec_driver_sql(f'CREATE ROLE "{role}" NOSUPERUSER NOBYPASSRLS')
            c.exec_driver_sql(f'CREATE SCHEMA "{role}" AUTHORIZATION "{role}"')
        c.exec_driver_sql(f'SET LOCAL ROLE "{owner}"')
        c.exec_driver_sql(f'SET LOCAL search_path TO "{owner}"')
        c.exec_driver_sql(OUTBOX_DDL)
        c.exec_driver_sql('CREATE TABLE obligations (id uuid PRIMARY KEY)')
    engines = []
    def connection_as(role):
        engine = create_engine(os.environ["OWNED_OUTBOX_DATABASE_URL"])
        @event.listens_for(engine, "connect")
        def scope(dbapi_connection, record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f'SET ROLE "{role}"')
                cursor.execute(f'SET search_path TO "{role}"')
            dbapi_connection.commit()
        engines.append(engine)
        return engine
    try:
        yield connection_as(owner), connection_as(foreign), owner
    finally:
        for engine in engines:
            engine.dispose()
        with admin.begin() as c:
            for role in (owner, foreign):
                c.exec_driver_sql(f'DROP SCHEMA "{role}" CASCADE')
                c.exec_driver_sql(f'DROP ROLE "{role}"')
        admin.dispose()


def envelope(tenant):
    return ControlPlaneEventEnvelope(event_type="hypermesh.lease.outcome.v1",
        service="hypermesh-plane", tenant_id=tenant, payload={
            "tenant_id":str(tenant), "lease_id":str(uuid.uuid4()),
            "billing_record_id":str(uuid.uuid4()), "host_org_id":str(uuid.uuid4()),
            "host_wallet":"test-wallet", "kind":"p1_hardware", "outcome":"ended",
            "certification":"passed", "delivered_cents":100,
            "observed_at":datetime.now(timezone.utc).isoformat()})


def scoped(c, tenant):
    c.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid":str(tenant)})


def test_event_failure_rolls_back_domain_write(database):
    owner, _, _ = database
    tid, eid = uuid.uuid4(), uuid.uuid4()
    with owner.begin() as c:
        scoped(c, tid)
        enqueue(c, envelope(tid), event_id=eid)
    with pytest.raises(DBAPIError):
        with owner.begin() as c:
            scoped(c, tid)
            c.execute(text('INSERT INTO obligations VALUES (:id)'), {"id":eid})
            enqueue(c, envelope(tid), event_id=eid)
    with owner.connect() as c:
        assert c.scalar(text('SELECT count(*) FROM obligations')) == 0


def test_failed_delivery_retries_same_identity_and_skips_claimed_rows(database):
    owner, _, _ = database
    tid, eid = uuid.uuid4(), uuid.uuid4()
    with owner.begin() as c:
        scoped(c, tid)
        enqueue(c, envelope(tid), event_id=eid)
    observed=[]
    def fail(event_id, fact):
        observed.append(event_id)
        raise RuntimeError('receiver unavailable')
    with pytest.raises(RuntimeError):
        relay_batch(owner, tid, fail)
    with owner.begin() as locked:
        scoped(locked, tid)
        locked.execute(text('SELECT id FROM service_outbox FOR UPDATE')).all()
        assert relay_batch(owner, tid, lambda *args: pytest.fail('locked row delivered')) == 0
    assert relay_batch(owner, tid, lambda event_id, fact: observed.append(event_id)) == 1
    assert observed == [eid, eid]
    assert relay_batch(owner, tid, lambda *args: pytest.fail('delivered twice')) == 0


def test_role_and_tenant_isolation(database):
    owner, foreign, schema = database
    tid = uuid.uuid4()
    with owner.begin() as c:
        scoped(c, tid)
        enqueue(c, envelope(tid), event_id=uuid.uuid4())
    assert relay_batch(owner, uuid.uuid4(), lambda *args: pytest.fail('foreign tenant')) == 0
    for sql in (f'SELECT * FROM "{schema}".service_outbox',
                f'INSERT INTO "{schema}".obligations VALUES (gen_random_uuid())'):
        with pytest.raises(DBAPIError):
            with foreign.begin() as c:
                scoped(c, tid)
                c.execute(text(sql))
    with pytest.raises(DBAPIError):
        with owner.begin() as c:
            scoped(c, uuid.uuid4())
            enqueue(c, envelope(tid), event_id=uuid.uuid4())


def test_destination_filters_leave_other_routes_pending(database):
    owner, _, _ = database
    tid = uuid.uuid4()
    lease, payment = envelope(tid), ControlPlaneEventEnvelope(
        event_type='payment.obligation.recorded.v1', service='payment-service', tenant_id=tid,
        payload={'tenant_id':str(tid),'lease_id':str(uuid.uuid4()),
                 'billing_record_id':str(uuid.uuid4()),'source_event_id':str(uuid.uuid4()),'net_usdc_atoms':10})
    ids = [uuid.uuid4(), uuid.uuid4()]
    with owner.begin() as c:
        scoped(c, tid)
        for eid, fact in zip(ids, (lease, payment)):
            enqueue(c, fact, event_id=eid)
    seen=[]
    assert relay_batch(owner, tid, lambda eid, _: seen.append(eid),
                       event_types=[lease.event_type]) == 1
    assert seen == ids[:1]
    assert relay_batch(owner, tid, lambda eid, _: seen.append(eid),
                       exclude_event_types=[lease.event_type]) == 1
    assert seen == ids


@pytest.mark.parametrize('filters', [dict(event_types=[]), dict(event_types='type'),
    dict(exclude_event_types=['']), dict(event_types=['x'], exclude_event_types=['x'])])
def test_invalid_route_filters_refuse_before_database_access(filters):
    with pytest.raises(ValueError):
        relay_batch(None, uuid.uuid4(), None, **filters)
