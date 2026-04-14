import psycopg2
import uuid
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import os, httpx
from models.createdb import get_db_connection
from user.user_model import (
    SettingsResponse,
    UpdateSettingsRequest)

load_dotenv()
telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
app = FastAPI(title="Settings API")
router = APIRouter()


# Get Settings API
@router.get("/settings/{user_id}", response_model=SettingsResponse, tags=["Settings Management"])
async def get_user_settings(user_id: str):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, sl, t1, t2, t3, targetBy FROM settings WHERE user_id = %s", (user_id,))
        settings = cur.fetchone()

        if not settings:
            raise HTTPException(status_code=404, detail="Settings not found for the user")

        return dict(zip (
            ["user_id", "sl", "t1", "t2", "t3", "targetBy"],
            settings
        ))
         
    finally:
        cur.close()
        conn.close()


# Update Settings API
@router.put("/settings/{user_id}", response_model=SettingsResponse, tags=["Settings Management"])
async def update_user_settings(user_id: str, request: UpdateSettingsRequest):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT user_id FROM settings WHERE user_id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Settings not found for the user")

        cur.execute(
            "UPDATE settings SET sl = %s, t1 = %s, t2 = %s, t3 = %s, targetBy = %s WHERE user_id = %s",
            (request.sl, request.t1, request.t2, request.t3, request.targetBy, user_id)
        )

        conn.commit()

        return {"user_id": user_id, **request.dict()}
    finally:
        cur.close()
        conn.close()


async def delete_trade_telegram_messages(trade_id: str):
    try:
        #  Fetch telegram_message_map
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT telegram_message_map
                FROM trade_history
                WHERE id = %s
                """,
                (trade_id,),
            )
            row = cur.fetchone()

        if not row or not row[0]:
            print(f" No telegram_message_map for trade {trade_id}")
            return
        telegram_map = row[0] 

        # Delete message from each channel
        async with httpx.AsyncClient() as client:
            for chat_id, message_id in telegram_map.items():
                url = f"https://api.telegram.org/bot{telegram_bot_token}/deleteMessage"
                payload = {
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                }

                resp = await client.post(url, json=payload)

                if resp.status_code == 200:
                    print(f" Deleted Telegram msg {message_id} from {chat_id}")
                else:
                    print(
                        f" Failed delete chat {chat_id}, msg {message_id}: {resp.text}"
                    )

    except Exception as e:
        print(f" Error deleting telegram messages for trade {trade_id}: {e}")


# Delete trade with Telegram message
@router.delete("/trade-msg-delete/{trade_id}/telegram")
async def delete_trade_telegram_api(trade_id: str):
    try:
        await delete_trade_telegram_messages(trade_id)

        # Delete trade from database
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM trade_history WHERE id = %s RETURNING id",
                (trade_id,),
            )
            deleted = cur.fetchone()
            conn.commit()

        if not deleted:
            raise HTTPException(status_code=404, detail="Trade not found")

        print(f" Trade {trade_id} deleted from DB")

        return {
            "status": "success",
            "trade_id": trade_id,
            "message": "Trade deleted from Telegram and database"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
