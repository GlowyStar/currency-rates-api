import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Text, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {str: Text()}


class Currency(Base):
    __tablename__ = "currencies"
    __table_args__ = (CheckConstraint("code ~ '^[A-Z]{3}$'", name="currencies_code_iso"),)

    code: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]


class ApiClient(Base):
    __tablename__ = "api_clients"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    key_hash: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def list_currencies(session: AsyncSession) -> Sequence[Currency]:
    result = await session.scalars(select(Currency).order_by(Currency.code))
    return result.all()


async def get_currency_codes(session: AsyncSession) -> set[str]:
    result = await session.scalars(select(Currency.code))
    return set(result)


async def upsert_currencies(session: AsyncSession, names_by_code: Mapping[str, str]) -> None:
    rows = [{"code": code, "name": name} for code, name in names_by_code.items()]
    statement = insert(Currency).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[Currency.code], set_={"name": statement.excluded.name}
    )
    await session.execute(statement)


async def add_api_client(session: AsyncSession, name: str, key_hash: str) -> ApiClient:
    client = ApiClient(name=name, key_hash=key_hash)
    session.add(client)
    await session.flush()
    return client


async def find_api_client(session: AsyncSession, key_hash: str) -> ApiClient | None:
    return await session.scalar(select(ApiClient).where(ApiClient.key_hash == key_hash))
