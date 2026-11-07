"""Read-only owner receipt guard before the real events entrypoint."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def verify(url):
    from sqlalchemy import create_engine,inspect,text
    from controlplane_events.owner_schema import COMMON_DDL
    from controlplane_events.store import normalize_sync_url
    baseline_path=Path('/opt/events-owner/owner-migrations/migrations/0001.sql')
    receipt=json.loads(Path('/opt/events-owner/runtime-receipt.json').read_text())
    if hashlib.sha256(baseline_path.read_bytes()).hexdigest()!=receipt['source_resource_sha256']['owner-migrations/migrations/0001.sql']:
        raise ValueError('Events baseline bytes differ from the image receipt')
    baseline=baseline_path.read_text()
    expected=hashlib.sha256((COMMON_DDL+baseline).encode()).hexdigest()
    required=set(re.findall(r'CREATE TABLE\s+(?:IF NOT EXISTS\s+)?"?([a-z_][a-z_0-9]*)',baseline,re.I))
    required|={'owner_schema_version','service_tenants'}
    engine=create_engine(normalize_sync_url(url),connect_args={'connect_timeout':10})
    try:
        with engine.begin() as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            schema=db.scalar(text('SELECT current_schema()'))
            if not schema or schema=='public':raise ValueError('Provision a events-owned schema')
            if db.scalar(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')):
                raise ValueError('Events runtime requires a nonprivileged database role')
            tables=set(inspect(db).get_table_names(schema=schema))
            if required-tables:raise ValueError('Events-owned schema is incomplete')
            mode=os.environ.get('EVENTS_LEGACY_LOG_FORWARD', '0')
            if mode not in ('0', '1'):raise ValueError('Invalid compatibility mode')
            if mode == '1':
                if not db.scalar(text("SELECT has_table_privilege(current_user,'public.controlplane_events','INSERT')")):
                    raise ValueError('Legacy log append authority is missing')
                sequence=db.scalar(text("SELECT pg_get_serial_sequence('public.controlplane_events','id')"))
                if sequence and not db.scalar(text("SELECT has_sequence_privilege(current_user,:seq,'USAGE')"), {'seq':sequence}):
                    raise ValueError('Legacy log sequence authority is missing')
            if db.execute(text('SELECT revision,sha256 FROM owner_schema_version')).all()!=[('0001_owner_schema',expected)] :
                raise ValueError('Events baseline receipt differs from the image')
    finally:engine.dispose()


def main():
    if os.environ.get('DATABASE_URL_FILE'):
        os.environ['DATABASE_URL']=Path(os.environ['DATABASE_URL_FILE']).read_text().strip()
    url=os.environ.get('DATABASE_URL','').strip()
    if not url:raise ValueError('DATABASE_URL is required')
    verify(url)
    os.execvp(sys.argv[1],sys.argv[1:])


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Events owner startup verification failed; details withheld') from None
