"""Offline SQL/transport substitutes for the worker observation contract."""
from contextlib import contextmanager
from uuid import UUID
import pytest
from controlplane_events.canary import Binding,Unavailable,snapshot,probe
from controlplane_events.http_relay import HttpEventSender,TransientDeliveryError

TENANT=UUID('3d38d8c6-ec68-4505-b6ae-d4f6a99723bc')


def binding(**kw):
    return Binding(TENANT,'canary_owner','canary_owner','user-service','user.created.v1','canary_migration',**kw)


class Result:
    def __init__(self,row):self.row=row
    def one(self):return self.row
    def one_or_none(self):return self.row
    def mappings(self):return self


class Engine:
    def __init__(self,rows):self.rows=iter(rows);self.calls=[];self.role=('canary_owner',False,False);self.relation=('canary_owner','canary_migration',True,True,False)
    @contextmanager
    def begin(self):yield self
    def execute(self,sql,params=None):
        query=str(sql);self.calls.append((query,params))
        if 'FROM pg_roles' in query:return Result(self.role)
        if 'FROM pg_class' in query:return Result(self.relation)
        if 'count(*)' in query:return Result(next(self.rows))
        return Result(None)


def row(count=1,oldest=10,newest=10):return dict(pending=count,oldest=oldest,newest=newest)


def test_disabled_binding_does_not_touch_database():
    engine=Engine([])
    with pytest.raises(Unavailable):snapshot(engine,binding())
    assert not engine.calls


def test_preflight_is_read_only_scoped_and_reports_empty_without_fake_age():
    engine=Engine([row(0,None,None)])
    assert snapshot(engine,binding(enabled=True))=={'pending':0,'oldest_age_seconds':None}
    assert engine.calls[0][0]=='SET TRANSACTION READ ONLY'
    query,params=engine.calls[-1]
    assert params=={'tenant':TENANT,'event_type':'user.created.v1'}
    assert not any(word in query for word in ('INSERT','UPDATE','DELETE','FOR UPDATE'))


@pytest.mark.parametrize('value',[row(-1),row(1,None,None),row(1,float('nan'),0),row(1,10,-1),row(0,0,None)])
def test_missing_or_invalid_queue_marks_refuse(value):
    with pytest.raises(Unavailable):snapshot(Engine([value]),binding(enabled=True))


@pytest.mark.parametrize('field,value',[('role',('canary_owner',True,False)),('role',('other',False,False)),
    ('relation',None),('relation',('public','canary_owner',True,True)),('relation',('canary_owner','canary_owner',True,False))])
def test_wrong_owner_or_unforced_rls_refuses(field,value):
    engine=Engine([]);setattr(engine,field,value)
    with pytest.raises(Unavailable):snapshot(engine,binding(enabled=True))


def test_committed_delivery_and_retry_marks():
    sender=HttpEventSender('http://127.0.0.1:8080/events','fixture',timeout=1)
    calls=[]
    def relay(*args,**kw):calls.append(kw);return 1
    result=probe(Engine([row(),row(0,None,None)]),sender,binding(enabled=True),relay=relay)
    assert result['status']=='committed' and result['delivered']==1
    assert calls==[{'limit':1,'event_types':['user.created.v1']}]
    def retry(*args,**kw):raise TransientDeliveryError('fixture lost ack')
    result=probe(Engine([row(),row()]),sender,binding(enabled=True),relay=retry)
    assert result['status']=='retry' and result['retries']==1 and result['delivered']==0


def test_no_samples_or_observation_gap_cannot_pass():
    sender=HttpEventSender('http://127.0.0.1:8080/events','fixture')
    with pytest.raises(Unavailable):probe(Engine([row(0,None,None)]),sender,binding(enabled=True))
    with pytest.raises(Unavailable):probe(Engine([row(),row()]),sender,binding(enabled=True),relay=lambda *a,**k:1)
    with pytest.raises(ValueError):binding(batch_size=True)
    with pytest.raises(ValueError):Binding(TENANT,'owner','owner','user-service','payment.succeeded.v3','migration')


def test_runtime_cannot_own_or_create_schema_and_no_progress_refuses():
    with pytest.raises(ValueError):
        Binding(TENANT,'owner','app','user-service','user.created.v1','owner')
    engine=Engine([])
    engine.relation=('canary_owner','canary_migration',True,True,True)
    with pytest.raises(Unavailable):snapshot(engine,binding(enabled=True))
    sender=HttpEventSender('http://127.0.0.1:8080/events','fixture',timeout=1)
    with pytest.raises(Unavailable):
        probe(Engine([row(),row()]),sender,binding(enabled=True),relay=lambda *a,**k:0)
