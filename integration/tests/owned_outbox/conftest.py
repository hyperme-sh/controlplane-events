"""Keep legacy table creation separate from the scheduled migrated event store."""
import uuid

import psycopg
from psycopg import sql
import pytest
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session", autouse=True)
def owned_database_url(migrated_postgres):
    # Reuse the scheduled container, but never create/drop legacy tables in the
    # database whose events migrations the other cases are validating.
    database = "owned_outbox_" + uuid.uuid4().hex
    source = make_url(migrated_postgres)
    admin_url = source.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            with pytest.MonkeyPatch.context() as patch:
                patch.setenv("OWNED_OUTBOX_DATABASE_URL", source.set(database=database).render_as_string(hide_password=False))
                yield
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
