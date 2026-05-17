import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from config import BOT_TOKEN
from db.engine import get_engine
from db.init_db import init_db
from db.middleware import DatabaseMiddleware
from handlers import admin, checkout, menu, start
from keyboards.main_menu import get_main_keyboard
from services.scheduler import create_scheduler


def _setup_logging() -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stdout)


async def main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())

    @dp.error()
    async def global_error_handler(event: ErrorEvent) -> None:
        update_id = event.update.update_id if event.update else "?"
        logger.exception(
            "Unhandled error on update %s: %s",
            update_id, event.exception,
            exc_info=event.exception,
        )
        msg = (
            event.update.message
            if event.update and event.update.message
            else None
        )
        if msg:
            try:
                await msg.answer(
                    "⚠️ An unexpected error occurred. Your session is intact — please try again.\n"
                    "If the issue persists, use /start.",
                    reply_markup=get_main_keyboard(),
                )
            except Exception:
                pass

    dp.update.middleware(DatabaseMiddleware())

    # Admin router first — /admin must not be caught by the checkout FSM
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(menu.router)
    dp.include_router(checkout.router)   # catch-all fallback lives here (last)

    scheduler = create_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started.")

    loop = asyncio.get_running_loop()
    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    )

    # SIGTERM (sent by Railway/Docker on shutdown) cancels the polling task cleanly.
    # SIGINT (Ctrl+C locally) is left to asyncio.run's default handler.
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, polling_task.cancel)

    logger.info("Bot is starting (long polling)...")
    try:
        await polling_task
    except asyncio.CancelledError:
        logger.info("Polling cancelled — shutting down.")
    finally:
        scheduler.shutdown(wait=False)
        await get_engine().dispose()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
