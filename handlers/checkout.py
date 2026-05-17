import logging
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import ADMIN_ID
from db.models import OrderStatus
from keyboards.admin_menu import (
    ConfirmPaymentCb, RejectPaymentCb, confirm_only_kb, confirm_payment_kb, payment_kb,
)
from keyboards.main_menu import MENU_TEXTS, get_main_keyboard
from services.order_service import (
    check_rate_limit, create_order, delete_order, get_order_with_user, update_order_status, validate_cart,
)
from services.scheduler import cancel_order_jobs, schedule_order_jobs
from services.user_service import get_or_create_user, update_user_phone

logger = logging.getLogger(__name__)
router = Router()


class CheckoutStates(StatesGroup):
    waiting_for_name    = State()
    waiting_for_phone   = State()
    waiting_for_address = State()


def _format_cart(cart: dict) -> tuple[str, float]:
    lines, total = [], 0.0
    for item in cart.values():
        sub = item["price"] * item["quantity"]
        total += sub
        lines.append(f"• {item['name']} × {item['quantity']} = {int(sub)}€")
    return "\n".join(lines), total


def _retry_cancel_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💳 Try Again",    callback_data=f"payment_retry:{order_id}")
    b.button(text="❌ Cancel Order", callback_data=f"payment_cancel:{order_id}")
    b.adjust(1)
    return b.as_markup()


# ── Entry: "✅ Place Order" ───────────────────────────────────────────────────

@router.message(F.text == "✅ Place Order")
async def start_checkout(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    cart: dict = data.get("cart", {})

    await state.clear()
    if not cart:
        await state.update_data(cart=cart)
        await message.answer("🛒 Your cart is empty! Add items from the Catalogue first.")
        return

    await state.update_data(cart=cart)

    user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
    can_order, count = await check_rate_limit(session, user.id)
    if not can_order:
        await message.answer(
            f"⏳ You have placed <b>{count} of 5</b> orders in the last hour.\n"
            "Please wait and try again later.",
            parse_mode="HTML",
        )
        return

    cart_text, total = _format_cart(cart)
    logger.info("User %s starting checkout, %d cart items", message.from_user.id, len(cart))
    await message.answer(
        f"📋 <b>Your order:</b>\n{cart_text}\n\n"
        f"💰 <b>Total: {int(total)}€</b>\n\n"
        "Please enter your <b>name</b> (or /cancel to abort):",
        parse_mode="HTML",
    )
    await state.set_state(CheckoutStates.waiting_for_name)
    logger.debug("User %s FSM: → waiting_for_name", message.from_user.id)


# ── Step 1: name ──────────────────────────────────────────────────────────────

@router.message(CheckoutStates.waiting_for_name, ~F.text.in_(MENU_TEXTS))
async def get_name(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Please send your name as text:")
        return

    name = message.text.strip()
    logger.debug("User %s submitted name: %r", message.from_user.id, name)

    if len(name) < 2 or len(name) > 64:
        await message.answer("❌ Name must be between 2 and 64 characters. Please try again:")
        return

    try:
        await state.update_data(customer_name=name)
        await state.set_state(CheckoutStates.waiting_for_phone)
        logger.debug("User %s FSM: waiting_for_name → waiting_for_phone", message.from_user.id)
    except Exception:
        logger.exception("FSM error saving name for user %s", message.from_user.id)
        await message.answer("❌ Failed to save your name. Please try again:")
        return

    await message.answer(
        f"Great, <b>{name}</b>! 👍\n\n"
        "Please enter your <b>phone number</b> (e.g. +7 999 123 45 67):",
        parse_mode="HTML",
    )


# ── Step 2: phone ─────────────────────────────────────────────────────────────

_PHONE_RE = re.compile(r'^\+\d{10,15}$')


def _normalize_phone(raw: str) -> str | None:
    stripped = re.sub(r'[\s\-\(\)]', '', raw.strip())
    return stripped if _PHONE_RE.match(stripped) else None


@router.message(CheckoutStates.waiting_for_phone, ~F.text.in_(MENU_TEXTS))
async def get_phone(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Please send your phone number as text:")
        return

    phone = _normalize_phone(message.text)
    if phone is None:
        await message.answer(
            "❌ Invalid phone number.\n\n"
            "Please use international format starting with <b>+</b>:\n"
            "• <code>+7 999 123 45 67</code>\n"
            "• <code>+380 50 123 45 67</code>\n\n"
            "The number must begin with + followed by 10–15 digits.\n"
            "Please try again:",
            parse_mode="HTML",
        )
        return

    try:
        await state.update_data(customer_phone=phone)
        await state.set_state(CheckoutStates.waiting_for_address)
        logger.debug("User %s FSM: waiting_for_phone → waiting_for_address", message.from_user.id)
    except Exception:
        logger.exception("FSM error saving phone for user %s", message.from_user.id)
        await message.answer("❌ Failed to save your phone. Please re-enter:")
        return

    await message.answer(
        "Please enter your <b>delivery address</b>\n(street, building, apartment, floor):",
        parse_mode="HTML",
    )


# ── Step 3: address → save order ─────────────────────────────────────────────

@router.message(CheckoutStates.waiting_for_address, ~F.text.in_(MENU_TEXTS))
async def get_address(message: Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    if not message.text:
        await message.answer("❌ Please send your address as text:")
        return

    address = message.text.strip()
    if len(address) < 5 or len(address) > 500:
        await message.answer("❌ Address must be between 5 and 500 characters. Please enter your full address:")
        return
    if not any(c.isdigit() for c in address):
        await message.answer(
            "❌ Please include a building or house number in your address.\n"
            "Example: <i>Baker Street 221, apt 5</i>",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    cart: dict  = data.get("cart", {})
    name: str   = data.get("customer_name", "")
    phone: str  = data.get("customer_phone", "")

    if not cart:
        await state.clear()
        await message.answer(
            "🛒 Your cart is empty. Please add items before placing an order.",
            reply_markup=get_main_keyboard(),
        )
        return

    is_valid, updated_cart, messages = await validate_cart(session, cart)
    if not is_valid:
        await state.update_data(cart=updated_cart)
        await state.set_state(None)
        msg_text = "⚠️ <b>Cart changed:</b>\n" + "\n".join(messages) + "\n\nPlease review your cart and try checking out again."
        await message.answer(msg_text, parse_mode="HTML", reply_markup=get_main_keyboard())
        return

    cart = updated_cart
    cart_text, total = _format_cart(cart)

    user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
    await update_user_phone(session, user, phone)

    can_order, count = await check_rate_limit(session, user.id)
    if not can_order:
        await message.answer(
            f"⏳ Rate limit reached ({count}/5 per hour). Please try again later.",
            reply_markup=get_main_keyboard(),
        )
        await state.clear()
        return

    try:
        order = await create_order(session, user.id, address, cart)
    except Exception:
        logger.exception("Order creation failed for user %s", message.from_user.id)
        await message.answer("❌ Failed to save your order. Please try again.")
        return

    # Schedule 10-min reminder and 20-min auto-cancel
    try:
        reminder_job_id, cancel_job_id = schedule_order_jobs(order.id, bot)
        order.reminder_job_id = reminder_job_id
        order.cancel_job_id   = cancel_job_id
        await session.commit()
    except Exception:
        logger.exception("Scheduler failed to create jobs for order #%d", order.id)

    # Clear entire state including cart — order is complete
    await state.clear()
    logger.info(
        "User %s placed order #%d (%.2f€), cart cleared",
        message.from_user.id, order.id, float(total),
    )

    try:
        await message.answer(
            "✅ <b>Order placed!</b>\n\n"
            f"🔖 Order number: <code>#{order.id}</code>\n"
            f"👤 Name: {name}\n"
            f"📞 Phone: {phone}\n"
            f"📍 Address: {address}\n\n"
            f"🛒 <b>Items:</b>\n{cart_text}\n\n"
            f"💰 <b>Total: {int(total)}€</b>",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(),
        )

        await message.answer(
            "💳 <b>Payment</b>\n\n"
            f"Amount to pay: <b>{int(total)}</b>€\n\n"
            "Transfer to card:\n"
            "<code>1234 5678 9012 3456</code>\n"
            "Recipient: <b>Our Restaurant</b>\n\n"
            "Press the button below after paying 👇",
            parse_mode="HTML",
            reply_markup=payment_kb(order.id),
        )
    except Exception:
        logger.exception("Failed to send order confirmation to user %s", message.from_user.id)

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🆕 <b>New order #{order.id}</b>\n\n"
            f"👤 {name} | 📞 {phone}\n"
            f"📍 {address}\n\n"
            f"🛒 {cart_text}\n\n"
            f"💰 <b>Total: {int(total)}€</b>\n"
            f"📊 Status: ⏳ Awaiting Payment\n\n"
            f"🔗 @{message.from_user.username or 'N/A'} "
            f"(ID: <code>{message.from_user.id}</code>)",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Admin notification failed for order #%d", order.id)


# ── Payment flow ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user_paid:"))
async def user_claims_payment(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await get_order_with_user(session, order_id)

    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return
    if order.user.telegram_id != callback.from_user.id:
        await callback.answer("This is not your order.", show_alert=True)
        return
    if order.status != OrderStatus.PENDING:
        await callback.answer("This order is no longer pending.", show_alert=True)
        return

    await callback.answer("✅ Notification sent to the administrator!")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "⏳ Please wait for payment confirmation from the administrator."
    )

    try:
        await bot.send_message(
            ADMIN_ID,
            f"💰 <b>User reported payment for order #{order_id}!</b>\n\n"
            f"👤 {order.user.username or 'N/A'} (ID: <code>{order.user.telegram_id}</code>)\n"
            f"💰 Amount: {int(order.total_price)}€",
            parse_mode="HTML",
            reply_markup=confirm_payment_kb(order_id, order.user.telegram_id),
        )
    except Exception:
        logger.exception("Payment claim notification failed for order #%d", order_id)


@router.callback_query(ConfirmPaymentCb.filter())
async def admin_confirm_payment(
    callback: CallbackQuery,
    callback_data: ConfirmPaymentCb,
    session: AsyncSession,
    bot: Bot,
) -> None:
    order = await get_order_with_user(session, callback_data.order_id)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return
    if order.status != OrderStatus.PENDING:
        await callback.answer("Payment already confirmed.", show_alert=True)
        return

    order = await update_order_status(session, callback_data.order_id, OrderStatus.PAID)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    cancel_order_jobs(order.reminder_job_id, order.cancel_job_id)

    await callback.answer("✅ Payment confirmed!")
    await callback.message.edit_text(
        callback.message.text + "\n\n✅ <b>PAYMENT CONFIRMED</b>",
        parse_mode="HTML",
        reply_markup=None,
    )

    try:
        await bot.send_message(
            callback_data.buyer_tid,
            f"✅ <b>Payment for order #{callback_data.order_id} confirmed!</b>\n\n"
            "Your order has been passed to the kitchen. 👨‍🍳",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Payment confirmation notification failed for order #%d", callback_data.order_id)


@router.callback_query(RejectPaymentCb.filter())
async def admin_reject_payment(
    callback: CallbackQuery,
    callback_data: RejectPaymentCb,
    bot: Bot,
) -> None:
    await callback.answer("❌ Payment rejected.")
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ <b>PAYMENT REJECTED</b> — awaiting customer retry",
        parse_mode="HTML",
        reply_markup=confirm_only_kb(callback_data.order_id, callback_data.buyer_tid),
    )

    try:
        await bot.send_message(
            callback_data.buyer_tid,
            "⚠️ Your payment could not be confirmed. Please check your payment details "
            "and try again, or contact us for assistance.",
            reply_markup=_retry_cancel_kb(callback_data.order_id),
        )
    except Exception:
        logger.exception("Payment rejection notification failed for order #%d", callback_data.order_id)


# ── Payment retry / cancel by user ───────────────────────────────────────────

@router.callback_query(F.data.startswith("payment_retry:"))
async def payment_retry(callback: CallbackQuery, session: AsyncSession) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await get_order_with_user(session, order_id)

    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return
    if order.user.telegram_id != callback.from_user.id:
        await callback.answer("This is not your order.", show_alert=True)
        return
    if order.status != OrderStatus.PENDING:
        await callback.answer("This order is no longer pending.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Showing payment details again.")
    await callback.message.answer(
        "💳 <b>Payment</b>\n\n"
        f"Amount to pay: <b>{int(order.total_price)}€</b>\n\n"
        "Transfer to card:\n"
        "<code>1234 5678 9012 3456</code>\n"
        "Recipient: <b>Our Restaurant</b>\n\n"
        "Press the button below after paying 👇",
        parse_mode="HTML",
        reply_markup=payment_kb(order_id),
    )


@router.callback_query(F.data.startswith("payment_cancel:"))
async def payment_cancel_by_user(
    callback: CallbackQuery, session: AsyncSession, bot: Bot
) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await get_order_with_user(session, order_id)

    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return
    if order.user.telegram_id != callback.from_user.id:
        await callback.answer("This is not your order.", show_alert=True)
        return
    if order.status != OrderStatus.PENDING:
        await callback.answer("This order is no longer pending.", show_alert=True)
        return

    cancel_order_jobs(order.reminder_job_id, order.cancel_job_id)
    deleted = await delete_order(session, order_id)

    await callback.message.edit_reply_markup(reply_markup=None)

    if not deleted:
        await callback.answer("Could not cancel order.", show_alert=True)
        return

    await callback.answer("Order cancelled.")
    await callback.message.answer(f"Your order #{order_id} has been cancelled.")

    try:
        await bot.send_message(
            ADMIN_ID,
            f"❌ Order #{order_id} was cancelled by the customer.",
        )
    except Exception:
        logger.exception("Admin cancel notification failed for order #%d", order_id)


# ── Fallback: catch-all for any unhandled message ────────────────────────────

# States owned by menu.py that the fallback must not interrupt
_MENU_FSM_STATES = {"MenuStates:waiting_for_quantity", "CartEditStates:waiting_for_qty"}


@router.message()
async def fallback_handler(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    logger.warning(
        "Fallback triggered: user=%s state=%s text=%r",
        message.from_user.id, current_state, message.text,
    )

    # These states are handled by menu.router — if we reach here something
    # unexpected happened; give a useful prompt and keep the state intact.
    if current_state in _MENU_FSM_STATES:
        await message.answer(
            "Please enter a number, or use /cancel to abort.",
        )
        return

    # Give step-specific guidance for checkout states
    if current_state == CheckoutStates.waiting_for_name.state:
        await message.answer(
            "Please enter your <b>name</b> (text, at least 2 characters):",
            parse_mode="HTML",
        )
        return

    if current_state == CheckoutStates.waiting_for_phone.state:
        await message.answer(
            "Please enter your <b>phone number</b> in international format:\n"
            "• <code>+7 999 123 45 67</code>",
            parse_mode="HTML",
        )
        return

    if current_state == CheckoutStates.waiting_for_address.state:
        await message.answer(
            "Please enter your <b>delivery address</b> (street, building, apartment):",
            parse_mode="HTML",
        )
        return

    # Unknown state — preserve cart, reset FSM, show menu
    if current_state is not None:
        data = await state.get_data()
        cart = data.get("cart", {})
        await state.clear()
        if cart:
            await state.update_data(cart=cart)
            logger.debug("Fallback: preserved cart for user %s (%d items)", message.from_user.id, len(cart))

    await message.answer(
        "💡 Use the menu buttons below to navigate.",
        reply_markup=get_main_keyboard(),
    )
