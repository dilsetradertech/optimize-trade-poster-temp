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


@router.get("/history/all")
def get_active_trades(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_name: Optional[str] = Query(None),
    segment: Optional[str] = Query(None),
    source: Optional[str] = Query(None)
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        query = """
            SELECT 
                th.id, th.scrip, th.tradeType, th.entryPrice, th.stoploss, th.exchangeID, 
                th.target1, th.target2, th.target3, 
                th.created_at AS created_at, 
                th.updated_at AS updated_at, 
                th.user_name, 
                tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit, 
                tt.is_monitoring_complete, 
                p.firstname, 
                tt.partial_profit, tt.partial_loss,
                th.lot_size, th.source, th.position_type, th.exchange_segment,

                -- ✅ TIME DIFF with DAYS
                CONCAT(
                    FLOOR(EXTRACT(EPOCH FROM (COALESCE(th.updated_at, NOW()) - th.created_at)) / 86400), 'd ',
                    LPAD(FLOOR(MOD(EXTRACT(EPOCH FROM (COALESCE(th.updated_at, NOW()) - th.created_at)), 86400) / 3600)::text, 2, '0'), ':',
                    TO_CHAR(
                        (COALESCE(th.updated_at, NOW()) - th.created_at)
                        - FLOOR(EXTRACT(EPOCH FROM (COALESCE(th.updated_at, NOW()) - th.created_at)) / 3600) * INTERVAL '1 hour',
                        'MI:SS'
                    )
                ) AS time_diff

            FROM trade_history th
            LEFT JOIN trade_targets tt ON th.id = tt.trade_id 
            LEFT JOIN profiles p ON th.user_id = p.user_id
            WHERE tt.is_monitoring_complete = TRUE
        """

        filters = []
        values = []

        # ✅ Date filters
        if start_date:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            filters.append("th.created_at >= %s")
            values.append(start_datetime)

        if end_date:
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
            filters.append("th.created_at <= %s")
            values.append(end_datetime)

        # ✅ User filter
        if user_name:
            filters.append("th.user_name = %s")
            values.append(user_name)

        # ✅ Source filter
        if source:
            filters.append("th.source = %s")
            values.append(source)

        # ✅ Segment filter
        segment_mapping = {
            "INDEX": "OPTIDX",
            "STOCK": "OPTSTK",
            "MCX": "OPTFUT",
            "CNC": "EQUITY"
        }

        instrument_name = segment_mapping.get(segment.upper()) if segment else None
        scrips = []

        if instrument_name:
            duck_conn = duckdb.connect("options_trade_poster.db")
            duck_cursor = duck_conn.cursor()

            duck_cursor.execute("""
                SELECT DISTINCT SEM_CUSTOM_SYMBOL 
                FROM instruments 
                WHERE SEM_INSTRUMENT_NAME = ?
            """, (instrument_name,))

            scrip_rows = duck_cursor.fetchall()
            scrips = [row[0] for row in scrip_rows]

            if scrips:
                filters.append("th.scrip = ANY(%s)")
                values.append(scrips)

            duck_cursor.close()
            duck_conn.close()

        # ✅ Apply filters
        if filters:
            query += " AND " + " AND ".join(filters)

        query += " ORDER BY th.created_at DESC"

        # ✅ Execute
        cursor.execute(query, tuple(values))
        trades = cursor.fetchall()

        # ✅ Response
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
                    if row[10]
                    else None
                ),
                "user_name": row[11],
                "t1_hit": row[12],
                "t2_hit": row[13],
                "t3_hit": row[14],
                "stoploss_hit": row[15],
                "is_monitoring": row[16],
                "firstname": row[17] if row[17] else "Unknown",
                "partial_profit": row[18],
                "partial_loss": row[19],
                "lot_size": row[20],
                "source": row[21],
                "position_type": row[22],
                "exchange_segment": row[23],
                "time_diff": row[24]
            }
            for row in trades
        ]

    finally:
        cursor.close()
        conn.close()

@router.get("/history/all-page")
def get_active_trades(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_name: Optional[str] = Query(None),
    segment: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, le=100)
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        query = """
            SELECT 
                th.id, th.scrip, th.tradeType, th.entryPrice, th.stoploss, th.exchangeID, 
                th.target1, th.target2, th.target3, 
                th.created_at AS created_at, 
                th.updated_at AS updated_at, 
                th.user_name, 
                tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit, 
                tt.is_monitoring_complete, 
                p.firstname, 
                tt.partial_profit, tt.partial_loss,
                th.lot_size, th.source, th.position_type, th.exchange_segment,

                -- ✅ TIME DIFF with DAYS
                CONCAT(
                    FLOOR(EXTRACT(EPOCH FROM (COALESCE(th.updated_at, NOW()) - th.created_at)) / 86400), 'd ',
                    LPAD(FLOOR(MOD(EXTRACT(EPOCH FROM (COALESCE(th.updated_at, NOW()) - th.created_at)), 86400) / 3600)::text, 2, '0'), ':',
                    TO_CHAR(
                        (COALESCE(th.updated_at, NOW()) - th.created_at)
                        - FLOOR(EXTRACT(EPOCH FROM (COALESCE(th.updated_at, NOW()) - th.created_at)) / 3600) * INTERVAL '1 hour',
                        'MI:SS'
                    )
                ) AS time_diff

            FROM trade_history th
            LEFT JOIN trade_targets tt ON th.id = tt.trade_id 
            LEFT JOIN profiles p ON th.user_id = p.user_id
            WHERE tt.is_monitoring_complete = TRUE
        """

        filters = []
        values = []

        # ✅ Date filters
        if start_date:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            filters.append("th.created_at >= %s")
            values.append(start_datetime)

        if end_date:
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
            filters.append("th.created_at <= %s")
            values.append(end_datetime)

        # ✅ User filter
        if user_name:
            filters.append("th.user_name = %s")
            values.append(user_name)

        # ✅ Source filter
        if source:
            filters.append("th.source = %s")
            values.append(source)

        # ✅ Segment filter
        segment_mapping = {
            "INDEX": "OPTIDX",
            "STOCK": "OPTSTK",
            "MCX": "OPTFUT",
            "CNC": "EQUITY"
        }

        instrument_name = segment_mapping.get(segment.upper()) if segment else None
        scrips = []

        if instrument_name:
            duck_conn = duckdb.connect("options_trade_poster.db")
            duck_cursor = duck_conn.cursor()

            duck_cursor.execute("""
                SELECT DISTINCT SEM_CUSTOM_SYMBOL 
                FROM instruments 
                WHERE SEM_INSTRUMENT_NAME = ?
            """, (instrument_name,))

            scrip_rows = duck_cursor.fetchall()
            scrips = [row[0] for row in scrip_rows]

            if scrips:
                filters.append("th.scrip = ANY(%s)")
                values.append(scrips)

            duck_cursor.close()
            duck_conn.close()

        # ✅ Apply filters
        if filters:
            query += " AND " + " AND ".join(filters)

        # ✅ TOTAL COUNT (before LIMIT)
        count_query = "SELECT COUNT(*) FROM (" + query + ") AS total_count"
        cursor.execute(count_query, tuple(values))
        total_count = cursor.fetchone()[0]

        # ✅ PAGINATION
        offset = (page - 1) * page_size
        query += " ORDER BY th.created_at DESC LIMIT %s OFFSET %s"
        values.extend([page_size, offset])
        cursor.execute(query, tuple(values))
        trades = cursor.fetchall()

        # ✅ Response
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
                    if row[10]
                    else None
                ),
                "user_name": row[11],
                "t1_hit": row[12],
                "t2_hit": row[13],
                "t3_hit": row[14],
                "stoploss_hit": row[15],
                "is_monitoring": row[16],
                "firstname": row[17] if row[17] else "Unknown",
                "partial_profit": row[18],
                "partial_loss": row[19],
                "lot_size": row[20],
                "source": row[21],
                "position_type": row[22],
                "exchange_segment": row[23],
                "time_diff": row[24]
            }
            for row in trades
        ]

    finally:
        cursor.close()
        conn.close()


@router.get("/history/{user_id}")
def get_trades(
    user_id: str,
    start_date: str = Query(None),
    end_date: str = Query(None),
    segment: Optional[str] = Query(None)
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        filters = ["th.user_id = %s", "tt.is_monitoring_complete = TRUE"]
        values = [user_id]

        if start_date:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            filters.append("th.created_at >= %s")
            values.append(start_datetime)

        if end_date:
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d")
            end_datetime = end_datetime.replace(hour=23, minute=59, second=59)
            filters.append("th.created_at <= %s")                                                         
            values.append(end_datetime)

        if segment:
            filters.append("th.segment = %s")
            values.append(segment)

        query = f"""
            SELECT 
                th.id, th.scrip, th.tradeType, th.entryPrice, th.stoploss, th.exchangeID, 
                th.target1, th.target2, th.target3, 
                th.created_at, th.updated_at, th.user_name,
                tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit, tt.is_monitoring_complete,
                tt.partial_loss, tt.partial_profit,
                th.source, th.position_type, th.exchange_segment
            FROM trade_history th
            LEFT JOIN trade_targets tt ON th.id = tt.trade_id 
            LEFT JOIN profiles p ON th.user_id = p.user_id
            WHERE {' AND '.join(filters)}
            ORDER BY th.created_at DESC
        """

        cursor.execute(query, tuple(values))
        trades = cursor.fetchall()

        result = []
        for row in trades:
            created_at = row[9]
            updated_at = row[10] or datetime.now()

            # ---------------------------
            # Time difference with days
            # ---------------------------
            delta = updated_at - created_at
            days = delta.days
            hours, remainder = divmod(delta.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            time_diff = f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
            # ---------------------------

            result.append({
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
                    created_at.astimezone(IST).isoformat()
                    if created_at.tzinfo
                    else IST.localize(created_at).isoformat()
                ),
                "updated_at": (
                    updated_at.astimezone(IST).isoformat()
                    if updated_at.tzinfo
                    else IST.localize(updated_at).isoformat()
                ),
                "user_name": row[11],
                "t1_hit": row[12],
                "t2_hit": row[13],
                "t3_hit": row[14],
                "stoploss_hit": row[15],
                "is_monitoring": row[16],
                "partial_loss": row[17],
                "partial_profit": row[18],
                "source": row[19],
                "position_type": row[20],
                "exchange_segment": row[21],
                "time_diff": time_diff
            })

        return result

    finally:
        cursor.close()
        conn.close()

        
        
@router.get("/analyst/current-trades/{user_id}")
def get_trades(user_id: str):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT th.id, th.scrip, th.tradeType, th.entryPrice, th.stoploss, th.exchangeID, 
            th.target1, th.target2, th.target3, th.created_at, th.updated_at, th.user_name,
            th.source,th.position_type,
            tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit, tt.is_monitoring_complete, 
            tt.partial_profit, tt.partial_loss, p.firstname
            FROM trade_history th
            LEFT JOIN trade_targets tt ON th.id = tt.trade_id
            LEFT JOIN profiles p ON th.user_id = p.user_id
            WHERE th.user_id = %s AND tt.is_monitoring_complete = FALSE
            ORDER BY th.created_at DESC
            """,
            (user_id,),
        )

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
                "created_at": row[9],
                "updated_at": row[10],
                "user_name": row[11],
                "source": row[12],
                "position_type": row[13],
                "t1_hit": row[14],
                "t2_hit": row[15],
                "t3_hit": row[16],
                "stoploss_hit": row[17],
                "is_monitoring_complete": row[18],
                "partial_profit": row[19],
                "partial_loss": row[20],
            }
            for row in trades
        ]
    finally:
        cursor.close()
        conn.close()


@router.put("/stop-monitoring/{trade_id}")
def stop_monitoring_trade(trade_id: str):
    """Mark the trade as monitoring complete by setting is_monitoring_complete to TRUE"""
    try:
        trade_uuid = str(uuid.UUID(trade_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trade ID format")

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM trade_history WHERE id = %s", (trade_uuid,))
        trade = cursor.fetchone()

        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")

        cursor.execute(
            "UPDATE trade_targets SET is_monitoring_complete = TRUE WHERE trade_id = %s",
            (trade_uuid,),
        )

        conn.commit()
        return {"message": f"Monitoring stopped for trade {trade_uuid}"}

    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    finally:
        cursor.close()
        conn.close()
