# Piatto — Production-Ready Telegram Restaurant Bot

Piatto is a fully featured Telegram bot for restaurant order management. Customers browse a live catalogue, build a cart, and place delivery orders entirely inside Telegram. Admins manage the menu, track orders through their full lifecycle, and confirm payments — all without leaving the app.

---

## Features

- Full ordering flow with cart (browse → add → checkout → pay)
- Admin panel (add/edit/delete products, manage categories, browse orders)
- Payment confirmation system with auto-cancel timer (10-min reminder, 20-min auto-cancel)
- Fraud protection — cart re-validated against the database at checkout (price, stock, quantity)
- Transaction-safe DB operations (SQLAlchemy async with rollback on error)
- Google Sheets order sync
- Input validation and length limits (name 2–64 chars, address 5–500 chars, phone normalisation)
- Rate limiting (5 orders per user per hour)
- Graceful shutdown with SIGTERM handling and DB connection pool cleanup

---

## Tech Stack

| Layer | Technology |
|---|---|
| Bot framework | Python 3.11, aiogram 3.x |
| Database | PostgreSQL / Supabase via SQLAlchemy 2 async + asyncpg |
| Scheduler | APScheduler 3 (in-process background jobs) |
| Sheets sync | Google Sheets API via gspread |
| Config | python-dotenv |
| Hosting | Railway (worker dyno, 24/7 uptime) |

---

## Setup

```bash
git clone https://github.com/your-username/piatto-telegram-bot.git
cd piatto-telegram-bot

pip install -r requirements.txt

cp .env.example .env
# Fill in BOT_TOKEN, ADMIN_ID, DATABASE_URL (see table below)

python main.py
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `ADMIN_ID` | ✅ | Your Telegram numeric user ID |
| `DATABASE_URL` | ✅ | PostgreSQL connection string (`postgresql+asyncpg://…`) |
| `LOGO_URL` | optional | Public URL for the welcome message image |
| `SUPABASE_URL` | optional | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | optional | Supabase service role key |
| `GOOGLE_CREDENTIALS_FILE` | optional | Path to Google service account JSON key |
| `GOOGLE_SHEET_ID` | optional | Google Sheet ID for order sync |

---

## Deployment

Deployed on Railway with 24/7 uptime.

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**.
3. Add a **PostgreSQL** plugin — Railway injects `DATABASE_URL` automatically.
4. Under **Variables**, add `BOT_TOKEN` and `ADMIN_ID`.
5. Railway detects `Procfile` and runs `python main.py` as a worker (no HTTP port needed).
6. The bot creates its own database schema on first start.

---

## Order Status Flow

```
PENDING → PAID → PREPARING → DELIVERING → DELIVERED
                                     ↘ CANCELLED
```

---

## Project Structure

```
piatto-telegram-bot/
├── main.py                  # Entry point, dispatcher, graceful shutdown
├── config.py                # Env var loader
├── Procfile                 # Railway: worker: python main.py
├── runtime.txt              # Railway: python-3.11
├── requirements.txt
├── .env.example
├── handlers/
│   ├── start.py             # /start, /cancel
│   ├── menu.py              # Catalogue, cart, quantity selection
│   ├── checkout.py          # Order flow, payment callbacks
│   └── admin.py             # Full admin panel
├── keyboards/
│   ├── main_menu.py
│   ├── catalog.py
│   └── admin_menu.py
├── services/
│   ├── order_service.py     # Order CRUD, cart validation, rate limiting
│   ├── product_service.py
│   ├── category_service.py
│   ├── user_service.py
│   └── scheduler.py         # APScheduler job wrappers
└── db/
    ├── engine.py            # SQLAlchemy async engine factory
    ├── middleware.py        # Per-update session injection
    ├── models.py            # ORM models
    └── init_db.py           # Table creation + safe migrations
```
