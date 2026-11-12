"""Reuse the scheduled suite's disposable database for owner transaction cases."""
import pytest


@pytest.fixture(scope="session", autouse=True)
def owned_database_url(migrated_postgres):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OWNED_OUTBOX_DATABASE_URL", migrated_postgres)
        yield
