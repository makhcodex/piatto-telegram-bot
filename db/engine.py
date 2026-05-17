from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import DATABASE_URL


def _make_engine():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Check your .env file.")
    return create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)


# Lazy singletons — created on first access so import-time errors are clear
_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory
