import logging

import aiohttp
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import LOGO_URL
from keyboards.main_menu import get_main_keyboard
from services.user_service import get_or_create_user

logger = logging.getLogger(__name__)
router = Router()

_WELCOME_TEXT = (
    "👋 <b>Welcome to Piatto!</b>\n"
    "🍕 Authentic Italian cuisine delivered to your door\n\n"
    "• 📋 <b>Catalogue</b> — browse our menu\n"
    "• 🛒 <b>Cart</b> — your current order\n"
    "• ✅ <b>Place Order</b> — checkout\n"
    "• 📦 <b>My Orders</b> — order history"
)


async def _fetch_logo() -> bytes | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(LOGO_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.read()
                logger.warning("Failed to fetch Piatto logo: HTTP %s from %s", resp.status, LOGO_URL)
    except Exception as exc:
        logger.warning("Failed to fetch Piatto logo: %s", exc)
    return None


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    cart = data.get("cart", {})
    await state.clear()
    if cart:
        await state.update_data(cart=cart)

    await get_or_create_user(session, message.from_user.id, message.from_user.username)

    if LOGO_URL:
        logo_bytes = await _fetch_logo()
        if logo_bytes:
            try:
                await message.answer_photo(
                    photo=BufferedInputFile(logo_bytes, filename="logo.png"),
                    caption=_WELCOME_TEXT,
                    parse_mode="HTML",
                    reply_markup=get_main_keyboard(),
                )
                return
            except Exception as exc:
                logger.warning("Failed to send Piatto logo: %s", exc)

    await message.answer(_WELCOME_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("No active operation to cancel.")
        return
    data = await state.get_data()
    cart = data.get("cart", {})
    await state.clear()
    if cart:
        await state.update_data(cart=cart)
    await message.answer("❌ Operation cancelled.", reply_markup=get_main_keyboard())


@router.message(Command("restart"))
async def cmd_restart(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🔄 Bot restarted. Let's start fresh!\n\n"
        "Use the menu below to continue.",
        reply_markup=get_main_keyboard(),
    )


@router.message(Command("admin"))
async def cmd_admin_silenced(message: Message) -> None:
    # Non-admin users get no response — admin.router handles the real /admin above this
    pass
