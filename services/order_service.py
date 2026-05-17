from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Order, OrderItem, OrderStatus, Product, User

RATE_LIMIT       = 5
RATE_WINDOW_HOURS = 1


async def check_rate_limit(session: AsyncSession, user_id: int) -> tuple[bool, int]:
    """Return (can_place_order, orders_placed_in_last_hour)."""
    since = datetime.now(timezone.utc) - timedelta(hours=RATE_WINDOW_HOURS)
    count = (
        await session.execute(
            select(func.count(Order.id))
            .where(Order.user_id == user_id, Order.created_at >= since)
        )
    ).scalar() or 0
    return count < RATE_LIMIT, count


async def validate_cart(session: AsyncSession, cart: dict) -> tuple[bool, dict, list[str]]:
    """
    Validates cart against current DB state (prices, stock, max quantity).
    Returns (is_valid, updated_cart, list_of_warning_messages).
    """
    is_valid = True
    updated_cart = {}
    messages = []

    if not cart:
        return False, {}, ["Your cart is empty."]

    product_ids = [int(pid) for pid in cart.keys()]
    result = await session.execute(
        select(Product).where(Product.id.in_(product_ids))
    )
    products_db = {str(p.id): p for p in result.scalars()}

    for pid, item in cart.items():
        db_p = products_db.get(pid)
        name = item.get("name", f"Item #{pid}")

        if not db_p or db_p.is_deleted:
            is_valid = False
            messages.append(f"❌ <b>{name}</b> is no longer available and was removed.")
            continue

        if not db_p.in_stock:
            is_valid = False
            messages.append(f"❌ <b>{db_p.name}</b> is out of stock and was removed.")
            continue

        price_db = float(db_p.price)
        if item["price"] != price_db:
            is_valid = False
            messages.append(
                f"⚠️ Price changed for <b>{db_p.name}</b>: {int(item['price'])}€ → {int(price_db)}€"
            )
            item["price"] = price_db

        qty = item["quantity"]
        if qty > db_p.max_quantity:
            is_valid = False
            messages.append(
                f"⚠️ Maximum quantity for <b>{db_p.name}</b> is {db_p.max_quantity}. Reduced from {qty}."
            )
            item["quantity"] = db_p.max_quantity

        item["name"] = db_p.name
        item["max_quantity"] = db_p.max_quantity
        updated_cart[pid] = item

    return is_valid, updated_cart, messages


async def create_order(
    session: AsyncSession,
    user_id: int,
    address: str,
    cart: dict,
) -> Order:
    total = sum(
        Decimal(str(item["price"])) * item["quantity"]
        for item in cart.values()
    )
    
    try:
        order = Order(user_id=user_id, status=OrderStatus.PENDING, total_price=total, address=address)
        session.add(order)
        await session.flush()

        session.add_all(
            OrderItem(
                order_id=order.id,
                product_id=int(pid),
                quantity=item["quantity"],
                price=Decimal(str(item["price"])),
            )
            for pid, item in cart.items()
        )
        await session.commit()
        await session.refresh(order)
        return order
    except Exception:
        await session.rollback()
        raise


async def get_order_with_user(session: AsyncSession, order_id: int) -> Order | None:
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.user), selectinload(Order.items).selectinload(OrderItem.product))
        .where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


async def get_active_orders(session: AsyncSession) -> list[Order]:
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.user), selectinload(Order.items).selectinload(OrderItem.product))
        .where(Order.status.in_(OrderStatus.ACTIVE))
        .order_by(Order.created_at.desc())
    )
    return list(result.scalars())


async def update_order_status(
    session: AsyncSession, order_id: int, new_status: str
) -> Order | None:
    order = await get_order_with_user(session, order_id)
    if not order:
        return None
    order.status = new_status
    await session.commit()
    await session.refresh(order)
    return order


async def get_stats(session: AsyncSession) -> dict:
    total_orders = (await session.execute(select(func.count(Order.id)))).scalar() or 0
    total_revenue = (
        await session.execute(
            select(func.sum(Order.total_price))
            .where(Order.status != OrderStatus.PENDING)
        )
    ).scalar() or Decimal(0)

    status_counts = {}
    for status in [
        OrderStatus.PENDING, OrderStatus.PAID, OrderStatus.PREPARING,
        OrderStatus.DELIVERING, OrderStatus.DELIVERED, OrderStatus.CANCELLED_UNPAID,
    ]:
        count = (
            await session.execute(
                select(func.count(Order.id)).where(Order.status == status)
            )
        ).scalar() or 0
        status_counts[status] = count

    return {"total_orders": total_orders, "total_revenue": total_revenue, "by_status": status_counts}


async def get_user_telegram_id(session: AsyncSession, user_id: int) -> int | None:
    result = await session.execute(select(User.telegram_id).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_orders(session: AsyncSession, user_id: int, limit: int = 10) -> list[Order]:
    """Return up to `limit` orders for a user: active ones first, then by date desc."""
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    orders = list(result.scalars())
    active   = [o for o in orders if o.status in OrderStatus.ACTIVE]
    inactive = [o for o in orders if o.status not in OrderStatus.ACTIVE]
    return active + inactive


async def delete_order(session: AsyncSession, order_id: int) -> Order | None:
    """Hard-delete any non-delivered order and its items. Returns the deleted order or None."""
    from sqlalchemy import delete as sa_delete
    order = await get_order_with_user(session, order_id)
    if not order or order.status == OrderStatus.DELIVERED:
        return None
    await session.execute(sa_delete(OrderItem).where(OrderItem.order_id == order_id))
    await session.delete(order)
    await session.commit()
    return order


async def get_all_orders(
    session: AsyncSession,
    offset: int = 0,
    limit: int = 10,
    filter_status: str = "all",
) -> tuple[list[Order], int]:
    """Return (orders, total_count) with optional status filter and pagination.

    filter_status: "all" | "active" | "completed" | "cancelled"
    """
    q = (
        select(Order)
        .options(selectinload(Order.user), selectinload(Order.items).selectinload(OrderItem.product))
        .order_by(Order.created_at.desc())
    )
    count_q = select(func.count(Order.id))

    if filter_status == "active":
        cond = Order.status.in_(OrderStatus.ACTIVE)
    elif filter_status == "completed":
        cond = Order.status == OrderStatus.DELIVERED
    elif filter_status == "cancelled":
        cond = Order.status == OrderStatus.CANCELLED_UNPAID
    else:
        cond = None

    if cond is not None:
        q = q.where(cond)
        count_q = count_q.where(cond)

    total = (await session.execute(count_q)).scalar() or 0
    orders = list((await session.execute(q.offset(offset).limit(limit))).scalars())
    return orders, total


async def get_orders_pending_warning(session: AsyncSession) -> list[Order]:
    """Pending orders that are 10-20 min old and haven't received a warning yet."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.user))
        .where(
            Order.status == OrderStatus.PENDING,
            Order.warning_sent.is_(False),
            Order.created_at <= now - timedelta(minutes=10),
            Order.created_at > now - timedelta(minutes=20),
        )
    )
    return list(result.scalars())


async def get_orders_to_auto_cancel(session: AsyncSession) -> list[Order]:
    """Pending orders that are 20+ minutes old — to be auto-cancelled."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.user))
        .where(
            Order.status == OrderStatus.PENDING,
            Order.created_at <= now - timedelta(minutes=20),
        )
    )
    return list(result.scalars())
