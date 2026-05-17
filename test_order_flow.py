import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import User, Chat, Message, CallbackQuery, Update
from aiogram import Bot

from db.engine import get_engine, get_session_factory
from db.init_db import init_db
from db.models import Product
from sqlalchemy import select
from handlers.menu import (
    quick_qty_add,
    handle_quantity,
    show_cart,
    select_item,
    goto_catalogue_cb,
    quick_qty_custom
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("SimulationTest")

class MockBot:
    async def send_message(self, *args, **kwargs):
        class MockMsg:
            message_id = 999
        return MockMsg()
    
    async def send_photo(self, *args, **kwargs):
        class MockMsg:
            message_id = 998
        return MockMsg()

    async def delete_message(self, *args, **kwargs):
        pass

class MockMessage:
    def __init__(self, text, user_id=123):
        self.text = text
        self.from_user = User(id=user_id, is_bot=False, first_name="Test")
        self.chat = Chat(id=user_id, type="private")
        self.bot = MockBot()
        self.message_id = 1000

    async def answer(self, text, *args, **kwargs):
        logger.info(f"[Bot Answered]: {text}")
        return MockMessage("MockResponse")
        
    async def edit_reply_markup(self, *args, **kwargs):
        pass
        
    async def edit_text(self, text, *args, **kwargs):
        logger.info(f"[Bot Edited Text]: {text}")
        return MockMessage("MockResponse")

class MockCallback:
    def __init__(self, data, user_id=123):
        self.data = data
        self.from_user = User(id=user_id, is_bot=False, first_name="Test")
        self.message = MockMessage("CallbackMessage", user_id)
        
    async def answer(self, text=None, *args, **kwargs):
        if text:
            logger.info(f"[Callback Answer]: {text}")

async def run_simulation():
    logger.info("Initializing DB with realistic prices...")
    await init_db()
    
    storage = MemoryStorage()
    from aiogram.fsm.storage.base import StorageKey
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=123, user_id=123))
    
    async with get_session_factory()() as session:
        # Get products
        pep = (await session.execute(select(Product).where(Product.name == "Pepperoni"))).scalar()
        water = (await session.execute(select(Product).where(Product.name == "Water 0.5L"))).scalar()
        
        logger.info(f"Loaded Pepperoni ID: {pep.id}, Price: {pep.price}")
        logger.info(f"Loaded Water ID: {water.id}, Price: {water.price}")

        # SIMULATE BUG 1: Add 1 Pepperoni, then add 7.
        logger.info("--- Simulating BUG 1: Quantity Replacement ---")
        cb1 = MockCallback(f"qty:quick:{pep.id}:1")
        await quick_qty_add(cb1, state, session)
        
        # User adds 7 custom quantity
        await state.update_data(pending_product_id=pep.id)
        msg_add_7 = MockMessage("7")
        await handle_quantity(msg_add_7, state, session)
        
        data = await state.get_data()
        cart = data.get("cart", {})
        qty = cart[str(pep.id)]["quantity"]
        logger.info(f"Final Pepperoni Quantity in Cart: {qty} (Expected 7, NOT 8)")
        assert qty == 7, f"Quantity accumulation bug present! Expected 7, got {qty}"

        # SIMULATE BUG 2 & 3: Context Lock and Ghost Items
        logger.info("--- Simulating BUG 2: Context Locking ---")
        # User views Water and clicks 'Custom Qty'
        cb_water = MockCallback(f"qty:custom:{water.id}")
        await quick_qty_custom(cb_water, state, session)
        
        data_after_water = await state.get_data()
        assert data_after_water["pending_product_id"] == water.id
        
        # Now user enters quantity "2"
        msg_add_water = MockMessage("2")
        await handle_quantity(msg_add_water, state, session)
        
        data = await state.get_data()
        cart = data.get("cart", {})
        assert str(water.id) in cart, "Water should be in cart"
        assert cart[str(water.id)]["quantity"] == 2
        logger.info("Water successfully added with quantity 2! Context properly locked to latest view.")

        # Simulate clearing context when opening Catalogue
        cb_catalog = MockCallback("goto:catalogue")
        await goto_catalogue_cb(cb_catalog, state, session)
        data_after_nav = await state.get_data()
        assert data_after_nav.get("pending_product_id") is None, "Pending product ID should be cleared!"
        logger.info("Navigation aggressively cleared pending_product_id context.")

        # SIMULATE BUG 4: Cart display loop prevention
        logger.info("--- Simulating BUG 4: Single Message Cart ---")
        msg_cart = MockMessage("🛒 Cart")
        await show_cart(msg_cart, state)
        data = await state.get_data()
        assert "last_cart_msg_id" in data
        logger.info(f"Cart displayed and saved message_id {data['last_cart_msg_id']} for debouncing/editing.")

        logger.info("✅ ALL SIMULATIONS PASSED SUCCESSFULLY! ✅")

if __name__ == "__main__":
    asyncio.run(run_simulation())
