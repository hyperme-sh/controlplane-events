"""Fresh owner-local schema installation; no foreign migration chains."""
import hashlib
from sqlalchemy import text

COMMON_DDL = """
CREATE TABLE service_tenants (tenant_id uuid PRIMARY KEY);
CREATE FUNCTION remember_service_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS NOT NULL THEN
        INSERT INTO service_tenants VALUES (NEW.tenant_id) ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TABLE owner_schema_version (revision text PRIMARY KEY, sha256 text NOT NULL);
"""


def migrate(engine, *, revision, ddl):
    digest = hashlib.sha256((COMMON_DDL + ddl).encode()).hexdigest()
    with engine.begin() as db:
        role = db.execute(text('''SELECT r.rolsuper,r.rolbypassrls,n.nspowner=r.oid AS owns_schema,n.nspname
            FROM pg_roles r JOIN pg_namespace n ON n.nspname=current_schema()
            WHERE r.rolname=current_user''')).mappings().one()
        if role['rolsuper'] or role['rolbypassrls'] or not role['owns_schema'] or role['nspname']=='public':
            raise ValueError('Owner migrations require a nonprivileged owner schema')
        db.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),0))")
        exists = db.scalar(text("SELECT to_regclass(current_schema() || '.owner_schema_version')"))
        if exists:
            versions = db.execute(text('SELECT revision,sha256 FROM owner_schema_version')).all()
            if versions != [(revision,digest)]:
                raise ValueError('Owner migration identity differs')
            return
        db.exec_driver_sql(COMMON_DDL)
        db.exec_driver_sql(ddl)
        tables = db.execute(text("""SELECT table_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND column_name='tenant_id'
              AND table_name <> 'service_tenants' ORDER BY table_name""")).scalars()
        for table in tables:
            quoted = db.dialect.identifier_preparer.quote(table)
            db.exec_driver_sql(f'CREATE TRIGGER remember_tenant AFTER INSERT OR UPDATE OF tenant_id ON {quoted} '
                               'FOR EACH ROW EXECUTE FUNCTION remember_service_tenant()')
        db.execute(text('INSERT INTO owner_schema_version VALUES (:revision,:digest)'),{'revision':revision,'digest':digest})


def main(package, revision):
    import argparse
    from importlib.resources import files
    from pathlib import Path
    from sqlalchemy import create_engine
    from controlplane_events.store import normalize_sync_url
    parser=argparse.ArgumentParser()
    parser.add_argument('--database-url-file',type=Path,required=True)
    args=parser.parse_args()
    engine=create_engine(normalize_sync_url(args.database_url_file.read_text()))
    try:
        migrate(engine,revision=revision,ddl=files(package).joinpath('migrations/0001.sql').read_text())
    finally:
        engine.dispose()
