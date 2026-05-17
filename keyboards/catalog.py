from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db.models import Category, Product


def get_categories_keyboard(categories: list[Category]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat in categories:
        b.button(text=cat.name, callback_data=f"category:{cat.slug}")
    b.adjust(1)
    return b.as_markup()


def get_items_keyboard(products: list[Product]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        b.button(text=f"{p.name} — {int(p.price)}€", callback_data=f"item:{p.id}")
    b.button(text="◀️ Back to Categories", callback_data="back_to_categories")
    b.adjust(1)
    return b.as_markup()
