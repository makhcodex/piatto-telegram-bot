from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(telegram_id=telegram_id, username=username)
        session.add(user)
        await session.flush()
    elif user.username != username:
        user.username = username

    await session.commit()
    await session.refresh(user)
    return user


async def update_user_phone(session: AsyncSession, user: User, phone: str) -> None:
    user.phone = phone
    await session.commit()
