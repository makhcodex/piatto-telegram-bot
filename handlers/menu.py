import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OrderStatus
from keyboards.catalog import get_categories_keyboard, get_items_keyboard
from keyboards.main_menu import MENU_TEXTS, get_main_keyboard
from services.category_service import get_all_categories, get_category_by_slug
from services.order_service import get_order_with_user, get_user_orders
from services.product_service import get_product_by_id, get_products_by_category
from services.user_service import get_or_create_user

logger = logging.getLogger(__name__)
router = Router()


class MenuStates(StatesGroup):
    waiting_for_quantity = State()


class CartEditStates(StatesGroup):
    waiting_for_qty = State()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _show_categories(target, session: AsyncSession) -> None:
    categories = await get_all_categories(session)
    kb = get_categories_keyboard(categories)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text("Choose a category:", reply_markup=kb)
        except TelegramBadRequest:
            await target.message.answer("Choose a category:", reply_markup=kb)
        await target.answer()
    else:
        await target.answer("Choose a category:", reply_markup=kb)


def _quick_qty_kb(product_id: int, max_qty: int) -> InlineKeyboardMarkup:
    """Inline keyboard with quick quantity buttons for adding an item."""
    b = InlineKeyboardBuilder()
    num_btns = min(4, max_qty)
    for qty in range(1, num_btns + 1):
        b.button(text=str(qty), callback_data=f"qty:quick:{product_id}:{qty}")
    b.button(text="✏️ Custom amount", callback_data=f"qty:custom:{product_id}")
    b.button(text="❌ Cancel", callback_data="qty:cancel")
    b.adjust(num_btns, 2)
    return b.as_markup()


def _cart_edit_quick_kb(pid: str, max_qty: int) -> InlineKeyboardMarkup:
    """Inline keyboard with quick quantity buttons for editing a cart item."""
    b = InlineKeyboardBuilder()
    num_btns = min(4, max_qty)
    for qty in range(1, num_btns + 1):
        b.button(text=str(qty), callback_data=f"cart:edit:set:{pid}:{qty}")
    b.button(text="✏️ Custom amount", callback_data=f"cart:edit:custom:{pid}")
    b.button(text="❌ Cancel", callback_data="cart:view")
    b.adjust(num_btns, 2)
    return b.as_markup()


def _build_cart_content(cart: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Return (text, inline_keyboard) for the cart view."""
    lines = ["🛒 <b>Your Cart:</b>\n"]
    total = 0.0
    for item in cart.values():
        sub = item["price"] * item["quantity"]
        total += sub
        lines.append(f"• {item['name']} × {item['quantity']} = {int(sub)}€")
    lines.append(f"\n💰 <b>Total: {int(total)}€</b>")

    b = InlineKeyboardBuilder()
    for pid, item in cart.items():
        b.button(text=f"✏️ {item['name'][:14]}", callback_data=f"cart:edit:qty:{pid}")
        b.button(text=f"🗑 {item['name'][:14]}", callback_data=f"cart:remove:prompt:{pid}")
    b.button(text="🗑 Clear Cart", callback_data="cart:clear:prompt")
    b.adjust(*([2] * len(cart)), 1)

    return "\n".join(lines), b.as_markup()


def _get_user_cart(data: dict) -> dict:
    """Single source of truth for reading cart from FSM data."""
    return data.get("cart", {})


# ── Catalogue entry ───────────────────────────────────────────────────────────

@router.message(F.text == "📋 Catalogue")
async def show_catalog(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    cart = _get_user_cart(data)
    await state.clear()
    if cart:
        await state.update_data(cart=cart)
    await _show_categories(message, session)


@router.callback_query(F.data == "goto:catalogue")
async def goto_catalogue_cb(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    cart = _get_user_cart(data)
    await state.clear()
    if cart:
        await state.update_data(cart=cart)
    await _show_categories(callback, session)


# ── Category navigation ───────────────────────────────────────────────────────

@router.callback_query(F.data == "back_to_categories")
async def back_to_categories(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.update_data(pending_product_id=None)
    await state.set_state(None)
    await _show_categories(callback, session)


@router.callback_query(F.data.startswith("category:"))
async def show_category_items(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    category_slug = callback.data.split(":")[1]
    await state.update_data(pending_product_id=None)
    await state.set_state(None)

    category = await get_category_by_slug(session, category_slug)
    if not category:
        await callback.answer("Category not found.", show_alert=True)
        return

    products = await get_products_by_category(session, category_slug)
    if not products:
        await callback.answer("No items available in this category.", show_alert=True)
        return

    await callback.message.edit_text(
        f"{category.name} — choose an item:",
        reply_markup=get_items_keyboard(products),
    )
    await callback.answer()


# ── Item selection ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("item:"))
async def select_item(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    product_id = int(callback.data.split(":")[1])
    product = await get_product_by_id(session, product_id)

    if not product or not product.in_stock:
        await callback.answer("Item unavailable.", show_alert=True)
        return

    logger.debug("User %s viewing product %d (%s)", callback.from_user.id, product_id, product.name)

    desc_block = f"\n{product.description}\n" if product.description else ""
    full_caption = (
        f"🍕 <b>{product.name}</b>\n"
        f"{desc_block}\n"
        f"💰 Price: <b>{int(product.price)}€</b>\n"
        f"📦 Max per order: {product.max_quantity}\n\n"
        "Choose quantity:"
    )
    short_caption = (
        f"🍕 <b>{product.name}</b>\n\n"
        f"💰 Price: <b>{int(product.price)}€</b>\n"
        f"📦 Max per order: {product.max_quantity}\n\n"
        "Choose quantity:"
    )

    kb = _quick_qty_kb(product_id, product.max_quantity)
    chat_id = callback.message.chat.id

    if product.image_url:
        caption = full_caption if len(full_caption) <= 1024 else short_caption
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=product.image_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
            if len(full_caption) > 1024 and product.description:
                await bot.send_message(chat_id=chat_id, text=product.description)
        except TelegramBadRequest as e:
            logger.error("Failed to send photo for product %d (url=%s): %s",
                         product_id, product.image_url, e)
            await callback.message.answer(full_caption, parse_mode="HTML", reply_markup=kb)
    else:
        await callback.message.answer(full_caption, parse_mode="HTML", reply_markup=kb)

    await callback.answer()


# ── Quick quantity buttons ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("qty:quick:"))
async def quick_qty_add(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    product_id = int(parts[2])
    qty = int(parts[3])

    product = await get_product_by_id(session, product_id)
    if not product or not product.in_stock:
        await callback.answer("❌ Item no longer available.", show_alert=True)
        return

    if qty < 1 or qty > product.max_quantity:
        await callback.answer(f"❌ Quantity must be 1–{product.max_quantity}.", show_alert=True)
        return

    data = await state.get_data()
    cart: dict = _get_user_cart(data)
    pid_str = str(product_id)
    in_cart = cart.get(pid_str, {}).get("quantity", 0)

    if in_cart + qty > product.max_quantity:
        remaining = product.max_quantity - in_cart
        if remaining <= 0:
            await callback.answer(
                f"❌ You already have the max ({product.max_quantity}×) in cart.",
                show_alert=True,
            )
        else:
            await callback.answer(
                f"❌ You can add up to {remaining} more (max {product.max_quantity}).",
                show_alert=True,
            )
        return

    if pid_str in cart:
        cart[pid_str]["quantity"] += qty
    else:
        cart[pid_str] = {
            "name": product.name,
            "price": float(product.price),
            "quantity": qty,
            "max_quantity": product.max_quantity,
        }

    await state.update_data(cart=cart)
    logger.info(
        "User %s added product %d (%s) ×%d via quick button; cart now %d items, contents: %s",
        callback.from_user.id, product_id, product.name, qty, len(cart),
        {k: v["quantity"] for k, v in cart.items()},
    )

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    await callback.answer()
    await callback.message.answer(
        f"✅ <b>{product.name}</b> × {qty} added to cart!\n"
        f"Items in cart: {len(cart)}",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(),
    )


@router.callback_query(F.data.startswith("qty:custom:"))
async def quick_qty_custom(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[2])
    product = await get_product_by_id(session, product_id)
    if not product or not product.in_stock:
        await callback.answer("❌ Item no longer available.", show_alert=True)
        return

    await state.update_data(pending_product_id=product_id)
    await state.set_state(MenuStates.waiting_for_quantity)
    logger.debug(
        "User %s FSM: → waiting_for_quantity (custom amount, product %d)",
        callback.from_user.id, product_id,
    )
    await callback.answer()
    await callback.message.answer(
        f"✏️ Enter quantity for <b>{product.name}</b>\n"
        f"(1–{product.max_quantity}), or /cancel to abort:",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "qty:cancel")
async def quick_qty_cancel(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.set_state(None)
    await callback.answer("Cancelled.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    categories = await get_all_categories(session)
    kb = get_categories_keyboard(categories)
    await callback.message.answer("Choose a category:", reply_markup=kb)


# ── Quantity text input (Custom amount only) ──────────────────────────────────

@router.message(MenuStates.waiting_for_quantity, ~F.text.in_(MENU_TEXTS))
async def handle_quantity(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please enter a number:")
        return

    try:
        quantity = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Please enter a whole number (e.g. 1, 2, 3):")
        return

    if quantity <= 0:
        await message.answer("❌ Quantity must be at least 1. Please try again:")
        return

    data = await state.get_data()
    product_id = data.get("pending_product_id")
    logger.debug(
        "User %s custom quantity input: %d (product_id=%s)",
        message.from_user.id, quantity, product_id,
    )

    if not product_id:
        await message.answer(
            "❌ No item selected. Please open 📋 Catalogue and choose an item first."
        )
        await state.set_state(None)
        return

    try:
        product = await get_product_by_id(session, product_id)
    except Exception:
        logger.exception("DB error fetching product %s for user %s", product_id, message.from_user.id)
        await message.answer("❌ Failed to load item details. Please try again.")
        return

    if not product or not product.in_stock:
        await message.answer("❌ This item is no longer available.")
        await state.update_data(pending_product_id=None)
        await state.set_state(None)
        return

    if quantity > product.max_quantity:
        await message.answer(
            f"❌ Please enter a number between 1 and <b>{product.max_quantity}</b>:",
            parse_mode="HTML",
        )
        return

    cart: dict = _get_user_cart(data)
    pid_str = str(product_id)
    in_cart = cart.get(pid_str, {}).get("quantity", 0)

    if in_cart + quantity > product.max_quantity:
        remaining = product.max_quantity - in_cart
        if remaining <= 0:
            await message.answer(
                f"❌ You already have the maximum ({product.max_quantity}×) "
                f"<b>{product.name}</b> in your cart.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"❌ You already have {in_cart}× <b>{product.name}</b> in your cart.\n"
                f"You can add up to <b>{remaining}</b> more (max {product.max_quantity}).",
                parse_mode="HTML",
            )
        return

    if pid_str in cart:
        cart[pid_str]["quantity"] += quantity
    else:
        cart[pid_str] = {
            "name": product.name,
            "price": float(product.price),
            "quantity": quantity,
            "max_quantity": product.max_quantity,
        }

    await state.update_data(cart=cart, pending_product_id=None)
    await state.set_state(None)
    logger.info(
        "User %s cart updated: product %d (%s) ×%d, cart now %d items, contents: %s",
        message.from_user.id, product_id, product.name, quantity, len(cart),
        {k: v["quantity"] for k, v in cart.items()},
    )

    await message.answer(
        f"✅ <b>{product.name}</b> × {quantity} added to cart!\n"
        f"Items in cart: {len(cart)}",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(),
    )


# ── Cart view ─────────────────────────────────────────────────────────────────

@router.message(F.text == "🛒 Cart")
async def show_cart(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cart: dict = _get_user_cart(data)

    await state.update_data(pending_product_id=None)
    await state.set_state(None)

    logger.info(
        "User %s viewing cart: %d items, contents: %s",
        message.from_user.id, len(cart),
        {k: v["quantity"] for k, v in cart.items()},
    )

    if not cart:
        b = InlineKeyboardBuilder()
        b.button(text="📋 Go to Catalogue", callback_data="goto:catalogue")
        await message.answer(
            "🛒 Your cart is empty.\n\nStart shopping in the Catalogue!",
            reply_markup=b.as_markup(),
        )
        return

    text, kb = _build_cart_content(cart)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "cart:view")
async def cart_view_cb(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cart: dict = _get_user_cart(data)

    logger.info(
        "User %s cart view (callback): %d items, contents: %s",
        callback.from_user.id, len(cart),
        {k: v["quantity"] for k, v in cart.items()},
    )

    if not cart:
        b = InlineKeyboardBuilder()
        b.button(text="📋 Go to Catalogue", callback_data="goto:catalogue")
        await callback.message.edit_text(
            "🛒 Your cart is empty.\n\nStart shopping in the Catalogue!",
            reply_markup=b.as_markup(),
        )
        await callback.answer()
        return

    text, kb = _build_cart_content(cart)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ── Cart item removal ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cart:remove:prompt:"))
async def cart_remove_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    pid = callback.data.split(":")[-1]
    data = await state.get_data()
    cart: dict = _get_user_cart(data)
    if pid not in cart:
        await callback.answer("Item not found in cart.", show_alert=True)
        return

    name = cart[pid]["name"]
    b = InlineKeyboardBuilder()
    b.button(text="✅ Yes, Remove", callback_data=f"cart:remove:yes:{pid}")
    b.button(text="❌ No, Keep",    callback_data="cart:remove:no")
    b.adjust(2)
    await callback.message.edit_text(
        f"❓ Remove <b>{name}</b> from your cart?",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cart:remove:yes:"))
async def cart_remove_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    pid = callback.data.split(":")[-1]
    data = await state.get_data()
    cart: dict = _get_user_cart(data)

    if pid not in cart:
        await callback.answer("Item not found.", show_alert=True)
        return

    removed_name = cart.pop(pid)["name"]
    await state.update_data(cart=cart)
    await state.set_state(None)
    logger.info(
        "User %s removed %s from cart, %d items remain, contents: %s",
        callback.from_user.id, removed_name, len(cart),
        {k: v["quantity"] for k, v in cart.items()},
    )

    if not cart:
        b = InlineKeyboardBuilder()
        b.button(text="📋 Go to Catalogue", callback_data="goto:catalogue")
        await callback.message.edit_text(
            "🛒 Your cart is empty.\n\nStart shopping in the Catalogue!",
            reply_markup=b.as_markup(),
        )
        await callback.answer(f"✅ Removed {removed_name}")
        return

    text, kb = _build_cart_content(cart)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer(f"✅ Removed {removed_name}")


@router.callback_query(F.data == "cart:remove:no")
async def cart_remove_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cart: dict = _get_user_cart(data)
    if not cart:
        b = InlineKeyboardBuilder()
        b.button(text="📋 Go to Catalogue", callback_data="goto:catalogue")
        await callback.message.edit_text(
            "🛒 Your cart is empty.",
            reply_markup=b.as_markup(),
        )
        await callback.answer()
        return
    text, kb = _build_cart_content(cart)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ── Cart item quantity edit ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cart:edit:qty:"))
async def cart_edit_qty_start(callback: CallbackQuery, state: FSMContext) -> None:
    pid = callback.data.split(":")[-1]
    data = await state.get_data()
    cart: dict = _get_user_cart(data)
    if pid not in cart:
        await callback.answer("Item not found in cart.", show_alert=True)
        return

    item = cart[pid]
    max_qty = item.get("max_quantity", 10)
    kb = _cart_edit_quick_kb(pid, max_qty)
    await callback.answer()
    await callback.message.edit_text(
        f"✏️ Edit <b>{item['name']}</b>\n"
        f"Currently: × {item['quantity']}\n\n"
        "Choose new quantity:",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("cart:edit:set:"))
async def cart_edit_quick_set(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    pid = parts[3]
    new_qty = int(parts[4])

    data = await state.get_data()
    cart: dict = _get_user_cart(data)

    if pid not in cart:
        await callback.answer("Item not found.", show_alert=True)
        return

    item = cart[pid]
    max_qty = item.get("max_quantity", 10)

    if new_qty == 0:
        removed_name = cart.pop(pid)["name"]
        await state.update_data(cart=cart)
        logger.info(
            "User %s removed %s from cart via edit set=0, %d items remain",
            callback.from_user.id, removed_name, len(cart),
        )
        await callback.answer(f"✅ {removed_name} removed")
        if not cart:
            b = InlineKeyboardBuilder()
            b.button(text="📋 Go to Catalogue", callback_data="goto:catalogue")
            await callback.message.edit_text(
                "🛒 Your cart is empty.\n\nStart shopping in the Catalogue!",
                reply_markup=b.as_markup(),
            )
        else:
            text, kb = _build_cart_content(cart)
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if new_qty > max_qty:
        await callback.answer(f"❌ Max quantity is {max_qty}", show_alert=True)
        return

    old_qty = item["quantity"]
    cart[pid]["quantity"] = new_qty
    await state.update_data(cart=cart, editing_cart_pid=None)
    logger.info(
        "User %s changed %s qty %d→%d via quick button; cart: %s",
        callback.from_user.id, item["name"], old_qty, new_qty,
        {k: v["quantity"] for k, v in cart.items()},
    )

    subtotal = int(item["price"] * new_qty)
    text, kb = _build_cart_content(cart)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()
    await callback.message.answer(
        f"✅ Updated: <b>{item['name']}</b> × {new_qty} = {subtotal}€",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("cart:edit:custom:"))
async def cart_edit_custom_start(callback: CallbackQuery, state: FSMContext) -> None:
    pid = callback.data.split(":")[-1]
    data = await state.get_data()
    cart: dict = _get_user_cart(data)
    if pid not in cart:
        await callback.answer("Item not found.", show_alert=True)
        return

    item = cart[pid]
    max_qty = item.get("max_quantity", 10)
    await state.update_data(editing_cart_pid=pid)
    await state.set_state(CartEditStates.waiting_for_qty)
    await callback.answer()
    await callback.message.answer(
        f"✏️ Enter new quantity for <b>{item['name']}</b>\n"
        f"(1–{max_qty}, or 0 to remove, or /cancel to abort):",
        parse_mode="HTML",
    )


@router.message(CartEditStates.waiting_for_qty, ~F.text.in_(MENU_TEXTS))
async def handle_cart_edit_qty(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please enter a number:")
        return

    try:
        new_qty = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Please enter a whole number (e.g. 0, 1, 2, 3):")
        return

    if new_qty < 0:
        await message.answer("❌ Quantity must be 0 or higher (0 = remove item):")
        return

    data = await state.get_data()
    pid = data.get("editing_cart_pid")
    cart: dict = _get_user_cart(data)

    if not pid or pid not in cart:
        await message.answer("❌ Item not found. Try opening your cart again.")
        await state.set_state(None)
        return

    item = cart[pid]

    if new_qty == 0:
        removed_name = cart.pop(pid)["name"]
        await state.update_data(cart=cart, editing_cart_pid=None)
        await state.set_state(None)
        logger.info(
            "User %s removed %s from cart via qty=0 text input, %d items remain",
            message.from_user.id, removed_name, len(cart),
        )
        await message.answer(
            f"✅ <b>{removed_name}</b> removed from cart.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(),
        )
        return

    try:
        product = await get_product_by_id(session, int(pid))
    except Exception:
        logger.exception("DB error fetching product %s during cart edit", pid)
        product = None

    max_qty = product.max_quantity if product else item.get("max_quantity", 99)

    if new_qty > max_qty:
        await message.answer(
            f"❌ Please enter a number between 0 and <b>{max_qty}</b>:",
            parse_mode="HTML",
        )
        return

    old_qty = item["quantity"]
    cart[pid]["quantity"] = new_qty
    await state.update_data(cart=cart, editing_cart_pid=None)
    await state.set_state(None)
    logger.info(
        "User %s changed %s qty %d→%d via text; cart: %s",
        message.from_user.id, item["name"], old_qty, new_qty,
        {k: v["quantity"] for k, v in cart.items()},
    )

    subtotal = int(item["price"] * new_qty)
    await message.answer(
        f"✅ Updated: <b>{item['name']}</b> × {new_qty} = {subtotal}€",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(),
    )


# ── Cart clear ────────────────────────────────────────────────────────────────

@router.message(F.text == "🗑 Clear Cart")
async def clear_cart_menu(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cart = _get_user_cart(data)

    await state.update_data(pending_product_id=None)
    await state.set_state(None)

    logger.debug("User %s clear cart request, cart has %d items", message.from_user.id, len(cart))

    if not cart:
        await message.answer("🛒 Your cart is already empty.")
        return
    await message.answer(
        "Are you sure? Your entire cart will be cleared.",
        reply_markup=_clear_cart_confirm_kb(),
    )


@router.callback_query(F.data == "cart:clear:prompt")
async def clear_cart_prompt_cb(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Are you sure? Your entire cart will be cleared.",
        reply_markup=_clear_cart_confirm_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "cart:clear:yes")
async def clear_cart_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(cart={}, pending_product_id=None)
    await state.set_state(None)
    logger.info("User %s cleared their cart", callback.from_user.id)
    await callback.message.edit_text("🗑 Cart cleared.")
    await callback.answer("✅ Cart cleared")


@router.callback_query(F.data == "cart:clear:no")
async def clear_cart_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cart: dict = _get_user_cart(data)
    if cart:
        text, kb = _build_cart_content(cart)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await callback.message.edit_text("Cancelled.")
    await callback.answer()


def _clear_cart_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Yes, Clear", callback_data="cart:clear:yes")
    b.button(text="❌ No, Keep",  callback_data="cart:clear:no")
    b.adjust(2)
    return b.as_markup()


# ── My orders ─────────────────────────────────────────────────────────────────

@router.message(F.text == "📦 My Orders")
async def show_my_orders(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.update_data(pending_product_id=None)
    await state.set_state(None)
    user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
    orders = await get_user_orders(session, user.id)
    if not orders:
        await message.answer("You have no orders yet.")
        return
    await message.answer(
        "📦 <b>My Orders</b> (last 10):",
        parse_mode="HTML",
        reply_markup=_orders_list_kb(orders),
    )


@router.callback_query(F.data == "myorders:list")
async def my_orders_list(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
    orders = await get_user_orders(session, user.id)
    if not orders:
        await callback.message.edit_text("You have no orders yet.")
        await callback.answer()
        return
    await callback.message.edit_text(
        "📦 <b>My Orders</b> (last 10):",
        parse_mode="HTML",
        reply_markup=_orders_list_kb(orders),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("myorder:"))
async def show_my_order_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await get_order_with_user(session, order_id)
    if not order or order.user.telegram_id != callback.from_user.id:
        await callback.answer("Order not found.", show_alert=True)
        return

    items_text = "\n".join(
        f"  • {i.product.name if i.product else '?'} × {i.quantity} = {int(i.price * i.quantity)}€"
        for i in order.items
    )
    date_str = order.created_at.strftime("%d.%m.%Y %H:%M")
    text = (
        f"📦 <b>Order #{order.id}</b>\n\n"
        f"📅 Date: {date_str}\n"
        f"📍 Address: {order.address}\n\n"
        f"🛒 Items:\n{items_text}\n\n"
        f"💰 Total: <b>{int(order.total_price)}€</b>\n"
        f"📊 Status: <b>{OrderStatus.LABELS.get(order.status, order.status)}</b>"
    )
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Back", callback_data="myorders:list")
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    await callback.answer()


def _orders_list_kb(orders) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for o in orders:
        label = OrderStatus.LABELS.get(o.status, o.status)
        b.button(
            text=f"#{o.id} | {label} | {int(o.total_price)}€",
            callback_data=f"myorder:{o.id}",
        )
    b.adjust(1)
    return b.as_markup()
