import asyncio

from alembic import command
from alembic.config import Config

from currency_rates.settings import Settings
from currency_rates.storage import clickhouse


async def create_clickhouse_schema(settings: Settings) -> None:
    client = await clickhouse.connect(settings)
    try:
        await clickhouse.create_schema(client)
    finally:
        await client.close()


def main() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    asyncio.run(create_clickhouse_schema(Settings()))


if __name__ == "__main__":
    main()
