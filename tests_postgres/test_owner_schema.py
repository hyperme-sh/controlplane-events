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


def test_http_inbox_auth_replay_and_conflict(owner):
    from datetime import datetime,timezone
    from fastapi.testclient import TestClient
    from controlplane_events.http_inbox import create_app
    from controlplane_events.inbox import INBOX_DDL
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
    with TestClient(create_app(engine=engine,producers={'payment-service':'test-producer'})) as client:
        assert client.post('/events',json=body).status_code==401
        first=client.post('/events',json=body,headers=headers)
        assert first.status_code==200,first.text
        assert first.json()['duplicate'] is False
        assert client.post('/events',json=body,headers=headers).json()['duplicate'] is True
        body['envelope']['payload']['net_usdc_atoms']+=1
        assert client.post('/events',json=body,headers=headers).status_code==409
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
    async def handle(envelope,payload):
        calls.append(payload.lease_id)
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
        assert calls==[payload.lease_id,payload.lease_id]
    finally:
        await consumer.stop()


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
