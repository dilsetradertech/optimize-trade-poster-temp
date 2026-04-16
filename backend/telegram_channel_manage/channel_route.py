import os
import random
import httpx
from datetime import datetime
from fastapi import APIRouter, HTTPException, Body,Query
from dotenv import load_dotenv
from psycopg2 import errors
from models.createdb import get_db_connection
from telegram_channel_manage.channel_model import ( ChannelCreate, ChannelUpdate)


load_dotenv()
router = APIRouter()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
MSG91_API_KEY = os.getenv("MSG91_API_KEY")
MSG91_TEMPLATE_ID = os.getenv("MSG91_TEMPLATE_ID")
USER_PHONE = os.getenv("USER_PHONE")

# ─────────────────────────────────────────────
#  Fetch Channel Name from Telegram
# ─────────────────────────────────────────────

@router.get("/channels/fetch-name", tags=["Channels Operations"])
async def fetch_channel_name(channel_id: str):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Telegram token missing")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChat"

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(url, params={"chat_id": channel_id})
        data = res.json()

    if not data.get("ok"):
        description = data.get("description", "").lower()

        if "chat not found" in description:
            msg = "Bot not authorized to access this "
        elif "unauthorized" in description or "Invalid" in description:
            msg = "Invalid Telegram Token. Please update your TELEGRAM_TOKEN."  
        else:
            msg = f"Telegram API error: {data.get('description', 'unknown error')}"  

        raise HTTPException(status_code=400, detail=msg)     

    return {
        "ok": True,
        "channel_name": data["result"].get("title", "Unnamed Channel"),
    }       
            

# ─────────────────────────────────────────────
#  Add or Update Channel (with toggles)
# ─────────────────────────────────────────────

@router.post("/channels/add", tags=["Channels Operations"])
async def create_channel(channel: ChannelCreate):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO telegram_channels (
                channel_id, channel_key, channel_name,
                allow_mcx, allow_index, allow_stock,
                allow_btst, allow_equity, allow_selling,allow_future, allow_button, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,%s)
            RETURNING id;
            """,
            (
                channel.channel_id,
                channel.channel_key,
                channel.channel_name,
                channel.allow_mcx,
                channel.allow_index,
                channel.allow_stock,
                channel.allow_btst,
                channel.allow_equity,
                channel.allow_selling,
                channel.allow_future,
                channel.allow_button,
                datetime.now(),
            ),
        )

        new_id = cur.fetchone()["id"]
        conn.commit()
        return {"status": True, "message": "Channel created successfully", "id": new_id}

    except errors.UniqueViolation:
        conn.rollback()
        return {"status": False, "message": f"Channel ID {channel.channel_id} already exists"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")

    finally:
        cur.close()
        conn.close()

# ─────────────────────────────────────────────
# 🔹 Get All Channels
# ─────────────────────────────────────────────
@router.get("/channels", tags=["Channels Operations"])
async def get_all_channels():
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM telegram_channels ORDER BY id DESC;")
        rows = cur.fetchall()

        # Extract column names from cursor
        columns = [desc[0] for desc in cur.description]

        # Convert each row tuple to a dictionary
        data = [dict(zip(columns, row)) for row in rows]

    return {"status": True, "data": data}

# ─────────────────────────────────────────────
# 🔹 Get Single Channel by channel_id
# ─────────────────────────────────────────────
@router.get("/channels/{channel_id}", tags=["Channels Operations"])
async def get_channel(channel_id: str):
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM telegram_channels WHERE channel_id = %s;", (channel_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Channel not found")
    return {"status": True, "data": dict(row)}


# ─────────────────────────────────────────────
# 🔹 Update Partial Fields (PATCH)
# ─────────────────────────────────────────────
@router.patch("/channels/{id}", tags=["Channels Operations"])
async def update_channel(id: int, channel: ChannelUpdate):
    """Update one or more fields of a channel using its ID."""
    updates = []
    params = []

    for field, value in channel.dict(exclude_none=True).items():
        updates.append(f"{field} = %s")
        params.append(value)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided for update")

    params.append(id)
    query = f"""
        UPDATE telegram_channels
        SET {', '.join(updates)}
        WHERE id = %s
        RETURNING id;
    """

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(query, tuple(params))
        updated = cur.fetchone()
        if not updated:
            raise HTTPException(status_code=404, detail="Channel not found")
        conn.commit()

    return {"status": True, "message": "Channel updated successfully"}


# ─────────────────────────────────────────────
#  Delete Channel
# ─────────────────────────────────────────────
otp_store = {}

otp_store = {}

def send_otp_via_msg91(phone: str, otp: str):
    """Send OTP via MSG91 using Authkey and Template."""
    if not MSG91_API_KEY or not MSG91_TEMPLATE_ID:
        raise HTTPException(status_code=500, detail="MSG91 credentials missing")

    url = "https://api.msg91.com/api/v5/otp"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
    }
    payload = {
        "template_id": MSG91_TEMPLATE_ID,
        "mobile": f"91{phone}",
        "otp": otp,
        "otp_expiry": 5,
    }

    #  Print OTP to terminal for debugging
    print(f"\n OTP for deleting channel: {otp}\n")

    try:
        response = httpx.post(url, json=payload, headers=headers, params={"authkey": MSG91_API_KEY})
        data = response.json()
        if response.status_code != 200 or not data.get("type") == "success":
            raise Exception(data.get("message", "Failed to send OTP"))
        print(" OTP sent successfully via MSG91")
    except Exception as e:
        print(f" Failed to send OTP via MSG91: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send OTP: {str(e)}")


@router.delete("/channels/{id}", tags=["Channels Operations"])
async def delete_channel(id: int, otp: str | None = Query(None), confirm: bool = Query(False)):
    """Secure delete channel using OTP confirmation."""
    # Step 1: Ask for confirmation
    if not confirm and not otp:
        otp_code = str(random.randint(100000, 999999))
        otp_store[id] = otp_code
        send_otp_via_msg91(USER_PHONE, otp_code)
        return {
            "status": False,
            "message": f"OTP sent to {USER_PHONE}. Please verify to confirm delete.",
            "require_otp": True
        }
    # Step 2: Verify OTP
    if otp:
        if otp_store.get(id) != otp:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM telegram_channels WHERE id = %s RETURNING id;", (id,))
            deleted = cur.fetchone()
            if not deleted:
                raise HTTPException(status_code=404, detail="Channel not found")
            conn.commit()

        otp_store.pop(id, None)
        return {"status": True, "message": f"Channel {id} deleted successfully"}

    raise HTTPException(status_code=400, detail="Missing confirmation or OTP")
