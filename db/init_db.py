from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import get_engine
from db.models import Base, Category, Product

SEED_CATEGORIES = [
    {"slug": "pizza",    "name": "🍕 Pizza"},
    {"slug": "drinks",   "name": "🥤 Drinks"},
    {"slug": "desserts", "name": "🍰 Desserts"},
]

SEED_PRODUCTS = [
    {"name": "Margherita",    "category": "pizza",    "price": 12, "max_quantity": 20, "description": "Tomato sauce, mozzarella, basil"},
    {"name": "Pepperoni",     "category": "pizza",    "price": 14, "max_quantity": 15, "description": "Pepperoni, mozzarella, tomato sauce"},
    {"name": "Four Cheese",   "category": "pizza",    "price": 15, "max_quantity": 15, "description": "Mozzarella, cheddar, gouda, parmesan"},
    {"name": "Cola 0.5L",     "category": "drinks",   "price":  3, "max_quantity": 50, "description": "Coca-Cola 0.5L"},
    {"name": "Water 0.5L",    "category": "drinks",   "price":  2, "max_quantity": 100,"description": "Still mineral water"},
    {"name": "Orange Juice",  "category": "drinks",   "price":  4, "max_quantity": 30, "description": "100% natural orange juice"},
    {"name": "Tiramisu",      "category": "desserts", "price":  8, "max_quantity": 10, "description": "Classic Italian dessert"},
    {"name": "Cheesecake",    "category": "desserts", "price":  7, "max_quantity": 10, "description": "New York style cheesecake"},
    {"name": "Brownie",       "category": "desserts", "price":  5, "max_quantity": 20, "description": "Chocolate brownie"},
]


async def _safe_migrations(conn) -> None:
    """Apply schema changes that create_all cannot handle (type changes, new columns)."""

    # ── Convert orders.status from PostgreSQL enum → VARCHAR ─────────────────
    # Previous code used SQLAlchemy Enum(OrderStatus) which created a PG custom
    # type `orderstatus`.  Current code uses String(20); PG refuses to compare
    # orderstatus = character varying without an explicit cast.
    await conn.execute(text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name  = 'orders'
                  AND column_name = 'status'
                  AND data_type  != 'character varying'
            ) THEN
                ALTER TABLE orders
                    ALTER COLUMN status TYPE VARCHAR(20) USING status::text;
            END IF;
        END $$;
    """))
    # Drop the stale enum type (CASCADE removes any remaining dependency)
    await conn.execute(text("DROP TYPE IF EXISTS orderstatus CASCADE;"))

    # ── products: add columns added in later schema versions ─────────────────
    for stmt in [
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url  VARCHAR(512)",
    ]:
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass

    # ── orders: add warning_sent + scheduler job ID columns ──────────────────
    for stmt in [
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS warning_sent BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS reminder_job_id VARCHAR(64)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS cancel_job_id VARCHAR(64)",
    ]:
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass

    # ── translate any legacy Russian product names to English ─────────────────
    _RU_TO_EN = [
        ("Кола 0.5л",                "Cola 0.5L",           "Coca-Cola 0.5L"),
        ("Вода 0.5л",                "Water 0.5L",          "Still mineral water 0.5L"),
        ("Минеральная вода без газа", "Still Mineral Water", "Still mineral water"),
        ("Тирамису",                 "Tiramisu",            "Classic Italian dessert"),
        ("Чизкейк",                  "Cheesecake",          "New York style cheesecake"),
        ("Брауни",                   "Brownie",             "Chocolate brownie"),
        ("Шоколадный брауни",        "Chocolate Brownie",   "Chocolate brownie"),
        ("Хой",                      "Hoi",                 None),
    ]
    for ru_name, en_name, en_desc in _RU_TO_EN:
        if en_desc:
            await conn.execute(
                text("UPDATE products SET name = :en, description = :desc WHERE name = :ru"),
                {"en": en_name, "desc": en_desc, "ru": ru_name},
            )
        else:
            await conn.execute(
                text("UPDATE products SET name = :en WHERE name = :ru"),
                {"en": en_name, "ru": ru_name},
            )


async def init_db() -> None:
    """Create tables, apply safe migrations, seed initial data."""
    engine = get_engine()

    async with engine.begin() as conn:
        await _safe_migrations(conn)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        # Seed categories
        cat_count = (await session.execute(select(func.count()).select_from(Category))).scalar()
        if cat_count == 0:
            session.add_all(Category(**c) for c in SEED_CATEGORIES)
            await session.commit()

        # Seed products
        prod_count = (await session.execute(select(func.count()).select_from(Product))).scalar()
        if prod_count == 0:
            session.add_all(Product(**p) for p in SEED_PRODUCTS)
            await session.commit()
