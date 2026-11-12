"""Reuse the scheduled suite's disposable database for owner transaction cases."""
import pytest


@pytest.fixture(autouse=True)
def owned_database_url(migrated_postgres, monkeypatch):
    monkeypatch.setenv("OWNED_OUTBOX_DATABASE_URL", migrated_postgres)
