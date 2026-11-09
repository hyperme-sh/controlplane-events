import pytest
from controlplane_events.store import normalize_sync_url,normalize_async_url,asyncpg_dsn


@pytest.mark.parametrize("raw,expected", [
    ("postgresql://db.internal/app", "postgresql+psycopg://db.internal/app"),
    ("postgres://db.internal/app", "postgresql+psycopg://db.internal/app"),
    ("postgresql+psycopg2://db.internal/app", "postgresql+psycopg://db.internal/app"),
    ("postgresql+asyncpg://db.internal/app", "postgresql+psycopg://db.internal/app"),
    ("  postgresql://db.internal/app\n", "postgresql+psycopg://db.internal/app"),
    ("postgresql+psycopg://db.internal/app", "postgresql+psycopg://db.internal/app"),
])
def test_plain_postgres_url_uses_the_installed_sync_driver(raw, expected):
    assert normalize_sync_url(raw) == expected


def test_explicit_psycopg_async_engine_preserves_libpq_schema_options():
    raw='postgresql+psycopg://runtime@db.internal/app?options=-c%20search_path%3Downer'
    assert normalize_async_url(raw)==raw
    # Low-level asyncpg LISTEN still takes a plain Postgres DSN, preserving the
    # original server options rather than a SQLAlchemy-only driver scheme.
    assert asyncpg_dsn(raw)==raw.replace('postgresql+psycopg://','postgresql://',1)


@pytest.mark.parametrize('raw',('postgresql://db/app','postgres://db/app','postgresql+psycopg2://db/app','postgresql+asyncpg://db/app'))
def test_existing_asyncpg_selection_and_listener_uri_remain_supported(raw):
    assert normalize_async_url(raw)=='postgresql+asyncpg://db/app'
    assert asyncpg_dsn(raw)=='postgresql://db/app'
