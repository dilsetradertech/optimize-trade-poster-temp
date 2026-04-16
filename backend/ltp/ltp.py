import asyncio
from fastapi import APIRouter, FastAPI
import httpx
import os
from dotenv import load_dotenv
from pydantic import BaseModel
from ltp.getlatestToken import get_latest_token
load_dotenv()

ACCESS_TOKEN = get_latest_token()
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")

app = FastAPI()
router = APIRouter()

class LTPRequest(BaseModel):
    security_id: int

client = httpx.AsyncClient(timeout=10)

async def get_ltp(security_id: int, max_retries: int = 5):
    url = "https://api.dhan.co/v2/marketfeed/ltp"

    access_token = get_latest_token()
    if not access_token:
        return {"error": "Access token not found"}

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": access_token,
        "client-id": DHAN_CLIENT_ID,
    }

    payload = {
        exchange: [security_id]
        for exchange in [
            "BSE_FNO", "BSE_EQ", "NSE_FNO", "NSE_EQ", "MCX_COMM", "IDX_I"
        ]
    }

    security_id_str = str(security_id)
    for attempt in range(1, max_retries + 1):
        try:
            response = await client.post(url, json=payload, headers=headers)            

            if response.status_code == 200:
                data = response.json()
                data_map = data.get("data", {})

                for securities in data_map.values():
                    if security_id_str in securities:
                        return securities[security_id_str]["last_price"]
                    
        except Exception as e:
            print(f"Error: {e}")

        await asyncio.sleep(3)

    return {
        "error": f"LTP not found for security ID {security_id} after {max_retries} attempts."
    }

@router.post("/get-ltp")
async def fetch_ltp_api(request: LTPRequest):
    """API to fetch LTP for a specific security_id."""
    ltp_result = await get_ltp(request.security_id)
    return {"security_id": request.security_id, "ltp": ltp_result}
