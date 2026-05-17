from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Status constants — stored as plain strings to avoid PostgreSQL enum migration pain
class OrderStatus:
    PENDING          = "pending"
    PAID             = "paid"
    PREPARING        = "preparing"
    DELIVERING       = "delivering"
    DELIVERED        = "delivered"
    CANCELLED_UNPAID = "cancelled_unpaid"

    ACTIVE = {"pending", "paid", "preparing", "delivering"}

    LABELS = {
        "pending":          "⏳ Awaiting Payment",
        "paid":             "💰 Paid",
        "preparing":        "👨‍🍳 Preparing",
        "delivering":       "🚚 Delivering",
        "delivered":        "✅ Delivered",
        "cancelled_unpaid": "❌ Cancelled (unpaid)",
    }

    # Each status → the next one admin can set
    TRANSITIONS = {
        "pending":    "paid",
        "paid":       "preparing",
        "preparing":  "delivering",
        "delivering": "delivered",
    }


class Category(Base):
    __tablename__ = "categories"

    id:         Mapped[int]  = mapped_column(Integer, primary_key=True)
    slug:       Mapped[str]  = mapped_column(String(32), unique=True, nullable=False, index=True)
    name:       Mapped[str]  = mapped_column(String(64), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)

    products: Mapped[list["Product"]] = relationship("Product", back_populates="category_rel",
                                                      foreign_keys="[Product.category]",
                                                      primaryjoin="Category.slug == Product.category")


class User(Base):
    __tablename__ = "users"

    id:          Mapped[int]        = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int]        = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username:    Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone:       Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="user")


class Product(Base):
    __tablename__ = "products"

    id:           Mapped[int]        = mapped_column(Integer, primary_key=True)
    name:         Mapped[str]        = mapped_column(String(128), nullable=False)
    category:     Mapped[str]        = mapped_column(String(32), nullable=False, index=True)
    description:  Mapped[str | None] = mapped_column(Text, nullable=True)
    price:        Mapped[Decimal]    = mapped_column(Numeric(10, 2), nullable=False)
    image_url:    Mapped[str | None] = mapped_column(String(512), nullable=True)
    in_stock:     Mapped[bool]       = mapped_column(Boolean, server_default="true", nullable=False)
    max_quantity: Mapped[int]        = mapped_column(Integer, server_default="10", nullable=False)
    is_deleted:   Mapped[bool]       = mapped_column(Boolean, server_default="false", nullable=False)

    category_rel: Mapped["Category | None"] = relationship(
        "Category", back_populates="products",
        foreign_keys=[category],
        primaryjoin="Product.category == Category.slug",
        viewonly=True,
    )
    order_items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="product")


class Order(Base):
    __tablename__ = "orders"

    id:               Mapped[int]        = mapped_column(Integer, primary_key=True)
    user_id:          Mapped[int]        = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    status:           Mapped[str]        = mapped_column(String(20), server_default="pending", nullable=False)
    total_price:      Mapped[Decimal]    = mapped_column(Numeric(10, 2), nullable=False)
    address:          Mapped[str]        = mapped_column(Text, nullable=False)
    created_at:       Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())
    warning_sent:     Mapped[bool]       = mapped_column(Boolean, server_default="false", nullable=False)
    reminder_job_id:  Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_job_id:    Mapped[str | None] = mapped_column(String(64), nullable=True)

    user:  Mapped["User"]            = relationship("User", back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id:         Mapped[int]     = mapped_column(Integer, primary_key=True)
    order_id:   Mapped[int]     = mapped_column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id: Mapped[int]     = mapped_column(Integer, ForeignKey("products.id"), nullable=False)
    quantity:   Mapped[int]     = mapped_column(Integer, nullable=False)
    price:      Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order:   Mapped["Order"]   = relationship("Order", back_populates="items")
    product: Mapped["Product"] = relationship("Product", back_populates="order_items")
