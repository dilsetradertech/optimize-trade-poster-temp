import os
from fastapi import APIRouter
import psycopg2
import asyncio
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

router =APIRouter()

load_dotenv()

LATEST_TOKEN = None  # 🔁 Global variable to hold the latest token
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")


def get_db_connection():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        database=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        cursor_factory=DictCursor,
    )


def get_latest_token(backend_name="trade-poster"):
    """Fetch the latest valid token once from DB"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
            SELECT access_token FROM api_tokens
            WHERE backend = %s AND expires_at > NOW()
            ORDER BY created_at DESC
            LIMIT 1;
        """
        cur.execute(query, (backend_name,))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if result:
            return result[0]
        else:
            print("⚠️ No valid access token found in database yet.")
            return None
    except Exception as e:
        print(f"❌ Database error in get_latest_token: {e}")
        return None


async def refresh_token_loop(interval_seconds=30, backend_name="trade-poster"):
    """Keep refreshing the global token every 30 seconds"""
    global LATEST_TOKEN
    while True:
        new_token = get_latest_token(backend_name)
        if new_token and new_token != LATEST_TOKEN:
            LATEST_TOKEN = new_token
            print(f"🔄 Token refreshed automatically: {LATEST_TOKEN[:10]}...")
        await asyncio.sleep(interval_seconds)


def get_active_token(backend_name="trade-poster"):
    """Safe way to always get a token: global if set, else fetch immediately"""
    global LATEST_TOKEN
    if not LATEST_TOKEN:
        LATEST_TOKEN = get_latest_token(backend_name)
    return LATEST_TOKEN

@router.get("/check-token", tags=["Token Management"])
def check_token():
    return {"token": LATEST_TOKEN}