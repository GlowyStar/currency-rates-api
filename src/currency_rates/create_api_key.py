import argparse
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from currency_rates.services import api_keys
from currency_rates.settings import Settings
from currency_rates.storage import postgres


async def create_api_key(settings: Settings, client_name: str) -> str:
    engine = create_async_engine(settings.postgres_dsn)
    try:
        async with postgres.create_session_factory(engine)() as session:
            return await api_keys.create_api_client(session, client_name)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an API client and print its key once."
    )
    parser.add_argument("name", help="unique name of the client")
    args = parser.parse_args()
    print(asyncio.run(create_api_key(Settings(), args.name)))


if __name__ == "__main__":
    main()
