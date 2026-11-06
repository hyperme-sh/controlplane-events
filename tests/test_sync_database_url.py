import pytest
from controlplane_events.store import normalize_sync_url


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
