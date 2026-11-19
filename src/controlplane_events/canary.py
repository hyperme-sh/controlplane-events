"""Inactive, tenant-scoped worker checks for a declared isolated event stream."""
from dataclasses import dataclass
import math
from uuid import UUID
from sqlalchemy import text

from .http_consumer import producer_owns_event
from .http_relay import HttpEventSender, TransientDeliveryError
from .outbox import relay_batch


class Unavailable(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    tenant: UUID
    role: str
    schema: str
    producer: str
    event_type: str
    table_owner: str
    enabled: bool = False
    batch_size: int = 1

    def __post_init__(self):
        if type(self.tenant) is not UUID or not self.tenant.int:
            raise ValueError('Declared synthetic tenant required')
        if not all(isinstance(v,str) and v and len(v)<=63 for v in (self.role,self.schema,self.table_owner)):
            raise ValueError('Declared owner role and schema required')
        if self.role == self.table_owner:
            raise ValueError('Runtime role must differ from migration owner')
        if not producer_owns_event(self.producer,self.event_type):
            raise ValueError('Event type does not belong to the declared producer')
        if type(self.batch_size) is not int or not 1<=self.batch_size<=10:
            raise ValueError('Canary batch must contain one to ten events')


def snapshot(engine, binding):
    """Read only this owner and tenant; no migration, claim, enqueue or deletion."""
    if binding.enabled is not True:
        raise Unavailable('Event canary binding disabled')
    with engine.begin() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        db.execute(text("SET LOCAL statement_timeout = '5s'"))
        db.execute(text("SET LOCAL lock_timeout = '1s'"))
        role=db.execute(text('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
        if tuple(role)!=(binding.role,False,False):
            raise Unavailable('Unqualified owner database role')
        relation=db.execute(text("""SELECT n.nspname,pg_get_userbyid(c.relowner),
            c.relrowsecurity,c.relforcerowsecurity,
            has_schema_privilege(current_user,n.oid,'CREATE') FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid=to_regclass('service_outbox')""")).one_or_none()
        if relation is None or tuple(relation)!=(binding.schema,binding.table_owner,True,True,False):
            raise Unavailable('Unqualified owner outbox')
        db.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"),{'tenant':str(binding.tenant)})
        row=db.execute(text("""SELECT count(*) AS pending,
            EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-min(created_at))) AS oldest,
            EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-max(created_at))) AS newest
            FROM service_outbox WHERE tenant_id=:tenant AND delivered_at IS NULL
            AND envelope->>'event_type'=:event_type"""),
            {'tenant':binding.tenant,'event_type':binding.event_type}).mappings().one()
        count,oldest,newest=row['pending'],row['oldest'],row['newest']
        if type(count) is not int or count<0:raise Unavailable('Invalid pending count')
        if count==0:
            if oldest is not None or newest is not None:raise Unavailable('Invalid empty queue')
            return {'pending':0,'oldest_age_seconds':None}
        if oldest is None or newest is None or not math.isfinite(float(oldest)) or not math.isfinite(float(newest)) or not 0<=newest<=oldest:
            raise Unavailable('Invalid or future queue timestamp')
        return {'pending':count,'oldest_age_seconds':float(oldest)}


def probe(engine, sender, binding, *, relay=relay_batch):
    """One bounded attempt; callers retain observations across restart/rollback.

    The isolated stream must be quiesced against other writers and workers.
    Missing observations and permanent failures propagate, never become a pass.
    """
    if not isinstance(sender,HttpEventSender) or sender.timeout>5:
        raise ValueError('Bounded owner HTTP sender required')
    before=snapshot(engine,binding)
    if before['pending']==0:raise Unavailable('No declared canary sample')
    retry=False
    try:
        delivered=relay(engine,binding.tenant,sender,limit=binding.batch_size,event_types=[binding.event_type])
    except TransientDeliveryError:
        retry=True;delivered=0
    after=snapshot(engine,binding)
    if type(delivered) is not int or not 0<=delivered<=binding.batch_size or before['pending']-after['pending']!=delivered:
        raise Unavailable('Queue observations disagree with the committed batch')
    if not retry and delivered == 0:
        raise Unavailable('No delivery progress on the declared stream')
    return {'status':'retry' if retry else 'committed','attempts':1,'retries':int(retry),
            'delivered':delivered,'before':before,'after':after}
