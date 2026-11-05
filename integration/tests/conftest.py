"""Disposable owner migrations; no production URL or monorepo checkout."""
import docker
import pytest
from controlplane_db import migrations
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope='session')
def require_docker():
    docker.from_env().ping()


@pytest.fixture(scope='session')
def migrated_postgres(require_docker):
    with PostgresContainer(
        'postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416',
        driver='psycopg',
    ) as pg:
        url = pg.get_connection_url()
        migrations.upgrade(url, chains=['events'])
        yield url


@pytest.fixture(scope='session')
def db_async_url(migrated_postgres):
    return migrated_postgres.replace('+psycopg', '+asyncpg')
