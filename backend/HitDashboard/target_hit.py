import os
from dotenv import load_dotenv
import psycopg2
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime, date, time
import pytz
from typing import Optional
from datetime import timedelta,timezone
from datetime import datetime, date, time, timedelta
from models.createdb import get_db_connection
from .models import TradeHistory, TradeTargets
load_dotenv()

TRADE_HISTORY_TABLE = os.getenv("TRADE_HISTORY_TABLE", "trade_history")
TRADE_TARGETS_TABLE = os.getenv("TRADE_TARGETS_TABLE", "trade_targets")
PROFILES_TABLE      = os.getenv("PROFILES_TABLE", "profiles")
IST = pytz.timezone("Asia/Kolkata")
router = APIRouter(tags=["Trades"])

@router.get(
    "/trade_history",
    response_model=list[TradeHistory],
    summary="Trades with target hits for the chosen calendar day",
    tags=["target hits"],
)
def get_trade_history(
    trade_date: date = Query(
        ...,
        alias="start_date",           # 👈 old name still works
        description="Date (YYYY-MM-DD) whose trades you want",
    )
):
    start_dt = IST.localize(datetime.combine(trade_date, time.min))
    next_day = start_dt + timedelta(days=1)
    #trade history
    sql = f"""
    SELECT
      th.id, th.scrip,
      th.tradetype AS "tradeType",
      th.entryprice AS "entryPrice",
      th.stoploss, th.exchangeid AS "exchangeID",
      th.target1, th.target2, th.target3,
      th.created_at, th.updated_at,
      th.position_type, th.exchange_segment,
      th.user_name, p.firstname,
      tt.t1_hit, tt.t2_hit, tt.t3_hit,      
      tt.stoploss_hit, tt.is_monitoring_complete
    FROM {TRADE_HISTORY_TABLE} th
    LEFT JOIN {TRADE_TARGETS_TABLE} tt ON th.id = tt.trade_id
    LEFT JOIN {PROFILES_TABLE}      p  ON th.user_id = p.user_id
    WHERE th.created_at >= %s
      AND th.created_at <  %s
      AND th.source = 'telegram'
      AND (COALESCE(tt.t1_hit,FALSE)
           OR COALESCE(tt.t2_hit,FALSE)
           OR COALESCE(tt.t3_hit,FALSE))
    ORDER BY th.created_at DESC
    """

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (start_dt, next_day))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

@router.get(
    "/trade_targets/user",

    response_model=list[dict],
    summary="Telegram trades with final target duration",
    tags=["target hits"],
)
def get_telegram_target_hits(
    user_id: str = Query(...),
    start_date: date = Query(None),
):
    sql = f"""
    SELECT
        th.id AS trade_id,
        th.scrip,
        th.tradetype AS tradetype,
        th.entryprice AS entryprice,
        th.stoploss,
        th.exchangeid AS exchangeid,
        th.target1,
        th.target2,
        th.target3,
        th.created_at,
        th.position_type,
        th.exchange_segment,
        th.updated_at,
        th.user_name,
        p.firstname,
        tt.t1_hit, tt.t2_hit, tt.t3_hit,
        tt.t1_hit_at, tt.t2_hit_at, tt.t3_hit_at,
        tt.stoploss_hit, tt.is_monitoring_complete
    FROM {TRADE_HISTORY_TABLE} th
    INNER JOIN {TRADE_TARGETS_TABLE} tt
        ON th.id = tt.trade_id
    LEFT JOIN {PROFILES_TABLE} p
        ON th.user_id = p.user_id
    WHERE th.user_id = %s
      AND th.source = 'telegram'
      AND (
            COALESCE(tt.t1_hit, FALSE)
         OR COALESCE(tt.t2_hit, FALSE)
         OR COALESCE(tt.t3_hit, FALSE)
      )
    """

    if start_date:
        sql += " AND DATE(th.created_at) = %s "

    sql += " ORDER BY th.created_at DESC"

    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            if start_date:
                cur.execute(sql, (user_id, start_date))
            else:
                cur.execute(sql, (user_id,))

            cols = [col[0] for col in cur.description]
            results = []

            for row in cur.fetchall():
                trade = dict(zip(cols, row))

                # Decide final hit time
                final_hit = None
                if trade.get("t3_hit") and trade.get("t3_hit_at"):
                    final_hit = trade["t3_hit_at"]
                elif trade.get("t2_hit") and trade.get("t2_hit_at"):
                    final_hit = trade["t2_hit_at"]
                elif trade.get("t1_hit") and trade.get("t1_hit_at"):
                    final_hit = trade["t1_hit_at"]

                # Calculate duration HH:MM:SS
                final_duration = None
                if final_hit:
                    delta = final_hit - trade["created_at"]
                    total_seconds = int(delta.total_seconds())
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    final_duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

                # Clean result
                clean_trade = {
                    "trade_id": trade["trade_id"],
                    "scrip": trade["scrip"],
                    "tradeType": trade["tradetype"],
                    "entryPrice": trade["entryprice"],
                    "stoploss": trade["stoploss"],
                    "exchangeID": trade["exchangeid"],
                    "target1": trade["target1"],
                    "target2": trade["target2"],
                    "target3": trade["target3"],
                    "t1_hit": trade["t1_hit"],
                    "t2_hit": trade["t2_hit"],
                    "t3_hit": trade["t3_hit"],
                    "stoploss_hit": trade["stoploss_hit"],
                    "is_monitoring_complete": trade["is_monitoring_complete"],
                    "final_target_duration": final_duration,
                }

                results.append(clean_trade)

        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))