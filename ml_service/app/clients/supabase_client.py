import logging
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Supabase is optional for raw SMS capture (POST /api/data, /api/data/bulk).
# Without credentials, `supabase` is None and every Supabase-dependent call
# site is already exception-safe around it (see app/service.py).
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    logger.warning(
        "SUPABASE_URL / SUPABASE_KEY not set — Supabase is disabled. "
        "CSV capture still works; Supabase reads/writes will no-op or fail gracefully."
    )