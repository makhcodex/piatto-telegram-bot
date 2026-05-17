from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from db.engine import get_session_factory


class DatabaseMiddleware(BaseMiddleware):
    """Opens an AsyncSession per update and injects it as data["session"]."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with get_session_factory()() as session:
            data["session"] = session
            return await handler(event, data)
