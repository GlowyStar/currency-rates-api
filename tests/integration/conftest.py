import asyncio
import datetime as dt
import os
from collections.abc import AsyncIterator
from pathlib import Path

import clickhouse_connect
import pytest
from alembic import command
from alembic.config import Config
from clickhouse_connect.driver import AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from currency_rates.domain import Rate
from currency_rates.services import api_keys
from currency_rates.settings import Settings
from currency_rates.storage import clickhouse, postgres

from .seed import CURRENCIES, FIRST_DAY, LAST_DAY

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        postgres_dsn=os.environ.get(
            "TEST_POSTGRES_DSN", "postgresql+asyncpg://rates:rates@localhost:5432/rates_test"
        ),
        clickhouse_host=os.environ.get("TEST_CLICKHOUSE_HOST", "localhost"),
        clickhouse_port=int(os.environ.get("TEST_CLICKHOUSE_PORT", "8123")),
        clickhouse_user="rates",
        clickhouse_password="rates",
        clickhouse_database="rates_test",
    )


async def _create_postgres_database(dsn: str) -> None:
    url = make_url(dsn)
    engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        exists = await connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            await connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    await engine.dispose()


@pytest.fixture(scope="session")
async def session_factory(
    settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await _create_postgres_database(settings.postgres_dsn)
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.postgres_dsn)
    # env.py runs its own event loop, so the migration needs a thread without one.
    await asyncio.to_thread(command.upgrade, config, "head")

    engine = create_async_engine(settings.postgres_dsn)
    yield postgres.create_session_factory(engine)
    await engine.dispose()


@pytest.fixture(scope="session")
async def clickhouse_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    admin = await clickhouse_connect.get_async_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )
    await admin.command(f"CREATE DATABASE IF NOT EXISTS {settings.clickhouse_database}")
    await admin.close()

    client = await clickhouse.connect(settings)
    await clickhouse.create_schema(client)
    yield client
    await client.close()


@pytest.fixture(autouse=True)
async def clean_databases(
    session_factory: async_sessionmaker[AsyncSession], clickhouse_client: AsyncClient
) -> None:
    async with session_factory() as session:
        await session.execute(text("TRUNCATE currencies, api_clients RESTART IDENTITY"))
        await session.commit()
    await clickhouse_client.command("TRUNCATE TABLE rates")


@pytest.fixture
async def seeded(
    session_factory: async_sessionmaker[AsyncSession], clickhouse_client: AsyncClient
) -> None:
    """Catalog of four currencies and ten days where USD to EUR goes 0.81, 0.82, ... 0.90."""
    async with session_factory() as session:
        await postgres.upsert_currencies(session, CURRENCIES)
        await session.commit()

    rates = []
    for offset in range((LAST_DAY - FIRST_DAY).days + 1):
        date = FIRST_DAY + dt.timedelta(days=offset)
        rates.append(Rate("USD", "EUR", date, round(0.81 + offset / 100, 2)))
        rates.append(Rate("USD", "GBP", date, 0.75))
    await clickhouse.insert_rates(clickhouse_client, rates)


@pytest.fixture
async def api_key(session_factory: async_sessionmaker[AsyncSession]) -> str:
    async with session_factory() as session:
        return await api_keys.create_api_client(session, "tests")
