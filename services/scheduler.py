import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import ADMIN_ID
from db.engine import get_session_factory
from db.models import OrderStatus

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized. Call create_scheduler() first.")
    return _scheduler


def create_scheduler(bot: Bot) -> AsyncIOScheduler:
    global _scheduler
    _scheduler = AsyncIOScheduler()
    return _scheduler


async def _send_payment_reminder(order_id: int, bot: Bot) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        from services.order_service import get_order_with_user
        order = await get_order_with_user(session, order_id)
        if not order or order.status != OrderStatus.PENDING:
            logger.info(
                "SCHEDULER: reminder skipped for order #%d (status=%s)",
                order_id, order.status if order else "not found",
            )
            return
        try:
            await bot.send_message(
                order.user.telegram_id,
                f"⚠️ Reminder: your order #{order_id} is still awaiting payment.\n"
                f"You have 10 minutes left before it is automatically cancelled.",
            )
            logger.info("SCHEDULER: sent 10-min payment reminder for order #%d", order_id)
        except Exception as exc:
            logger.error("SCHEDULER: reminder failed for order #%d: %s", order_id, exc)


async def _auto_cancel_order(order_id: int, bot: Bot) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        from services.order_service import get_order_with_user
        order = await get_order_with_user(session, order_id)
        if not order or order.status != OrderStatus.PENDING:
            logger.info(
                "SCHEDULER: auto-cancel skipped for order #%d (status=%s)",
                order_id, order.status if order else "not found",
            )
            return
        order.status = OrderStatus.CANCELLED_UNPAID
        await session.commit()
        try:
            await bot.send_message(
                order.user.telegram_id,
                f"❌ Your order #{order_id} has been automatically cancelled "
                f"due to non-payment within 20 minutes.",
            )
            await bot.send_message(
                ADMIN_ID,
                f"⏱ Order #{order_id} was automatically cancelled (unpaid after 20 minutes)",
            )
            logger.warning("AUTO-CANCEL: order #%d cancelled (unpaid 20 min)", order_id)
        except Exception as exc:
            logger.error("AUTO-CANCEL: notification failed for order #%d: %s", order_id, exc)


def schedule_order_jobs(order_id: int, bot: Bot) -> tuple[str, str]:
    """Schedule 10-min reminder and 20-min auto-cancel for a new order.
    Returns (reminder_job_id, cancel_job_id).
    """
    scheduler = get_scheduler()
    now = datetime.now(timezone.utc)
    reminder_job_id = f"remind_{order_id}"
    cancel_job_id   = f"cancel_{order_id}"

    scheduler.add_job(
        _send_payment_reminder,
        trigger="date",
        run_date=now + timedelta(minutes=10),
        kwargs={"order_id": order_id, "bot": bot},
        id=reminder_job_id,
        replace_existing=True,
    )
    scheduler.add_job(
        _auto_cancel_order,
        trigger="date",
        run_date=now + timedelta(minutes=20),
        kwargs={"order_id": order_id, "bot": bot},
        id=cancel_job_id,
        replace_existing=True,
    )
    logger.info(
        "SCHEDULER: jobs scheduled for order #%d — reminder=%s, cancel=%s",
        order_id, reminder_job_id, cancel_job_id,
    )
    return reminder_job_id, cancel_job_id


def cancel_order_jobs(reminder_job_id: str | None, cancel_job_id: str | None) -> None:
    """Remove the reminder and auto-cancel jobs for a paid/deleted order."""
    try:
        scheduler = get_scheduler()
    except RuntimeError:
        return
    for job_id in (reminder_job_id, cancel_job_id):
        if job_id:
            try:
                scheduler.remove_job(job_id)
                logger.info("SCHEDULER: removed job %s", job_id)
            except Exception:
                pass  # job already fired or was never added
