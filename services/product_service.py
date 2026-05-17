from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Product


async def get_products_by_category(session: AsyncSession, category: str) -> list[Product]:
    result = await session.execute(
        select(Product)
        .where(
            Product.category == category,
            Product.in_stock.is_(True),
            Product.is_deleted.is_(False),
        )
        .order_by(Product.id)
    )
    return list(result.scalars())


async def get_product_by_id(session: AsyncSession, product_id: int) -> Product | None:
    result = await session.execute(
        select(Product).where(Product.id == product_id, Product.is_deleted.is_(False))
    )
    return result.scalar_one_or_none()


async def get_all_products(session: AsyncSession) -> list[Product]:
    result = await session.execute(
        select(Product).where(Product.is_deleted.is_(False)).order_by(Product.category, Product.id)
    )
    return list(result.scalars())


async def create_product(
    session: AsyncSession,
    name: str,
    category: str,
    price: Decimal,
    max_quantity: int,
    description: str | None = None,
) -> Product:
    product = Product(
        name=name,
        category=category,
        price=price,
        max_quantity=max_quantity,
        description=description,
        in_stock=True,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def soft_delete_product(session: AsyncSession, product_id: int) -> bool:
    product = await get_product_by_id(session, product_id)
    if not product:
        return False
    product.is_deleted = True
    await session.commit()
    return True


async def toggle_product_stock(session: AsyncSession, product_id: int) -> bool | None:
    """Toggle in_stock flag. Returns new value or None if not found."""
    product = await get_product_by_id(session, product_id)
    if not product:
        return None
    product.in_stock = not product.in_stock
    await session.commit()
    return product.in_stock


async def update_product_field(session: AsyncSession, product_id: int, **kwargs) -> "Product | None":
    """Update one or more fields on a product. Returns updated product or None if not found."""
    product = await get_product_by_id(session, product_id)
    if not product:
        return None
    for field, value in kwargs.items():
        setattr(product, field, value)
    await session.commit()
    await session.refresh(product)
    return product


async def update_product_price(session: AsyncSession, product_id: int, new_price: Decimal) -> bool:
    product = await get_product_by_id(session, product_id)
    if not product:
        return False
    product.price = new_price
    await session.commit()
    return True
