from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db.models import OrderStatus


class OrderStatusCb(CallbackData, prefix="os"):
    order_id: int
    status: str


class ConfirmPaymentCb(CallbackData, prefix="cp"):
    order_id: int
    buyer_tid: int


class RejectPaymentCb(CallbackData, prefix="rp"):
    order_id: int
    buyer_tid: int


class ProductActionCb(CallbackData, prefix="pa"):
    action: str         # "stock"
    product_id: int


class ProductEditCb(CallbackData, prefix="pe"):
    product_id: int
    field: str          # "name"|"desc"|"price"|"photo"|"max_qty"|"category"|"stock"|"delete"|"delete_confirm"


class CategoryActionCb(CallbackData, prefix="ca"):
    action: str         # "rename" | "delete"
    slug: str


class DeleteOrderCb(CallbackData, prefix="deladm"):
    order_id: int
    confirmed: bool


class HistoryPageCb(CallbackData, prefix="hp"):
    flt: str    # all | active | completed | cancelled
    page: int


class HistoryOrderCb(CallbackData, prefix="hor"):
    order_id: int
    flt: str
    page: int


def admin_main_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 Active Orders",         callback_data="adm:orders")
    b.button(text="📜 Order History",         callback_data="adm:history")
    b.button(text="➕ Add Product",           callback_data="adm:add_product")
    b.button(text="📦 Manage Products",       callback_data="adm:manage_products")
    b.button(text="📂 Manage Categories",     callback_data="adm:categories")
    b.button(text="📊 Statistics",            callback_data="adm:stats")
    b.button(text="🔄 Toggle Stock",          callback_data="adm:stock")
    b.adjust(1)
    return b.as_markup()


def order_detail_kb(order_id: int, current_status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    next_status = OrderStatus.TRANSITIONS.get(current_status)
    if next_status:
        label = {
            "paid":       "✅ Confirm Payment",
            "preparing":  "👨‍🍳 Start Cooking",
            "delivering": "🚚 Out for Delivery",
            "delivered":  "✅ Delivered",
        }[next_status]
        b.button(text=label, callback_data=OrderStatusCb(order_id=order_id, status=next_status).pack())
    if current_status != OrderStatus.DELIVERED:
        b.button(
            text="🗑 Delete Order",
            callback_data=DeleteOrderCb(order_id=order_id, confirmed=False).pack(),
        )
    b.button(text="◀️ Back to Orders", callback_data="adm:orders")
    b.adjust(1)
    return b.as_markup()


def history_order_detail_kb(order_id: int, current_status: str, flt: str, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    next_status = OrderStatus.TRANSITIONS.get(current_status)
    if next_status:
        label = {
            "paid":       "✅ Confirm Payment",
            "preparing":  "👨‍🍳 Start Cooking",
            "delivering": "🚚 Out for Delivery",
            "delivered":  "✅ Delivered",
        }[next_status]
        b.button(text=label, callback_data=OrderStatusCb(order_id=order_id, status=next_status).pack())
    if current_status != OrderStatus.DELIVERED:
        b.button(
            text="🗑 Delete Order",
            callback_data=DeleteOrderCb(order_id=order_id, confirmed=False).pack(),
        )
    b.button(text="◀️ Back to History", callback_data=HistoryPageCb(flt=flt, page=page).pack())
    b.adjust(1)
    return b.as_markup()


def history_kb(flt: str, page: int, orders: list, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    for f_val, f_label in [("all", "All"), ("active", "Active"), ("completed", "Done"), ("cancelled", "Cancelled")]:
        mark = "▶ " if f_val == flt else ""
        b.button(text=f"{mark}{f_label}", callback_data=HistoryPageCb(flt=f_val, page=0).pack())

    for o in orders:
        label = f"#{o.id} | {OrderStatus.LABELS.get(o.status, o.status)} | {int(o.total_price)}€"
        b.button(text=label, callback_data=HistoryOrderCb(order_id=o.id, flt=flt, page=page).pack())

    total_pages = max(1, (total + 9) // 10)
    nav_count = 0
    if total_pages > 1:
        if page > 0:
            b.button(text="◀️", callback_data=HistoryPageCb(flt=flt, page=page - 1).pack())
            nav_count += 1
        b.button(text=f"{page + 1}/{total_pages}", callback_data="adm:noop")
        nav_count += 1
        if page < total_pages - 1:
            b.button(text="▶️", callback_data=HistoryPageCb(flt=flt, page=page + 1).pack())
            nav_count += 1

    b.button(text="◀️ Back to Admin", callback_data="adm:back")

    widths = [4] + [1] * len(orders)
    if nav_count:
        widths.append(nav_count)
    widths.append(1)
    b.adjust(*widths)
    return b.as_markup()


def confirm_payment_kb(order_id: int, buyer_tid: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ Confirm Payment",
        callback_data=ConfirmPaymentCb(order_id=order_id, buyer_tid=buyer_tid).pack(),
    )
    b.button(
        text="❌ Reject Payment",
        callback_data=RejectPaymentCb(order_id=order_id, buyer_tid=buyer_tid).pack(),
    )
    b.adjust(1)
    return b.as_markup()


def confirm_only_kb(order_id: int, buyer_tid: int) -> InlineKeyboardMarkup:
    """After rejection — only Confirm remains to avoid re-rejection spam."""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ Confirm Payment (if user pays again)",
        callback_data=ConfirmPaymentCb(order_id=order_id, buyer_tid=buyer_tid).pack(),
    )
    b.adjust(1)
    return b.as_markup()


def payment_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ I have paid", callback_data=f"user_paid:{order_id}")
    b.adjust(1)
    return b.as_markup()


# ── Product management keyboards ──────────────────────────────────────────────

def products_list_manage_kb(products) -> InlineKeyboardMarkup:
    """List of all products — clicking one opens its edit detail view."""
    b = InlineKeyboardBuilder()
    for p in products:
        stock_icon = "✅" if p.in_stock else "❌"
        label = f"{stock_icon} {p.name} ({int(p.price)}€)"
        b.button(text=label, callback_data=f"adm:prod:{p.id}")
    b.button(text="◀️ Back", callback_data="adm:back")
    b.adjust(1)
    return b.as_markup()


def product_detail_edit_kb(product_id: int, in_stock: bool) -> InlineKeyboardMarkup:
    """All edit options for a single product."""
    stock_label = "✅ In Stock — click to disable" if in_stock else "❌ Out of Stock — click to enable"
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Name",           callback_data=ProductEditCb(product_id=product_id, field="name").pack())
    b.button(text="📝 Description",    callback_data=ProductEditCb(product_id=product_id, field="desc").pack())
    b.button(text="💰 Price",          callback_data=ProductEditCb(product_id=product_id, field="price").pack())
    b.button(text="📸 Photo",          callback_data=ProductEditCb(product_id=product_id, field="photo").pack())
    b.button(text="📦 Max Quantity",   callback_data=ProductEditCb(product_id=product_id, field="max_qty").pack())
    b.button(text="📂 Category",       callback_data=ProductEditCb(product_id=product_id, field="category").pack())
    b.button(text=f"🔄 {stock_label}", callback_data=ProductEditCb(product_id=product_id, field="stock").pack())
    b.button(text="🗑 Delete Product", callback_data=ProductEditCb(product_id=product_id, field="delete").pack())
    b.button(text="◀️ Back to List",  callback_data="adm:manage_products")
    b.adjust(2, 2, 2, 1, 1, 1)
    return b.as_markup()


def products_list_kb(products, action: str) -> InlineKeyboardMarkup:
    """Legacy list used for stock-toggle view."""
    b = InlineKeyboardBuilder()
    for p in products:
        stock_icon = "✅" if p.in_stock else "❌"
        label = f"{stock_icon} {p.name} ({int(p.price)}€)"
        b.button(text=label, callback_data=ProductActionCb(action=action, product_id=p.id).pack())
    b.button(text="◀️ Back", callback_data="adm:back")
    b.adjust(1)
    return b.as_markup()


def categories_manage_kb(categories) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Add Category", callback_data="adm:add_category")
    for cat in categories:
        b.button(text=f"✏️ {cat.name}", callback_data=CategoryActionCb(action="rename", slug=cat.slug).pack())
        b.button(text=f"🗑 {cat.name}", callback_data=CategoryActionCb(action="delete", slug=cat.slug).pack())
    b.button(text="◀️ Back", callback_data="adm:back")
    b.adjust(1, *([2] * len(categories)), 1)
    return b.as_markup()


def category_select_kb(categories, prefix: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat in categories:
        b.button(text=cat.name, callback_data=f"{prefix}:{cat.slug}")
    b.adjust(1)
    return b.as_markup()
