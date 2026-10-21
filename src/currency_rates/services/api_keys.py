import hashlib
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from currency_rates.errors import InvalidApiKeyError
from currency_rates.storage import postgres
from currency_rates.storage.postgres import ApiClient


def hash_api_key(api_key: str) -> str:
    # Keys are 256-bit random tokens, so a plain SHA-256 is enough to make a leaked
    # table useless; slow password hashes are for low-entropy secrets.
    return hashlib.sha256(api_key.encode()).hexdigest()


async def create_api_client(session: AsyncSession, name: str) -> str:
    api_key = secrets.token_urlsafe(32)
    await postgres.add_api_client(session, name, hash_api_key(api_key))
    await session.commit()
    return api_key


async def authenticate(session: AsyncSession, api_key: str) -> ApiClient:
    client = await postgres.find_api_client(session, hash_api_key(api_key))
    if client is None:
        raise InvalidApiKeyError()
    return client
