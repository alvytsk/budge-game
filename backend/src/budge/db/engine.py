from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def sessionmaker_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: callers read ORM objects after the transaction
    # context exits, and a lazy refresh at that point would be I/O against a
    # transaction that is already gone.
    return async_sessionmaker(engine, expire_on_commit=False)
