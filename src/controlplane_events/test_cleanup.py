"""Native TEST-only deletion in one owner's schema, without external calls.

The database administrator installs the immutable instance binding. Neither an
HTTP parameter nor an environment variable can grant TEST authority.
"""
from __future__ import annotations

import json
import uuid
from sqlalchemy import MetaData, text


_TEST_NETWORKS = {'test', 'test-network', 'testnet', 'sandbox', 'devnet',
                  'solana-devnet', 'base-sepolia', 'sepolia', '84532', '11155111'}
_MAX_ROWS = 10_000


def _reject_live(value):
    if isinstance(value, dict):
        for key, child in value.items():
            name=str(key).lower()
            if name=='livemode' and child is not False and child is not None:
                raise ValueError('Live or unresolved provider rows cannot be TEST-cleaned')
            if name in {'network','network_id','chain_id'} and child is not None:
                if str(child).lower() not in _TEST_NETWORKS:
                    raise ValueError('Live or unknown networks cannot be TEST-cleaned')
            _reject_live(child)
    elif isinstance(value,(tuple,list)):
        for child in value:_reject_live(child)


def purge_test_tenant(engine, tenant_id: uuid.UUID) -> dict[str, int]:
    tenant_id = uuid.UUID(str(tenant_id))
    with engine.begin() as db:
        db.execute(text('SELECT test_authority.require_test_database()'))
        role = db.execute(text('''SELECT r.rolsuper,r.rolbypassrls,n.nspowner=r.oid AS owns_schema,n.nspname
            FROM pg_roles r JOIN pg_namespace n ON n.nspname=current_schema()
            WHERE r.rolname=current_user''')).mappings().one()
        if role['rolsuper'] or role['rolbypassrls'] or role['nspname']=='public':
            raise ValueError('TEST cleanup requires a nonprivileged owner schema')
        db.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {'tenant':str(tenant_id)})
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))"),
                   {'identity':f"{role['nspname']}:{tenant_id}"})
        metadata = MetaData(schema=role['nspname'])
        metadata.reflect(bind=db, resolve_fks=False)
        # Lock and validate the bounded batch before the first deletion. A later
        # DELETE names those ctids, so a row committed after validation is kept.
        count=0
        planned={}
        for table in metadata.sorted_tables:
            if 'tenant_id' not in table.c:
                continue
            ctids, documents=_lock_rows(db, table, 't.tenant_id = :tenant',
                                        {'tenant': tenant_id}, _MAX_ROWS - count + 1)
            kept=[]
            for ctid, document in zip(ctids, documents):
                count+=1
                if count>_MAX_ROWS:
                    raise ValueError('TEST cleanup batch exceeds the bounded row limit')
                _reject_live(document)
                kept.append(ctid)
            planned[table.key]=(table, kept)
        tenants = metadata.tables.get(f"{role['nspname']}.tenants")
        tenant_ctids=[]
        if tenants is not None:
            tenant_ctids, documents=_lock_rows(db, tenants, 't.id = :tenant', {'tenant': tenant_id}, 2)
            for document in documents:
                _reject_live(document)
        deleted = {}
        for table in reversed(metadata.sorted_tables):
            item=planned.get(table.key)
            if item is not None:
                locked_table, ctids=item
                deleted[locked_table.name]=_delete_ctids(db, locked_table, ctids)
        # The user owner's tenant row is keyed by id rather than tenant_id.
        if tenants is not None:
            deleted['tenants']=_delete_ctids(db, tenants, tenant_ctids)
        return deleted


def _qualified(table, db):
    preparer=db.dialect.identifier_preparer
    name=preparer.quote(table.name)
    if table.schema:
        return preparer.quote_schema(table.schema)+'.'+name
    return name


def _document(value):
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, (bytes, bytearray)):
        return json.loads(value.decode())
    return value


def _lock_rows(db, table, where_sql, params, limit):
    qualified=_qualified(table, db)
    rows=db.execute(text(f'''
        SELECT t.ctid::text AS ctid, to_jsonb(t.*) AS document
        FROM {qualified} AS t
        WHERE {where_sql}
        ORDER BY t.ctid
        LIMIT :limit
        FOR UPDATE OF t
    '''), {**params, 'limit': limit}).mappings().all()
    return [row['ctid'] for row in rows], [_document(row['document']) for row in rows]


def _delete_ctids(db, table, ctids):
    if not ctids:
        return 0
    qualified=_qualified(table, db)
    params={f'c{index}': ctid for index, ctid in enumerate(ctids)}
    values=', '.join(f'CAST(:c{index} AS tid)' for index in range(len(ctids)))
    return db.execute(text(f'DELETE FROM {qualified} WHERE ctid = ANY (ARRAY[{values}])'), params).rowcount


def main():
    import argparse
    import json
    import os
    from pathlib import Path
    from sqlalchemy import create_engine
    from controlplane_events.store import normalize_sync_url
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url-file',type=Path,required=True)
    parser.add_argument('--tenant',type=uuid.UUID,required=True)
    args=parser.parse_args()
    if os.environ.get('OWNER_DATA_POLICY') != 'TEST':
        raise RuntimeError('Private cleanup worker requires explicit TEST configuration')
    engine=create_engine(normalize_sync_url(args.database_url_file.read_text()))
    try:
        print(json.dumps({'tenant_id':str(args.tenant),'deleted':purge_test_tenant(engine,args.tenant)},sort_keys=True))
    finally:
        engine.dispose()

if __name__=='__main__':
    main()
