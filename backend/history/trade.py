import asyncio
import pytz
from fastapi import APIRouter, FastAPI, HTTPException, Path, Query
import httpx
from pydantic import BaseModel
from psycopg2 import sql
import psycopg2
from dotenv import load_dotenv
import os, uuid
from typing import Optional
from datetime import datetime
import os
import math
import duckdb



load_dotenv()
app = FastAPI()

router = APIRouter()

IST = pytz.timezone("Asia/Kolkata")

POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")


# Function to get DB connection (Sync)
def get_db():
    return psycopg2.connect(
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port="5432",
    )


class TradeCreate(BaseModel):
    scrip: str
    tradeType: str
    entryPrice: float
    stoploss: float
    exchangeID: str
    target1: float
    target2: float
    target3: float


class TargetUpdate(BaseModel):
    target: str


@router.get("/analyst/{user_id}", tags=["Trades"])
async def get_trades_by_user(user_id: str):
    conn = get_db()
    cur = conn.cursor()

    # Fetch trades for the given user_id
    cur.execute(
        """
        SELECT id, scrip, tradeType, entryPrice, stoploss, target1, target2, target3, exchangeID, created_at, updated_at 
        FROM trade_history WHERE user_id = %s ORDER BY created_at DESC
        """,
        (user_id,),
    )
    trades = cur.fetchall()

    cur.close()
    conn.close()

    if not trades:
        raise HTTPException(status_code=404, detail="No trades found for this user")

    trade_list = [
        {
            "id": trade[0],
            "scrip": trade[1],
            "tradeType": trade[2],
            "entryPrice": trade[3],
            "stoploss": trade[4],
            "target1": trade[5],
            "target2": trade[6],
            "target3": trade[7],
            "exchangeID": trade[8],
            "created_at": trade[9],
            "updated_at": trade[10],
        }
        for trade in trades
    ]

    return trade_list


@router.get(
    "/current-trades", description="Retrieve all trade records with user settings."
)
def get_trades(
    segment: Optional[str] = Query(None)  # Add segment as a query param
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        # --- SEGMENT FILTER LOGIC (from your history/all) ---
        segment_mapping = {
            "INDEX": "OPTIDX",
            "STOCK": "OPTSTK",
            "MCX": "OPTFUT"
        }

        instrument_name = segment_mapping.get(segment.upper()) if segment else None
        scrips = []

        if instrument_name:
            import duckdb
            duck_conn = duckdb.connect("options_trade_poster.db")
            duck_cursor = duck_conn.cursor()
            duck_cursor.execute("""
                SELECT DISTINCT SEM_CUSTOM_SYMBOL 
                FROM instruments 
                WHERE SEM_INSTRUMENT_NAME = ?
            """, (instrument_name,))
            scrip_rows = duck_cursor.fetchall()
            scrips = [row[0] for row in scrip_rows]
            duck_cursor.close()
            duck_conn.close()

        query = """
            SELECT th.id, th.scrip, th.tradeType, th.entryPrice, th.stoploss, th.exchangeID, 
                   th.target1, th.target2, th.target3, th.created_at, th.updated_at, th.user_name,
                   tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit, tt.is_monitoring_complete,
                   p.firstname, th.source,th.position_type
            FROM trade_history th
            LEFT JOIN trade_targets tt ON th.id = tt.trade_id 
            LEFT JOIN profiles p ON th.user_id = p.user_id
            WHERE tt.is_monitoring_complete = FALSE
        """

        values = []
        if scrips:
            query += " AND th.scrip = ANY(%s)"
            values.append(scrips)

        query += " ORDER BY th.created_at DESC"

        cursor.execute(query, tuple(values) if values else None)
        trades = cursor.fetchall()

        return [
            {
                "id": row[0],
                "scrip": row[1],
                "tradeType": row[2],
                "entryPrice": row[3],
                "stoploss": row[4],
                "exchangeID": row[5],
                "target1": row[6],
                "target2": row[7],
                "target3": row[8],
                "created_at": (
                    row[9].astimezone(IST).isoformat()
                    if row[9].tzinfo
                    else IST.localize(row[9]).isoformat()
                ),
                "updated_at": (
                    row[10].astimezone(IST).isoformat()
                    if row[10].tzinfo
                    else IST.localize(row[10]).isoformat()
                ),
                "user_name": row[11],
                "t1_hit": row[12],
                "t2_hit": row[13],
                "t3_hit": row[14],
                "stoploss_hit": row[15],
                "is_monitoring": row[16],
                "firstname": row[17],
                "source": row[18],
                "position_type": row[19]
            }
            for row in trades
        ]
    finally:
        cursor.close()
        conn.close()

