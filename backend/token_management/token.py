from fastapi import APIRouter, HTTPException
import httpx
from datetime import datetime, timedelta,timezone
import pytz
from dotenv import load_dotenv
import traceback
from models.createdb import get_db_connection  
from token_management.token_model import TokenRequest, ManualTokenOnly 

load_dotenv()

router = APIRouter()
IST = pytz.timezone("Asia/Kolkata") 

# DB Helpers
def get_latest_token_from_db():
    """Fetch latest active token"""
    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, client_id, access_token, backend, created_at, expires_at, status
                    FROM api_tokens
                    WHERE status = 'active'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
                return cur.fetchone()
    except Exception:
        traceback.print_exc()
        return None


# Utility function 
def is_token_expiring_in_one_hour(token_row):
    """check if token is expiring within 30 minutes"""
    expires_at = token_row["expires_at"]

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)

    #  Convert to IST ONLY for display
    now_ist = now_utc.astimezone(IST)
    expires_ist = expires_at.astimezone(IST)

    print(f" [IST] Current time  : {now_ist.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" [IST] Token expires : {expires_ist.strftime('%Y-%m-%d %H:%M:%S')}")

    return (expires_at - now_utc) <= timedelta(minutes=30)

    
# Core Logic
async def auto_renew_dhan_token(force: bool = False):
    try:
        print("[AUTO CHECK] Scheduler ticking…")

        token_row = get_latest_token_from_db()
        if not token_row:
            print("No active Dhan token found")
            return None

        time_left = token_row["expires_at"] - datetime.now(timezone.utc)

        if not force and not is_token_expiring_in_one_hour(token_row):
            print(f"Token valid. Not expiring soon. Time left: {time_left}. Skipping renew")
            return None

        print("Token expiring soon. Renewing now...")

        old_token = token_row["access_token"]
        client_id = token_row["client_id"]

        headers = {
            "access-token": old_token,
            "dhanClientId": client_id
        }
            
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get("https://api.dhan.co/v2/RenewToken", headers=headers)

        if res.status_code != 200:
            print("Renew failed:", res.text)
            return None

        payload = res.json()
        new_token = (
            payload.get("token")
            or payload.get("access_token")
            or payload.get("jwtToken")
            or (payload.get("data", {}).get("token") if isinstance(payload.get("data"), dict) else None)
        )

        if not new_token:
            print("Failed to extract token:", payload)
            return None

        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                # Expire old token
                cur.execute(
                    "UPDATE api_tokens SET status='expired' WHERE id=%s",
                    (token_row["id"],)
                )

                # Insert new token
                cur.execute(
                    """
                    INSERT INTO api_tokens 
                    (client_id, access_token, backend, created_at, expires_at, status)
                    VALUES (%s, %s, %s, NOW(), NOW() + INTERVAL '24 hours', 'active')
                    """,
                    (client_id, new_token, token_row["backend"])
                )

        print("Token renewed & saved successfully")

        return {
            "old_token_id": token_row["id"],
            "new_token": new_token
        }

    except Exception:
        traceback.print_exc()
        return None

# Routes
@router.post("/tokens/renew",tags=["Token Management"])
async def manual_renew():
    new_token = await auto_renew_dhan_token()
    print(f"{new_token=}")
    return {"message": "Token renewed", "new_token": new_token}


@router.post("/tokens/renew/force",tags=["Token Management"])
async def force_renew():
    result = await auto_renew_dhan_token(force=True)

    if not result:
        raise HTTPException(status_code=500, detail="Token renewal failed")

    return {
        "status": "success",
        "action": "NEW_ROW_APPENDED",
        "old_token_id": result["old_token_id"],
        "new_token": result["new_token"]
    }


@router.post("/tokens/manual",tags=["Token Management"])
def insert_manual_token(data: ManualTokenOnly):
    try:
        print(" Manual token override started")

        conn = get_db_connection()
        cur = conn.cursor()

        #  Get current active token row
        cur.execute("""
            SELECT id, client_id, backend
            FROM api_tokens
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
        """)
        current = cur.fetchone()

        if not current:
            raise HTTPException(status_code=404, detail="No active token found")

        old_id = current["id"]
        client_id = current["client_id"]
        backend = current["backend"]

        #  Expire old token
        cur.execute("""
            UPDATE api_tokens
            SET status = 'expired', expires_at = NOW()
            WHERE id = %s
            """, (old_id,))

        #  Insert new active token (same client_id & backend)
        cur.execute("""
            INSERT INTO api_tokens
            (client_id, access_token, backend, created_at, expires_at, status)
            VALUES (%s, %s, %s, NOW(), NOW() + INTERVAL '24 hours', 'active')
            RETURNING id
            """, 
            (client_id, data.access_token, backend))

        new_id = cur.fetchone()["id"]

        conn.commit()
        cur.close()
        conn.close()

        print("Manual token inserted successfully")

        return {
            "status": "success",
            "old_token_id": old_id,
            "new_token_id": new_id
        }

    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Manual token insert failed")

