from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

MENU_TEXTS = frozenset({
    "📋 Catalogue", "🛒 Cart", "✅ Place Order",
    "📦 My Orders",
})


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Catalogue")],
            [KeyboardButton(text="🛒 Cart"), KeyboardButton(text="✅ Place Order")],
            [KeyboardButton(text="📦 My Orders")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
