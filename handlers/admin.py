import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import ADMIN_ID
from db.models import OrderStatus
from keyboards.admin_menu import (
    CategoryActionCb, DeleteOrderCb, HistoryOrderCb, HistoryPageCb,
    OrderStatusCb, ProductActionCb, ProductEditCb,
    admin_main_kb, categories_manage_kb, category_select_kb,
    history_kb, history_order_detail_kb,
    order_detail_kb, product_detail_edit_kb, products_list_kb,
    products_list_manage_kb,
)
from services.category_service import (
    create_category, delete_category, get_all_categories, rename_category,
)
from services.order_service import (
    delete_order, get_active_orders, get_all_orders, get_order_with_user,
    get_stats, update_order_status,
)
from services.product_service import (
    create_product, get_all_products, get_product_by_id,
    soft_delete_product, toggle_product_stock, update_product_field,
)
from services.scheduler import cancel_order_jobs

logger = logging.getLogger(__name__)
router = Router()


# ── Admin guard filter ────────────────────────────────────────────────────────

class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user.id == ADMIN_ID


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ── FSM states ────────────────────────────────────────────────────────────────

class AddProductStates(StatesGroup):
    name         = State()
    description  = State()
    price        = State()
    category     = State()
    max_quantity = State()


class EditProductStates(StatesGroup):
    name         = State()
    description  = State()
    price        = State()
    image_url    = State()
    max_quantity = State()


class AddCategoryStates(StatesGroup):
    slug = State()
    name = State()


class RenameCategoryStates(StatesGroup):
    new_name = State()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_order_detail(order) -> str:
    items_text = "\n".join(
        f"  • {i.product.name if i.product else '?'} × {i.quantity} = {int(i.price * i.quantity)}€"
        for i in order.items
    )
    return (
        f"📦 <b>Order #{order.id}</b>\n\n"
        f"👤 @{order.user.username or 'N/A'} (ID: <code>{order.user.telegram_id}</code>)\n"
        f"📞 {order.user.phone or '—'}\n"
        f"📍 {order.address}\n\n"
        f"🛒 Items:\n{items_text}\n\n"
        f"💰 Total: <b>{int(order.total_price)}€</b>\n"
        f"📊 Status: <b>{OrderStatus.LABELS.get(order.status, order.status)}</b>"
    )


def _format_product_detail(product) -> str:
    return (
        f"📦 <b>{product.name}</b>  (ID: {product.id})\n\n"
        f"📂 Category: <code>{product.category}</code>\n"
        f"💰 Price: <b>{int(product.price)}€</b>\n"
        f"📦 Max per order: <b>{product.max_quantity}</b>\n"
        f"📝 Description: {product.description or '(none)'}\n"
        f"📸 Photo: {'✅ set' if product.image_url else '❌ not set'}\n"
        f"🔄 Stock: {'✅ In Stock' if product.in_stock else '❌ Out of Stock'}"
    )


# ── /admin entry point ────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🛠 <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_main_kb())


@router.callback_query(F.data == "adm:back")
async def adm_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🛠 <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_main_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "adm:noop")
async def adm_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ── Active orders ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:orders")
async def adm_orders(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        orders = await get_active_orders(session)
    except Exception:
        logger.exception("adm_orders: DB error")
        await callback.answer("DB error loading orders.", show_alert=True)
        return

    if not orders:
        await callback.answer("No active orders.", show_alert=True)
        return

    b = InlineKeyboardBuilder()
    for o in orders:
        label = (
            f"#{o.id} | {OrderStatus.LABELS.get(o.status, o.status)} | "
            f"{int(o.total_price)}€ | @{o.user.username or o.user.telegram_id}"
        )
        b.button(text=label, callback_data=f"adm:order:{o.id}")
    b.button(text="◀️ Back", callback_data="adm:back")
    b.adjust(1)

    await callback.message.edit_text(
        f"📋 <b>Active Orders ({len(orders)})</b>",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:order:"))
async def adm_order_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    order_id = int(callback.data.split(":")[2])
    try:
        order = await get_order_with_user(session, order_id)
    except Exception:
        logger.exception("adm_order_detail: DB error for order %d", order_id)
        await callback.answer("DB error.", show_alert=True)
        return

    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    await callback.message.edit_text(
        _format_order_detail(order), parse_mode="HTML",
        reply_markup=order_detail_kb(order.id, order.status),
    )
    await callback.answer()


@router.callback_query(OrderStatusCb.filter())
async def adm_change_status(
    callback: CallbackQuery, callback_data: OrderStatusCb, session: AsyncSession, bot: Bot
) -> None:
    try:
        order = await get_order_with_user(session, callback_data.order_id)
    except Exception:
        logger.exception("adm_change_status: DB error for order %d", callback_data.order_id)
        await callback.answer("DB error.", show_alert=True)
        return

    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    # Idempotency: skip if already at target status
    if order.status == callback_data.status:
        await callback.answer("Status is already set to that value.", show_alert=True)
        return

    try:
        order = await update_order_status(session, callback_data.order_id, callback_data.status)
    except Exception:
        logger.exception("adm_change_status: failed to update order %d", callback_data.order_id)
        await callback.answer("Failed to update status.", show_alert=True)
        return

    if callback_data.status == OrderStatus.PAID:
        cancel_order_jobs(order.reminder_job_id, order.cancel_job_id)

    await callback.answer(f"Status → {callback_data.status}")
    await callback.message.edit_reply_markup(reply_markup=order_detail_kb(order.id, order.status))

    if callback_data.status == OrderStatus.PAID:
        try:
            await bot.send_message(
                order.user.telegram_id,
                f"✅ <b>Payment confirmed! Your order #{order.id} is being prepared.</b>",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Payment notification failed for order %d", order.id)


# ── Order history ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:history")
async def adm_history_entry(callback: CallbackQuery, session: AsyncSession) -> None:
    orders, total = await get_all_orders(session, offset=0, limit=10, filter_status="all")
    await callback.message.edit_text(
        f"📜 <b>Order History</b> — All ({total} total)",
        parse_mode="HTML",
        reply_markup=history_kb("all", 0, orders, total),
    )
    await callback.answer()


@router.callback_query(HistoryPageCb.filter())
async def adm_history_page(
    callback: CallbackQuery, callback_data: HistoryPageCb, session: AsyncSession
) -> None:
    flt, page = callback_data.flt, callback_data.page
    orders, total = await get_all_orders(session, offset=page * 10, limit=10, filter_status=flt)

    filter_labels = {"all": "All", "active": "Active", "completed": "Completed", "cancelled": "Cancelled"}
    header = f"📜 <b>Order History</b> — {filter_labels.get(flt, flt)} ({total} total)"

    await callback.message.edit_text(
        header, parse_mode="HTML",
        reply_markup=history_kb(flt, page, orders, total),
    )
    await callback.answer()


@router.callback_query(HistoryOrderCb.filter())
async def adm_history_order_detail(
    callback: CallbackQuery, callback_data: HistoryOrderCb, session: AsyncSession
) -> None:
    order = await get_order_with_user(session, callback_data.order_id)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    await callback.message.edit_text(
        _format_order_detail(order), parse_mode="HTML",
        reply_markup=history_order_detail_kb(
            order.id, order.status, callback_data.flt, callback_data.page
        ),
    )
    await callback.answer()


# ── Delete order ──────────────────────────────────────────────────────────────

@router.callback_query(DeleteOrderCb.filter(F.confirmed == False))
async def adm_delete_order_prompt(
    callback: CallbackQuery, callback_data: DeleteOrderCb, session: AsyncSession
) -> None:
    order = await get_order_with_user(session, callback_data.order_id)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return
    if order.status == OrderStatus.DELIVERED:
        await callback.answer("Cannot delete a delivered order.", show_alert=True)
        return

    b = InlineKeyboardBuilder()
    b.button(
        text="✅ Yes, delete",
        callback_data=DeleteOrderCb(order_id=callback_data.order_id, confirmed=True).pack(),
    )
    b.button(text="❌ No", callback_data=f"adm:order:{callback_data.order_id}")
    b.adjust(2)
    await callback.message.edit_text(
        f"❓ Delete order #{callback_data.order_id}? This cannot be undone.",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(DeleteOrderCb.filter(F.confirmed == True))
async def adm_delete_order_confirm(
    callback: CallbackQuery, callback_data: DeleteOrderCb, session: AsyncSession, bot: Bot
) -> None:
    order = await get_order_with_user(session, callback_data.order_id)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return
    if order.status == OrderStatus.DELIVERED:
        await callback.answer("Cannot delete a delivered order.", show_alert=True)
        return

    buyer_tid = order.user.telegram_id
    order_id  = order.id

    cancel_order_jobs(order.reminder_job_id, order.cancel_job_id)

    try:
        deleted = await delete_order(session, order_id)
    except Exception:
        logger.exception("Failed to delete order %d", order_id)
        await callback.answer("DB error deleting order.", show_alert=True)
        return

    if not deleted:
        await callback.answer("Failed to delete order.", show_alert=True)
        return

    await callback.answer("✅ Order deleted.")
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Back to Admin", callback_data="adm:back")
    await callback.message.edit_text(
        f"✅ Order #{order_id} has been deleted.",
        reply_markup=b.as_markup(),
    )

    try:
        await bot.send_message(
            buyer_tid,
            f"Your order #{order_id} has been cancelled by the restaurant. "
            f"Please contact us for details.",
        )
    except Exception:
        logger.exception("Delete-order notification failed for order %d", order_id)


# ── Statistics ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        s = await get_stats(session)
    except Exception:
        logger.exception("adm_stats: DB error")
        await callback.answer("DB error loading stats.", show_alert=True)
        return

    lines = [
        "📊 <b>Statistics</b>\n",
        f"Total orders: <b>{s['total_orders']}</b>",
        f"Revenue (paid): <b>{int(s['total_revenue'])}€</b>\n",
        "<b>By status:</b>",
    ]
    for status, count in s["by_status"].items():
        lines.append(f"  {OrderStatus.LABELS.get(status, status)}: {count}")

    b = InlineKeyboardBuilder()
    b.button(text="◀️ Back", callback_data="adm:back")
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=b.as_markup())
    await callback.answer()


# ── Add product ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:add_product")
async def adm_add_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddProductStates.name)
    await callback.message.answer(
        "➕ <b>Add Product</b>\n\nStep 1/5 — Enter product name:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AddProductStates.name)
async def adm_product_name(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Please send the name as text:")
        return
    name = message.text.strip()
    if len(name) < 1:
        await message.answer("❌ Name cannot be empty:")
        return
    await state.update_data(prod_name=name)
    await state.set_state(AddProductStates.description)
    await message.answer("Step 2/5 — Enter description (or 'no' to skip):")


@router.message(AddProductStates.description)
async def adm_product_desc(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    text = message.text.strip()
    await state.update_data(prod_desc=None if text.lower() in ("no", "-", "") else text)
    await state.set_state(AddProductStates.price)
    await message.answer("Step 3/5 — Enter price (e.g. 12 or 12.50):")


@router.message(AddProductStates.price)
async def adm_product_price(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    try:
        price = Decimal(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("❌ Invalid price. Enter a positive number (e.g. 12 or 9.50):")
        return

    await state.update_data(prod_price=str(price))
    await state.set_state(AddProductStates.category)

    try:
        categories = await get_all_categories(session)
    except Exception:
        logger.exception("adm_product_price: failed to load categories")
        await message.answer("❌ Failed to load categories. Please try again.")
        return

    await message.answer(
        "Step 4/5 — Choose category:",
        reply_markup=category_select_kb(categories, "adm_cat_pick"),
    )


@router.callback_query(F.data.startswith("adm_cat_pick:"))
async def adm_product_category(callback: CallbackQuery, state: FSMContext) -> None:
    slug = callback.data.split(":")[1]
    await state.update_data(prod_category=slug)
    await state.set_state(AddProductStates.max_quantity)
    await callback.message.answer("Step 5/5 — Enter max quantity per order (e.g. 10):")
    await callback.answer()


@router.message(AddProductStates.max_quantity)
async def adm_product_max_qty(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please send the number as text:")
        return

    try:
        max_qty = int(message.text.strip())
        if max_qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Enter a positive whole number (e.g. 10):")
        return

    data = await state.get_data()

    # Validate all required fields collected across previous steps
    missing = [f for f in ("prod_name", "prod_category", "prod_price") if not data.get(f)]
    if missing:
        await state.clear()
        await message.answer(
            f"❌ Session error — missing: {', '.join(missing)}. Please start over from /admin.",
            reply_markup=admin_main_kb(),
        )
        return

    try:
        product = await create_product(
            session,
            name=data["prod_name"],
            category=data["prod_category"],
            price=Decimal(data["prod_price"]),
            max_quantity=max_qty,
            description=data.get("prod_desc"),
        )
    except Exception:
        logger.exception(
            "create_product failed: name=%r category=%r price=%r max_qty=%d",
            data.get("prod_name"), data.get("prod_category"), data.get("prod_price"), max_qty,
        )
        await message.answer(
            "❌ Database error while saving the product.\n"
            "Check bot.log for details. Please try again.",
            reply_markup=admin_main_kb(),
        )
        return

    await state.clear()
    await message.answer(
        f"✅ <b>{product.name}</b> (ID {product.id}) added!\n\n"
        f"📂 Category: {product.category}\n"
        f"💰 Price: {int(product.price)}€\n"
        f"📦 Max qty: {product.max_quantity}",
        parse_mode="HTML",
        reply_markup=admin_main_kb(),
    )
    logger.info("Admin created product #%d '%s'", product.id, product.name)


# ── Manage / Edit products ────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:manage_products")
async def adm_manage_products(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    try:
        products = await get_all_products(session)
    except Exception:
        logger.exception("adm_manage_products: DB error")
        await callback.answer("DB error loading products.", show_alert=True)
        return

    if not products:
        await callback.message.edit_text(
            "📦 <b>Manage Products</b>\n\nNo products found. Add one first.",
            parse_mode="HTML",
            reply_markup=admin_main_kb(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📦 <b>Manage Products</b>\n\nSelect a product to view or edit:",
        parse_mode="HTML",
        reply_markup=products_list_manage_kb(products),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:"))
async def adm_product_detail(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[2])
    await state.clear()

    try:
        product = await get_product_by_id(session, product_id)
    except Exception:
        logger.exception("adm_product_detail: DB error for product %d", product_id)
        await callback.answer("DB error.", show_alert=True)
        return

    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return

    await callback.message.edit_text(
        _format_product_detail(product),
        parse_mode="HTML",
        reply_markup=product_detail_edit_kb(product_id, product.in_stock),
    )
    await callback.answer()


@router.callback_query(ProductEditCb.filter())
async def adm_product_edit(
    callback: CallbackQuery, callback_data: ProductEditCb, state: FSMContext, session: AsyncSession
) -> None:
    product_id = callback_data.product_id
    field = callback_data.field

    try:
        product = await get_product_by_id(session, product_id)
    except Exception:
        logger.exception("adm_product_edit: DB error for product %d", product_id)
        await callback.answer("DB error.", show_alert=True)
        return

    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return

    # ── Inline actions (no FSM needed) ────────────────────────────────────────

    if field == "stock":
        try:
            new_val = await toggle_product_stock(session, product_id)
        except Exception:
            logger.exception("Stock toggle failed for product %d", product_id)
            await callback.answer("DB error toggling stock.", show_alert=True)
            return
        icon = "✅ In Stock" if new_val else "❌ Out of Stock"
        await callback.answer(f"Stock: {icon}")
        product = await get_product_by_id(session, product_id)
        await callback.message.edit_text(
            _format_product_detail(product), parse_mode="HTML",
            reply_markup=product_detail_edit_kb(product_id, product.in_stock),
        )
        return

    if field == "delete":
        b = InlineKeyboardBuilder()
        b.button(
            text="✅ Yes, delete permanently",
            callback_data=ProductEditCb(product_id=product_id, field="delete_confirm").pack(),
        )
        b.button(text="❌ No, cancel", callback_data=f"adm:prod:{product_id}")
        b.adjust(1)
        await callback.message.edit_text(
            f"❓ Delete <b>{product.name}</b>?\n\nThis removes it from the catalogue permanently.",
            parse_mode="HTML",
            reply_markup=b.as_markup(),
        )
        await callback.answer()
        return

    if field == "delete_confirm":
        try:
            ok = await soft_delete_product(session, product_id)
        except Exception:
            logger.exception("Soft-delete failed for product %d", product_id)
            await callback.answer("DB error deleting product.", show_alert=True)
            return

        if not ok:
            await callback.answer("Product not found.", show_alert=True)
            return

        await callback.answer("✅ Product deleted.")
        logger.info("Admin deleted product #%d '%s'", product_id, product.name)
        try:
            products = await get_all_products(session)
            await callback.message.edit_text(
                "📦 <b>Manage Products</b>\n\nProduct deleted. Select another:",
                parse_mode="HTML",
                reply_markup=products_list_manage_kb(products) if products else admin_main_kb(),
            )
        except Exception:
            await callback.message.edit_text(
                "✅ Product deleted.", reply_markup=admin_main_kb()
            )
        return

    if field == "category":
        try:
            categories = await get_all_categories(session)
        except Exception:
            logger.exception("adm_product_edit(category): failed to load categories")
            await callback.answer("DB error.", show_alert=True)
            return

        await callback.message.answer(
            f"Current category: <b>{product.category}</b>\n\nChoose new category:",
            parse_mode="HTML",
            reply_markup=category_select_kb(categories, f"adm_cat_edit:{product_id}"),
        )
        await callback.answer()
        return

    # ── FSM-based text-input edits ─────────────────────────────────────────────

    await state.update_data(editing_product_id=product_id)

    if field == "name":
        await state.set_state(EditProductStates.name)
        await callback.message.answer(
            f"Current name: <b>{product.name}</b>\n\nEnter new name:",
            parse_mode="HTML",
        )
    elif field == "desc":
        await state.set_state(EditProductStates.description)
        current = product.description or "(none)"
        await callback.message.answer(
            f"Current description: {current}\n\nEnter new description (or 'no' to clear):"
        )
    elif field == "price":
        await state.set_state(EditProductStates.price)
        await callback.message.answer(
            f"Current price: <b>{int(product.price)}€</b>\n\nEnter new price:",
            parse_mode="HTML",
        )
    elif field == "photo":
        await state.set_state(EditProductStates.image_url)
        await callback.message.answer(
            "Send a new photo for this product (or type 'no' to remove the current photo):"
        )
    elif field == "max_qty":
        await state.set_state(EditProductStates.max_quantity)
        await callback.message.answer(
            f"Current max quantity: <b>{product.max_quantity}</b>\n\nEnter new max quantity:",
            parse_mode="HTML",
        )

    await callback.answer()


@router.callback_query(F.data.startswith("adm_cat_edit:"))
async def adm_edit_category_pick(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    # Format: adm_cat_edit:{product_id}:{slug}
    parts = callback.data.split(":")
    product_id = int(parts[1])
    slug = parts[2]

    try:
        product = await update_product_field(session, product_id, category=slug)
    except Exception:
        logger.exception("adm_edit_category_pick: DB error for product %d", product_id)
        await callback.answer("DB error updating category.", show_alert=True)
        return

    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return

    await callback.answer(f"✅ Category → {slug}")
    logger.info("Admin updated product #%d category → %s", product_id, slug)
    await callback.message.edit_text(
        _format_product_detail(product), parse_mode="HTML",
        reply_markup=product_detail_edit_kb(product_id, product.in_stock),
    )


# ── Edit product field — message handlers ─────────────────────────────────────

async def _finish_edit(message: Message, state: FSMContext, session: AsyncSession, **kwargs) -> None:
    """Apply kwargs update to the product and send the refreshed detail view."""
    data = await state.get_data()
    product_id = data.get("editing_product_id")
    if not product_id:
        await state.clear()
        await message.answer("❌ Edit session expired. Start again from /admin.", reply_markup=admin_main_kb())
        return

    try:
        product = await update_product_field(session, product_id, **kwargs)
    except Exception:
        logger.exception("_finish_edit: DB error updating product %s", product_id)
        await message.answer("❌ DB error saving changes. Please try again.")
        return

    if not product:
        await state.clear()
        await message.answer("❌ Product not found.", reply_markup=admin_main_kb())
        return

    await state.clear()
    field_name = list(kwargs.keys())[0]
    logger.info("Admin updated product #%d field '%s'", product_id, field_name)
    await message.answer(
        f"✅ Updated!\n\n{_format_product_detail(product)}",
        parse_mode="HTML",
        reply_markup=product_detail_edit_kb(product_id, product.in_stock),
    )


@router.message(EditProductStates.name)
async def adm_edit_name_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    name = message.text.strip()
    if len(name) < 1:
        await message.answer("❌ Name cannot be empty:")
        return
    await _finish_edit(message, state, session, name=name)


@router.message(EditProductStates.description)
async def adm_edit_desc_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    text = message.text.strip()
    desc = None if text.lower() in ("no", "-", "") else text
    await _finish_edit(message, state, session, description=desc)


@router.message(EditProductStates.price)
async def adm_edit_price_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    try:
        price = Decimal(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("❌ Invalid price. Enter a positive number (e.g. 12 or 9.50):")
        return
    await _finish_edit(message, state, session, price=price)


@router.message(EditProductStates.image_url)
async def adm_edit_photo_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text and message.text.strip().lower() in ("no", "-", ""):
        # Remove photo
        await _finish_edit(message, state, session, image_url=None)
        return

    if not message.photo:
        await message.answer("❌ Please send a photo (or type 'no' to remove the current one):")
        return

    file_id = message.photo[-1].file_id
    await _finish_edit(message, state, session, image_url=file_id)


@router.message(EditProductStates.max_quantity)
async def adm_edit_max_qty_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    try:
        max_qty = int(message.text.strip())
        if max_qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Enter a positive whole number (e.g. 10):")
        return
    await _finish_edit(message, state, session, max_quantity=max_qty)


# ── Stock management (standalone toggle list) ─────────────────────────────────

@router.callback_query(F.data == "adm:stock")
async def adm_stock(callback: CallbackQuery, session: AsyncSession) -> None:
    products = await get_all_products(session)
    await callback.message.edit_text(
        "🔄 Click a product to toggle in/out of stock:",
        reply_markup=products_list_kb(products, "stock"),
    )
    await callback.answer()


@router.callback_query(ProductActionCb.filter(F.action == "stock"))
async def adm_toggle_stock(
    callback: CallbackQuery, callback_data: ProductActionCb, session: AsyncSession
) -> None:
    try:
        new_val = await toggle_product_stock(session, callback_data.product_id)
    except Exception:
        logger.exception("adm_toggle_stock: DB error for product %d", callback_data.product_id)
        await callback.answer("DB error.", show_alert=True)
        return

    icon = "✅ in stock" if new_val else "❌ out of stock"
    await callback.answer(f"Product is now: {icon}")
    products = await get_all_products(session)
    await callback.message.edit_reply_markup(reply_markup=products_list_kb(products, "stock"))


# ── Category management ───────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:categories")
async def adm_categories(callback: CallbackQuery, session: AsyncSession) -> None:
    cats = await get_all_categories(session)
    await callback.message.edit_text(
        "📂 <b>Manage Categories</b>",
        parse_mode="HTML",
        reply_markup=categories_manage_kb(cats),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:add_category")
async def adm_add_category_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddCategoryStates.slug)
    await callback.message.answer(
        "Enter category slug:\n"
        "<i>Only letters, digits and '_', e.g.: <code>sushi</code></i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AddCategoryStates.slug)
async def adm_category_slug(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    slug = message.text.strip().lower()
    if not slug.replace("_", "").isalnum():
        await message.answer("❌ Slug may only contain letters, digits and '_':")
        return
    await state.update_data(cat_slug=slug)
    await state.set_state(AddCategoryStates.name)
    await message.answer("Enter display name (emojis allowed, e.g. 🍣 Sushi):")


@router.message(AddCategoryStates.name)
async def adm_category_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    data = await state.get_data()
    await state.clear()
    try:
        result = await create_category(session, slug=data["cat_slug"], name=message.text.strip())
    except Exception:
        logger.exception("adm_category_name: DB error")
        await message.answer("❌ DB error creating category.", reply_markup=admin_main_kb())
        return

    if isinstance(result, str):
        await message.answer(f"❌ {result}", reply_markup=admin_main_kb())
    else:
        await message.answer(
            f"✅ Category <b>{result.name}</b> (slug: <code>{result.slug}</code>) created!",
            parse_mode="HTML",
            reply_markup=admin_main_kb(),
        )


@router.callback_query(CategoryActionCb.filter(F.action == "rename"))
async def adm_rename_category_start(
    callback: CallbackQuery, callback_data: CategoryActionCb, state: FSMContext
) -> None:
    await state.clear()
    await state.update_data(rename_slug=callback_data.slug)
    await state.set_state(RenameCategoryStates.new_name)
    await callback.message.answer(
        f"Enter new name for category <code>{callback_data.slug}</code>:", parse_mode="HTML"
    )
    await callback.answer()


@router.message(RenameCategoryStates.new_name)
async def adm_rename_category(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        await message.answer("❌ Please send text:")
        return
    data = await state.get_data()
    slug = data.get("rename_slug", "")
    await state.clear()
    try:
        ok = await rename_category(session, slug, message.text.strip())
    except Exception:
        logger.exception("adm_rename_category: DB error")
        await message.answer("❌ DB error renaming category.", reply_markup=admin_main_kb())
        return

    if ok:
        await message.answer("✅ Category renamed.", reply_markup=admin_main_kb())
    else:
        await message.answer("❌ Category not found.", reply_markup=admin_main_kb())


@router.callback_query(CategoryActionCb.filter(F.action == "delete"))
async def adm_delete_category(
    callback: CallbackQuery, callback_data: CategoryActionCb, session: AsyncSession
) -> None:
    try:
        err = await delete_category(session, callback_data.slug)
    except Exception:
        logger.exception("adm_delete_category: DB error")
        await callback.answer("DB error deleting category.", show_alert=True)
        return

    if err:
        await callback.answer(err, show_alert=True)
    else:
        await callback.answer("✅ Category deleted.")
        cats = await get_all_categories(session)
        await callback.message.edit_reply_markup(reply_markup=categories_manage_kb(cats))
