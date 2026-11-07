"""Owner migration and TEST cleanup against disposable PostgreSQL."""
import os
from pathlib import Path
import uuid
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from controlplane_events.owner_schema import migrate
from controlplane_events.test_cleanup import purge_test_tenant

DDL = '''CREATE TABLE records (id uuid PRIMARY KEY,tenant_id uuid NOT NULL,value text);
ALTER TABLE records ENABLE ROW LEVEL SECURITY;
ALTER TABLE records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON records USING
(tenant_id = nullif(current_setting('app.current_tenant_id',true),'')::uuid)
WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id',true),'')::uuid);
'''

@pytest.fixture(scope='module')
def test_authority():
    admin = create_engine(os.environ['OWNED_OUTBOX_DATABASE_URL'])
    with admin.begin() as db:
        # Refuse a pre-existing marker: these tests must own a fresh fixture.
        db.exec_driver_sql(Path(__file__).with_name('test_authority.sql').read_text())
    try:
        yield
    finally:
        with admin.begin() as db:
            db.exec_driver_sql('DROP SCHEMA test_authority CASCADE')
        admin.dispose()


@pytest.fixture
def owner(test_authority):
    admin = create_engine(os.environ['OWNED_OUTBOX_DATABASE_URL'])
    role='migration_'+uuid.uuid4().hex
    with admin.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS')
        db.exec_driver_sql(f'CREATE SCHEMA {role} AUTHORIZATION {role}')
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA test_authority TO {role}')
        db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION test_authority.require_test_database() TO {role}')
    engine=create_engine(os.environ['OWNED_OUTBOX_DATABASE_URL'])
    @event.listens_for(engine,'connect')
    def scope(connection,_):
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
            cursor.execute(f'SET search_path TO {role}')
        connection.commit()
    try:
        yield engine,admin,role
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.exec_driver_sql(f'DROP SCHEMA {role} CASCADE')
            db.exec_driver_sql(f'DROP OWNED BY {role}')
            db.exec_driver_sql(f'DROP ROLE {role}')
        admin.dispose()


def test_migration_replay_inventory_and_test_cleanup(owner):
    engine,_,_=owner
    migrate(engine,revision='1',ddl=DDL)
    migrate(engine,revision='1',ddl=DDL)
    with pytest.raises(ValueError,match='identity differs'):
        migrate(engine,revision='1',ddl=DDL+'\n')
    first,second=uuid.uuid4(),uuid.uuid4()
    for tenant in (first,second):
        with engine.begin() as db:
            db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(tenant)})
            db.execute(text("INSERT INTO records VALUES (:id,:t,'data')"),{'id':uuid.uuid4(),'t':tenant})
    with engine.connect() as db:
        assert set(db.scalars(text('SELECT tenant_id FROM service_tenants'))) == {first,second}
        assert db.scalar(text('SELECT count(*) FROM records')) == 0
    assert purge_test_tenant(engine,first)['records']==1
    assert purge_test_tenant(engine,first)['records']==0
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(second)})
        assert db.scalar(text('SELECT count(*) FROM records'))==1
    for sql in ('SELECT * FROM test_authority.database_identity',
                'UPDATE test_authority.database_identity SET cluster_identifier=0'):
        with pytest.raises(DBAPIError):
            with engine.begin() as db:db.execute(text(sql))


def test_instance_binding_mismatch_refuses_cleanup(owner):
    engine,admin,_=owner
    migrate(engine,revision='1',ddl=DDL)
    with admin.begin() as db:
        original=db.scalar(text('SELECT cluster_identifier FROM test_authority.database_identity'))
        db.execute(text('UPDATE test_authority.database_identity SET cluster_identifier=0'))
    try:
        with pytest.raises(DBAPIError,match='not the bound TEST'):
            purge_test_tenant(engine,uuid.uuid4())
    finally:
        with admin.begin() as db:
            db.execute(text('UPDATE test_authority.database_identity SET cluster_identifier=:v'),{'v':original})


@pytest.mark.asyncio
async def test_unknown_bearer_is_rejected_before_the_body_is_read():
    from fastapi import HTTPException
    from controlplane_events.http_inbox import MAX_DELIVERY_BYTES, authenticated_delivery, read_limited_body

    class Closed:
        headers = {'authorization': 'Bearer wrong', 'content-length': str(MAX_DELIVERY_BYTES + 1)}
        async def stream(self):
            raise AssertionError('body read before authentication')
            yield b''

    with pytest.raises(HTTPException) as denied:
        await authenticated_delivery(Closed(), {'payment-service': 'test-producer'})
    assert denied.value.status_code == 401

    class Chunked:
        headers = {'authorization': 'Bearer test-producer'}
        async def stream(self):
            yield b'{' * 16
            yield b'x' * MAX_DELIVERY_BYTES

    with pytest.raises(HTTPException) as oversized:
        await read_limited_body(Chunked())
    assert oversized.value.status_code == 413
    with pytest.raises(HTTPException) as rejected:
        await authenticated_delivery(Chunked(), {'payment-service': 'test-producer'})
    assert rejected.value.status_code == 413


def test_http_inbox_auth_replay_and_conflict(owner, monkeypatch):
    import threading
    from datetime import datetime,timezone
    from fastapi.testclient import TestClient
    from controlplane_events import http_inbox
    from controlplane_events.http_inbox import create_app
    from controlplane_events.inbox import INBOX_DDL
    ids={}
    real_receive=http_inbox.receive
    def spy(*args, **kwargs):
        ids['persist']=threading.get_ident()
        return real_receive(*args, **kwargs)
    monkeypatch.setattr(http_inbox, 'receive', spy)
    engine,_,_=owner
    migrate(engine,revision='inbox1',ddl=INBOX_DDL)
    tenant,event_id=uuid.uuid4(),uuid.uuid4()
    body={'event_id':str(event_id),'envelope':{
        'event_type':'payment.obligation.recorded.v1','service':'payment-service',
        'tenant_id':str(tenant),'occurred_at':datetime.now(timezone.utc).isoformat(),
        'payload':{'tenant_id':str(tenant),'lease_id':str(uuid.uuid4()),
                   'billing_record_id':str(uuid.uuid4()),'source_event_id':str(uuid.uuid4()),
                   'net_usdc_atoms':880000}}}
    headers={'Authorization':'Bearer test-producer','Idempotency-Key':str(event_id)}
    app=create_app(engine=engine,producers={'payment-service':'test-producer'})
    @app.get('/loop-thread')
    async def loop_thread():
        ids['loop']=threading.get_ident()
        return {'ok':True}
    with TestClient(app) as client:
        assert client.post('/events',json=body).status_code==401
        first=client.post('/events',json=body,headers=headers)
        assert first.status_code==200,first.text
        assert first.json()['duplicate'] is False
        assert client.get('/loop-thread').status_code==200
        assert ids['persist']!=ids['loop']
        assert client.post('/events',json=body,headers=headers).json()['duplicate'] is True
        body['envelope']['payload']['net_usdc_atoms']+=1
        assert client.post('/events',json=body,headers=headers).status_code==409
        foreign_id=uuid.uuid4()
        observed=datetime.now(timezone.utc).isoformat()
        foreign={'event_id':str(foreign_id),'envelope':{
            'event_type':'hypermesh.lease.outcome.v1','service':'payment-service',
            'tenant_id':str(tenant),'occurred_at':observed,'payload':{
                'tenant_id':str(tenant),'lease_id':str(uuid.uuid4()),
                'billing_record_id':str(uuid.uuid4()),'host_org_id':str(uuid.uuid4()),
                'host_wallet':'wallet','kind':'p1_hardware','outcome':'ended',
                'certification':'passed','delivered_cents':100,'observed_at':observed}}}
        rejected=client.post('/events',json=foreign,headers={
            'Authorization':'Bearer test-producer','Idempotency-Key':str(foreign_id)})
        assert rejected.status_code==403,rejected.text
        from controlplane_events.http_inbox import MAX_DELIVERY_BYTES
        unauthenticated=client.post('/events', content=b'{}', headers={'content-length': str(MAX_DELIVERY_BYTES + 1)})
        assert unauthenticated.status_code==401
        too_large=client.post('/events', content=b'x' * (MAX_DELIVERY_BYTES + 1), headers={'Authorization': 'Bearer test-producer'})
        assert too_large.status_code==413, too_large.text
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(tenant)})
        assert db.scalar(text('SELECT count(*) FROM event_inbox'))==1


@pytest.mark.asyncio
async def test_http_consumer_retries_failure_and_receipts_success(owner):
    from datetime import datetime,timezone
    from pydantic import BaseModel
    from controlplane_events.envelope import ControlPlaneEventEnvelope
    from controlplane_events.http_consumer import HttpRoutingConsumer
    from controlplane_events.inbox import INBOX_DDL
    engine,_,role=owner
    migrate(engine,revision='inbox1',ddl=INBOX_DDL)
    class Payload(BaseModel):
        tenant_id:uuid.UUID
        lease_id:uuid.UUID
        billing_record_id:uuid.UUID
        source_event_id:uuid.UUID
        net_usdc_atoms:int
    calls=[]
    async def handle(envelope,payload,delivery_id):
        calls.append((payload.lease_id,delivery_id))
        if len(calls)==1:raise RuntimeError('transient handler failure')
    consumer=HttpRoutingConsumer(os.environ['OWNED_OUTBOX_DATABASE_URL'],
        {'payment.obligation.recorded.v1':(Payload,handle)})
    @event.listens_for(consumer.engine.sync_engine,'connect')
    def scope(connection,_):
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
            cursor.execute(f'SET search_path TO {role}')
        connection.commit()
    tenant=uuid.uuid4()
    payload=Payload(tenant_id=tenant,lease_id=uuid.uuid4(),billing_record_id=uuid.uuid4(),source_event_id=uuid.uuid4(),net_usdc_atoms=880000)
    envelope=ControlPlaneEventEnvelope(event_type='payment.obligation.recorded.v1',service='payment-service',tenant_id=tenant,payload=payload.model_dump(mode='json'))
    event_id=uuid.uuid4()
    try:
        await consumer.start()
        with pytest.raises(RuntimeError,match='transient'):
            await consumer.receive(event_id,envelope)
        assert await consumer.receive(event_id,envelope)
        assert not await consumer.receive(event_id,envelope)
        assert calls==[(payload.lease_id,event_id),(payload.lease_id,event_id)]
    finally:
        await consumer.stop()


@pytest.mark.asyncio
async def test_reused_event_identity_is_rejected_before_the_handler(owner):
    from pydantic import BaseModel
    from controlplane_events.envelope import ControlPlaneEventEnvelope
    from controlplane_events.http_consumer import HttpRoutingConsumer
    from controlplane_events.inbox import INBOX_DDL
    engine,_,role=owner
    migrate(engine,revision='inbox-identity',ddl=INBOX_DDL)
    class Payload(BaseModel):
        tenant_id:uuid.UUID
        lease_id:uuid.UUID
        billing_record_id:uuid.UUID
        source_event_id:uuid.UUID
        net_usdc_atoms:int
    calls=[]
    async def handle(envelope,payload,delivery_id):
        calls.append(delivery_id)
    consumer=HttpRoutingConsumer(os.environ['OWNED_OUTBOX_DATABASE_URL'],
        {'payment.obligation.recorded.v1':(Payload,handle)})
    @event.listens_for(consumer.engine.sync_engine,'connect')
    def scope(connection,_):
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
            cursor.execute(f'SET search_path TO {role}')
        connection.commit()
    first,second,event_id=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
    def envelope(tenant):
        payload=Payload(tenant_id=tenant,lease_id=uuid.uuid4(),billing_record_id=uuid.uuid4(),
                        source_event_id=uuid.uuid4(),net_usdc_atoms=10)
        return ControlPlaneEventEnvelope(event_type='payment.obligation.recorded.v1',service='payment-service',
                                         tenant_id=tenant,payload=payload.model_dump(mode='json'))
    try:
        await consumer.start()
        assert await consumer.receive(event_id,envelope(first))
        with pytest.raises(ValueError,match='reused'):
            await consumer.receive(event_id,envelope(second))
        assert calls==[event_id]
    finally:
        await consumer.stop()
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(first)})
        assert db.scalar(text('SELECT count(*) FROM event_inbox'))==1
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(second)})
        assert db.scalar(text('SELECT count(*) FROM event_inbox'))==0


@pytest.mark.asyncio
async def test_handler_domain_write_does_not_wait_on_the_receipt(owner):
    import asyncio
    from pydantic import BaseModel
    from controlplane_events.envelope import ControlPlaneEventEnvelope
    from controlplane_events.http_consumer import HttpRoutingConsumer
    from controlplane_events.inbox import INBOX_DDL
    engine,_,role=owner
    migrate(engine,revision='inbox-lock',ddl=INBOX_DDL+DDL)
    class Payload(BaseModel):
        tenant_id:uuid.UUID
        lease_id:uuid.UUID
        billing_record_id:uuid.UUID
        source_event_id:uuid.UUID
        net_usdc_atoms:int
    async def handle(envelope,payload,delivery_id):
        def write():
            with engine.begin() as db:
                db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(envelope.tenant_id)})
                db.execute(text("INSERT INTO records(id,tenant_id,value) VALUES (:id,:t,'domain')"),
                           {'id':uuid.uuid4(),'t':envelope.tenant_id})
        await asyncio.to_thread(write)
    consumer=HttpRoutingConsumer(os.environ['OWNED_OUTBOX_DATABASE_URL'],
        {'payment.obligation.recorded.v1':(Payload,handle)})
    @event.listens_for(consumer.engine.sync_engine,'connect')
    def scope(connection,_):
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
            cursor.execute(f'SET search_path TO {role}')
        connection.commit()
    tenant=uuid.uuid4()
    payload=Payload(tenant_id=tenant,lease_id=uuid.uuid4(),billing_record_id=uuid.uuid4(),
                    source_event_id=uuid.uuid4(),net_usdc_atoms=10)
    envelope=ControlPlaneEventEnvelope(event_type='payment.obligation.recorded.v1',service='payment-service',
                                       tenant_id=tenant,payload=payload.model_dump(mode='json'))
    try:
        await consumer.start()
        assert await asyncio.wait_for(consumer.receive(uuid.uuid4(),envelope),timeout=5)
    finally:
        await consumer.stop()
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(tenant)})
        assert db.scalar(text('SELECT count(*) FROM event_inbox'))==1
        assert db.scalar(text('SELECT count(*) FROM records'))==1


def test_http_consumer_rejects_event_owned_by_another_producer(owner, tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sqlalchemy import event as sa_event
    from controlplane_events.http_consumer import HttpRoutingConsumer, consumer_router
    from controlplane_events.inbox import INBOX_DDL
    from controlplane_events.payloads.lease_outcome import LeaseOutcome
    engine, _, role = owner
    migrate(engine, revision='inbox-auth', ddl=INBOX_DDL)
    tokens = tmp_path / 'tokens.json'
    tokens.write_text(json.dumps({'payment-service': 'pay-token', 'hypermesh-plane': 'plane-token'}))
    monkeypatch.setenv('EVENT_PRODUCER_TOKENS_FILE', str(tokens))
    calls = []

    class Payload(BaseModel):
        tenant_id: uuid.UUID
        lease_id: uuid.UUID
        billing_record_id: uuid.UUID
        source_event_id: uuid.UUID
        net_usdc_atoms: int

    async def handle(envelope, payload, delivery_id):
        calls.append((envelope.event_type, delivery_id))

    consumer = HttpRoutingConsumer(os.environ['OWNED_OUTBOX_DATABASE_URL'], {
        'payment.obligation.recorded.v1': (Payload, handle),
        'hypermesh.lease.outcome.v1': (LeaseOutcome, handle),
    })
    @sa_event.listens_for(consumer.engine.sync_engine, 'connect')
    def scope(connection, _):
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
            cursor.execute(f'SET search_path TO {role}')
        connection.commit()
    app = FastAPI()
    app.state.event_consumer = consumer
    app.include_router(consumer_router())
    tenant = uuid.uuid4()
    payment_id = uuid.uuid4()
    payment = {'event_id': str(payment_id), 'envelope': {
        'event_type': 'payment.obligation.recorded.v1', 'service': 'payment-service',
        'tenant_id': str(tenant), 'occurred_at': datetime.now(timezone.utc).isoformat(),
        'payload': {'tenant_id': str(tenant), 'lease_id': str(uuid.uuid4()),
                    'billing_record_id': str(uuid.uuid4()), 'source_event_id': str(uuid.uuid4()),
                    'net_usdc_atoms': 10}}}
    foreign_id = uuid.uuid4()
    foreign = {'event_id': str(foreign_id), 'envelope': {
        'event_type': 'hypermesh.lease.outcome.v1', 'service': 'payment-service',
        'tenant_id': str(tenant), 'occurred_at': datetime.now(timezone.utc).isoformat(),
        'payload': {'tenant_id': str(tenant), 'lease_id': str(uuid.uuid4()),
                    'billing_record_id': str(uuid.uuid4()), 'host_org_id': str(uuid.uuid4()),
                    'host_wallet': 'wallet', 'kind': 'p1_hardware', 'outcome': 'ended',
                    'certification': 'passed', 'delivered_cents': 100,
                    'observed_at': datetime.now(timezone.utc).isoformat()}}}
    try:
        with TestClient(app) as client:
            owned = client.post('/internal/events', json=payment, headers={
                'Authorization': 'Bearer pay-token', 'Idempotency-Key': str(payment_id)})
            assert owned.status_code == 200, owned.text
            rejected = client.post('/internal/events', json=foreign, headers={
                'Authorization': 'Bearer pay-token', 'Idempotency-Key': str(foreign_id)})
            assert rejected.status_code == 403, rejected.text
            assert calls == [('payment.obligation.recorded.v1', payment_id)]
            from controlplane_events.http_inbox import MAX_DELIVERY_BYTES
            unauthenticated = client.post('/internal/events', content=b'{}', headers={
                'content-length': str(MAX_DELIVERY_BYTES + 1)})
            assert unauthenticated.status_code == 401
            too_large = client.post('/internal/events', content=b'x' * (MAX_DELIVERY_BYTES + 1), headers={
                'Authorization': 'Bearer pay-token'})
            assert too_large.status_code == 413, too_large.text
            assert calls == [('payment.obligation.recorded.v1', payment_id)]
    finally:
        import asyncio
        asyncio.run(consumer.stop())


def test_cleanup_keeps_rows_committed_after_validation(owner):
    import json
    from sqlalchemy import bindparam, event as sa_event
    from sqlalchemy.dialects.postgresql import JSONB
    engine, admin, role = owner
    migrate(engine, revision='1', ddl=DDL + 'ALTER TABLE records ADD COLUMN metadata jsonb;')
    tenant, safe, live = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"), {'t': str(tenant)})
        db.execute(text('INSERT INTO records(id,tenant_id,metadata) VALUES (:id,:t,:m)')
                   .bindparams(bindparam('m', type_=JSONB)),
                   {'id': safe, 't': tenant, 'm': {'livemode': False}})
    planted = {'done': False}

    def plant(conn, cursor, statement, parameters, context, executemany):
        normalized = statement.lower()
        if planted['done'] or 'delete' not in normalized or 'records' not in normalized:
            return
        planted['done'] = True
        # Superuser session skips the tenant trigger so the insert does not wait
        # on the cleanup transaction's service_tenants row lock.
        other = admin.connect()
        try:
            other.exec_driver_sql("SET session_replication_role = 'replica'")
            other.exec_driver_sql(f'SET ROLE "{role}"')
            other.exec_driver_sql(f'SET search_path TO "{role}"')
            other.commit()
            with other.begin():
                other.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"), {'t': str(tenant)})
                other.execute(text('INSERT INTO records(id,tenant_id,metadata) VALUES (:id,:t,:m)')
                              .bindparams(bindparam('m', type_=JSONB)),
                              {'id': live, 't': tenant, 'm': {'livemode': True}})
        finally:
            other.exec_driver_sql('RESET ROLE')
            other.exec_driver_sql('RESET session_replication_role')
            other.exec_driver_sql('RESET search_path')
            other.commit()
            other.close()

    sa_event.listen(engine, 'before_cursor_execute', plant)
    try:
        deleted = purge_test_tenant(engine, tenant)
    finally:
        sa_event.remove(engine, 'before_cursor_execute', plant)
    assert planted['done']
    assert deleted['records'] == 1
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"), {'t': str(tenant)})
        remaining = db.execute(text('SELECT id, metadata FROM records')).mappings().all()
    assert len(remaining) == 1 and remaining[0]['id'] == live
    metadata = remaining[0]['metadata']
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert metadata['livemode'] is True


def test_test_cleanup_refuses_live_rows_atomically(owner):
    from sqlalchemy import bindparam
    from sqlalchemy.dialects.postgresql import JSONB
    engine,_,_=owner
    migrate(engine,revision='1',ddl=DDL+'ALTER TABLE records ADD COLUMN metadata jsonb;')
    tenant=uuid.uuid4()
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(tenant)})
        db.execute(text('INSERT INTO records(id,tenant_id,metadata) VALUES (:id,:t,:m)').bindparams(bindparam('m',type_=JSONB)),
                   [{'id':uuid.uuid4(),'t':tenant,'m':{'livemode':False,'financial_hold':True}},
                    {'id':uuid.uuid4(),'t':tenant,'m':{'livemode':True}}])
    with pytest.raises(ValueError,match='Live or unresolved'):
        purge_test_tenant(engine,tenant)
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"),{'t':str(tenant)})
        assert db.scalar(text('SELECT count(*) FROM records'))==2
        db.exec_driver_sql("UPDATE records SET metadata=jsonb_build_object('network','mainnet')")
    with pytest.raises(ValueError,match='Live or unknown'):
        purge_test_tenant(engine,tenant)


def test_inbox_forward_is_atomic_and_exactly_once(owner):
    from controlplane_events.inbox import INBOX_DDL, receive
    from controlplane_events.envelope import ControlPlaneEventEnvelope
    engine, _, _ = owner
    migrate(engine, revision='forward', ddl=INBOX_DDL+'CREATE TABLE forwarded (id uuid PRIMARY KEY);')
    tid, eid = uuid.uuid4(), uuid.uuid4()
    fact=ControlPlaneEventEnvelope(event_type='payment.obligation.recorded.v1',
        service='payment-service', tenant_id=tid, payload={'tenant_id':str(tid),
        'lease_id':str(uuid.uuid4()),'billing_record_id':str(uuid.uuid4()),
        'source_event_id':str(uuid.uuid4()),'net_usdc_atoms':10})
    def append(db, event_id, envelope):
        db.execute(text('INSERT INTO forwarded VALUES (:id)'), {'id':event_id})
    def fail(db, event_id, envelope):
        append(db, event_id, envelope)
        raise RuntimeError('destination unavailable')
    with pytest.raises(RuntimeError):
        receive(engine, eid, fact, producer='payment-service', forward=fail)
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id',:t,true)"), {'t':str(tid)})
        assert db.scalar(text('SELECT count(*) FROM event_inbox')) == 0
        assert db.scalar(text('SELECT count(*) FROM forwarded')) == 0
    assert receive(engine, eid, fact, producer='payment-service', forward=append)
    assert not receive(engine, eid, fact, producer='payment-service', forward=append)
    changed=fact.model_copy(update={'payload':dict(fact.payload, net_usdc_atoms=11)})
    with pytest.raises(ValueError, match='different facts'):
        receive(engine, eid, changed, producer='payment-service', forward=append)
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM forwarded')) == 1


def test_http_inbox_commits_configured_forward_once(owner):
    from fastapi.testclient import TestClient
    from controlplane_events.http_inbox import create_app
    from controlplane_events.inbox import INBOX_DDL
    from controlplane_events.envelope import ControlPlaneEventEnvelope
    engine, _, _ = owner
    migrate(engine, revision='http-forward', ddl=INBOX_DDL+'CREATE TABLE forwarded (id uuid PRIMARY KEY);')
    tid, eid = uuid.uuid4(), uuid.uuid4()
    fact=ControlPlaneEventEnvelope(event_type='payment.obligation.recorded.v1', service='payment-service',
        tenant_id=tid, payload={'tenant_id':str(tid),'lease_id':str(uuid.uuid4()),
        'billing_record_id':str(uuid.uuid4()),'source_event_id':str(uuid.uuid4()),'net_usdc_atoms':10})
    def append(db, event_id, envelope):
        db.execute(text('INSERT INTO forwarded VALUES (:id)'), {'id':event_id})
    app=create_app(engine=engine, producers={'payment-service':'fixture'}, forward=append)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer fixture','Idempotency-Key':str(eid)}
        body={'event_id':str(eid),'envelope':fact.model_dump(mode='json')}
        assert client.post('/events', json=body, headers=headers).status_code == 200
        assert client.post('/events', json=body, headers=headers).json()['duplicate']
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM forwarded')) == 1
