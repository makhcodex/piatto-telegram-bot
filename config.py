import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN:            str = os.getenv("BOT_TOKEN", "")
ADMIN_ID:             int = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL:         str = os.getenv("DATABASE_URL", "")
LOGO_URL:             str = os.getenv("LOGO_URL", "")
SUPABASE_URL:         str = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
