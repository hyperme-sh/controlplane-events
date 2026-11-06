"""Native TEST-only deletion in one owner's schema, without external calls.

The database administrator installs the immutable instance binding. Neither an
HTTP parameter nor an environment variable can grant TEST authority.
"""
from __future__ import annotations

import uuid
from sqlalchemy import MetaData, select, text


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
        # Validate the entire bounded batch before the first deletion. Retention
        # and holds are deliberately irrelevant, but live ancestry is not.
        count=0
        for table in metadata.tables.values():
            if 'tenant_id' not in table.c:
                continue
            rows=db.execute(select(table).where(table.c.tenant_id==tenant_id).limit(_MAX_ROWS+1)).mappings()
            for row in rows:
                count+=1
                if count>_MAX_ROWS:
                    raise ValueError('TEST cleanup batch exceeds the bounded row limit')
                _reject_live(dict(row))
        deleted = {}
        for table in reversed(metadata.sorted_tables):
            if 'tenant_id' in table.c:
                deleted[table.name] = db.execute(table.delete().where(table.c.tenant_id == tenant_id)).rowcount
        # The user owner's tenant row is keyed by id rather than tenant_id.
        tenants = metadata.tables.get(f"{role['nspname']}.tenants")
        if tenants is not None:
            deleted['tenants'] = db.execute(tenants.delete().where(tenants.c.id == tenant_id)).rowcount
        return deleted


def main():
    import argparse
    import json
    import os
    from pathlib import Path
    from sqlalchemy import create_engine
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url-file',type=Path,required=True)
    parser.add_argument('--tenant',type=uuid.UUID,required=True)
    args=parser.parse_args()
    if os.environ.get('OWNER_DATA_POLICY') != 'TEST':
        raise RuntimeError('Private cleanup worker requires explicit TEST configuration')
    engine=create_engine(args.database_url_file.read_text().strip())
    try:
        print(json.dumps({'tenant_id':str(args.tenant),'deleted':purge_test_tenant(engine,args.tenant)},sort_keys=True))
    finally:
        engine.dispose()

if __name__=='__main__':
    main()
