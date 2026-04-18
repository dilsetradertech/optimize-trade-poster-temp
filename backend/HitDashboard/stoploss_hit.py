


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
    "/stoploss_history/all",
    response_model=list[dict],
    summary="Trades with stoploss hit for the chosen calendar day with duration",
    tags=["stoploss hits"],
)
def get_stoploss_history_all(
    trade_date: date = Query(
        ...,
        alias="start_date",
        description="Date (YYYY-MM-DD)",
    )
):
    start_dt = IST.localize(datetime.combine(trade_date, time.min))
    next_day = start_dt + timedelta(days=1)

    sql = f"""
    SELECT
      th.id AS trade_id,
      th.scrip,
      th.tradetype AS "tradeType",
      th.entryprice AS "entryPrice",
      th.stoploss,
      th.exchangeid AS "exchangeID",
      th.target1, th.target2, th.target3,
      th.created_at, th.updated_at,
      th.position_type, th.exchange_segment,
      th.user_name, p.firstname,
      tt.t1_hit, tt.t2_hit, tt.t3_hit,
      tt.stoploss_hit, tt.stoploss_hit_at, tt.is_monitoring_complete
    FROM {TRADE_HISTORY_TABLE} th
    LEFT JOIN {TRADE_TARGETS_TABLE} tt ON th.id = tt.trade_id
    LEFT JOIN {PROFILES_TABLE} p ON th.user_id = p.user_id
    WHERE th.created_at >= %s
      AND th.created_at < %s
      AND th.source = 'telegram'
      AND COALESCE(tt.stoploss_hit, FALSE) = TRUE
      AND COALESCE(tt.t1_hit, FALSE) = FALSE
      AND COALESCE(tt.t2_hit, FALSE) = FALSE
      AND COALESCE(tt.t3_hit, FALSE) = FALSE
    ORDER BY th.created_at DESC
    """

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (start_dt, next_day))
        cols = [c[0] for c in cur.description]
        results = []

        for row in cur.fetchall():
            trade = dict(zip(cols, row))

            final_duration = None
            if trade.get("stoploss_hit") and trade.get("stoploss_hit_at"):
                created_at = trade["created_at"]
                stoploss_hit_at = trade["stoploss_hit_at"]

                # ✅ FIX TIMEZONE
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                if stoploss_hit_at.tzinfo is None:
                    stoploss_hit_at = stoploss_hit_at.replace(tzinfo=timezone.utc)

                delta = stoploss_hit_at - created_at

                total_seconds = int(delta.total_seconds())
                hours, remainder = divmod(total_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)

                final_duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            trade["final_target_duration"] = final_duration
            results.append(trade)

    return results  

@router.get(
    "/stoploss_history/user",
    response_model=list[dict],
    summary="User-wise SL hit trades with duration",
    tags=["stoploss hits"],
)
def get_stoploss_history_user(
    trade_date: date = Query(..., alias="start_date"),
    user_id: str | None = Query(None),
):
    start_dt = IST.localize(datetime.combine(trade_date, time.min))
    next_day = start_dt + timedelta(days=1)

    sql = f"""
    SELECT
      th.id,
      th.scrip,
      th.tradetype,
      th.entryprice,
      th.stoploss,
      th.exchangeid,
      th.target1, th.target2, th.target3,
      th.created_at, th.updated_at,
      th.position_type, th.exchange_segment,
      th.user_name, p.firstname,
      tt.t1_hit, tt.t2_hit, tt.t3_hit,
      tt.stoploss_hit, tt.stoploss_hit_at, tt.is_monitoring_complete
    FROM {TRADE_HISTORY_TABLE} th
    LEFT JOIN {TRADE_TARGETS_TABLE} tt ON th.id = tt.trade_id
    LEFT JOIN {PROFILES_TABLE} p ON th.user_id = p.user_id
    WHERE th.created_at >= %s
      AND th.created_at < %s
      AND th.source = 'telegram'
      AND COALESCE(tt.stoploss_hit, FALSE) = TRUE
      AND COALESCE(tt.t1_hit, FALSE) = FALSE
      AND COALESCE(tt.t2_hit, FALSE) = FALSE
      AND COALESCE(tt.t3_hit, FALSE) = FALSE
    """

    params = [start_dt, next_day]

    if user_id:
        sql += " AND th.user_id = %s"
        params.append(user_id)

    sql += " ORDER BY th.created_at DESC"

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        results = []

        for row in cur.fetchall():
            trade = dict(zip(cols, row))

            final_duration = None
            if trade.get("stoploss_hit") and trade.get("stoploss_hit_at"):
                created_at = trade["created_at"]
                stoploss_hit_at = trade["stoploss_hit_at"]

                # ✅ FIX TIMEZONE
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                if stoploss_hit_at.tzinfo is None:
                    stoploss_hit_at = stoploss_hit_at.replace(tzinfo=timezone.utc)

                delta = stoploss_hit_at - created_at

                total_seconds = int(delta.total_seconds())
                hours, remainder = divmod(total_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)

                final_duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            trade["final_target_duration"] = final_duration
            results.append(trade)

    return results