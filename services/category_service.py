from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Category, Product


async def get_all_categories(session: AsyncSession) -> list[Category]:
    result = await session.execute(
        select(Category).where(Category.is_deleted.is_(False)).order_by(Category.id)
    )
    return list(result.scalars())


async def get_category_by_slug(session: AsyncSession, slug: str) -> Category | None:
    result = await session.execute(
        select(Category).where(Category.slug == slug, Category.is_deleted.is_(False))
    )
    return result.scalar_one_or_none()


async def create_category(session: AsyncSession, slug: str, name: str) -> Category | str:
    """Return Category on success or error string."""
    existing = await session.execute(select(Category).where(Category.slug == slug))
    if existing.scalar_one_or_none() is not None:
        return "A category with this slug already exists."
    cat = Category(slug=slug, name=name)
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def rename_category(session: AsyncSession, slug: str, new_name: str) -> bool:
    result = await session.execute(
        select(Category).where(Category.slug == slug, Category.is_deleted.is_(False))
    )
    cat = result.scalar_one_or_none()
    if not cat:
        return False
    cat.name = new_name
    await session.commit()
    return True


async def delete_category(session: AsyncSession, slug: str) -> str:
    """Soft-delete category. Returns error message or empty string on success."""
    product_count = (
        await session.execute(
            select(func.count(Product.id)).where(
                Product.category == slug,
                Product.is_deleted.is_(False),
            )
        )
    ).scalar() or 0

    if product_count > 0:
        return f"Cannot delete: category has {product_count} active product(s)."

    result = await session.execute(
        select(Category).where(Category.slug == slug, Category.is_deleted.is_(False))
    )
    cat = result.scalar_one_or_none()
    if not cat:
        return "Category not found."
    cat.is_deleted = True
    await session.commit()
    return ""
