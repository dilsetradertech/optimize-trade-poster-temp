import psycopg2
import os
from datetime import datetime, timedelta, time
import pytz
from fastapi import APIRouter
import random
from models.createdb import get_db_connection
from routes.send_trade import (
    send_trade_update_to_telegram,
    send_trade_update_to_algoapp,
)

IST = pytz.timezone("Asia/Kolkata")
router = APIRouter()

async def check_and_stop_ws_if_needed():
    from routes.dhan_ws import DHAN_WS, DHAN_SUBSCRIBED

    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT 1
                FROM trade_targets
                WHERE is_monitoring_complete = FALSE
                LIMIT 1
            """)
            active = cur.fetchone()

        if not active:
            print(" No active trades → closing WS")
    except Exception as e:
        print(" WS stop check error:", e)


# ──────────────────────────────────────────────────────────────
# MCX SESSION CHECK
# ──────────────────────────────────────────────────────────────
def mcx_market_open(now: datetime) -> bool:
    t = now.time()
    return time(9, 0) <= t <= time(23, 30)

# ──────────────────────────────────────────────────────────────
# NOTIFY TRADE UPDATE
# ──────────────────────────────────────────────────────────────
async def notify_trade_update(trade_id: str, level: str, price: float):
    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT source
                FROM trade_history
                WHERE id = %s
                """,
                (trade_id,),
            )
            row = cur.fetchone()

        if not row:
            print(f" Trade ID {trade_id} not found.")
            return

        source = row[0]

        if source == "telegram":
            await send_trade_update_to_telegram(trade_id, level, price)

        elif source == "algoapp":
            await send_trade_update_to_algoapp(trade_id, level, price)

        else:
            print(f" Unknown source {source}")

    except Exception as e:
        print(f" Error notifying trade update: {e}")

# ──────────────────────────────────────────────────────────────
# AUTO STOP TIME
# ──────────────────────────────────────────────────────────────
def get_stop_monitoring_time(exchange, tradetype, created_at):
    if created_at is None:
        return None

    if created_at.tzinfo is None:
        created_at = IST.localize(created_at)
    else:
        created_at = created_at.astimezone(IST)

    exchange = exchange.upper()

    if "NSE" in exchange or "BSE" in exchange:
        if tradetype.lower() == "intraday":
            stop_time = created_at.replace(hour=15, minute=25, second=0, microsecond=0)

        elif tradetype.lower() == "btst":
            fourth_day = (created_at + timedelta(days=4)).astimezone(IST)
            stop_time = fourth_day.replace(hour=15, minute=25, second=0, microsecond=0)

        elif tradetype.lower() == "cnc":
            return None
        
    elif "MCX" in exchange:
        if tradetype.lower() == "btst":
            return created_at + timedelta(hours=96)
    return None


# ──────────────────────────────────────────────────────────────
# CORE TRADE LOGIC (CALLED FROM WS)
# ──────────────────────────────────────────────────────────────
async def process_trade_logic(security_id: int, ltp: float):
    symbols = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "⚫", "⚪"]
    symbol = random.choice(symbols)
    print(f"{symbol} ltp {ltp} for this {security_id}")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT th.id, th.stoploss, th.target1, th.target2, th.target3,
                       tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit, tt.is_monitoring_complete,tt.t1_hit_at,tt.t2_hit_at,t3_hit_at,stoploss_hit_at,
                       th.exchangeid, th.tradetype, th.created_at, th.scrip, th.updated_at, th.position_type
                FROM trade_history th
                JOIN trade_targets tt ON th.id = tt.trade_id
                WHERE th.security_id = %s AND tt.is_monitoring_complete = FALSE
            """,(security_id,))
            trades = cur.fetchall()

    for trade in trades:
        (
            trade_id, stoploss, t1, t2, t3,
            t1_hit, t2_hit, t3_hit, stoploss_hit, is_monitoring_complete,
            t1_hit_at, t2_hit_at, t3_hit_at, stoploss_hit_at,
            exchange, tradetype, created_at, scrip, updated_at, position_type
        ) = trade

        updates = []
        stop_monitoring = False
        current_time = datetime.now(IST)
        is_short = (position_type or "LONG").upper() == "SHORT"

        # ── MCX guard ──
        if exchange == "MCX" and tradetype == "BTST":
            if ltp == 0 or not mcx_market_open(current_time):
                continue

        # ── Auto stop ──
        stop_time = get_stop_monitoring_time(exchange, tradetype, created_at)
        if tradetype.lower() != "cnc":
            if stop_time and current_time >= stop_time:
                updates.append("is_monitoring_complete = TRUE")
                updates.append("updated_at = NOW()")
                
        hit_time = datetime.now(IST)
        # ── TARGETS ──
        if not t1_hit:
            if (not is_short and ltp >= t1) or (is_short and ltp <= t1):
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE trade_targets
                            SET t1_hit = TRUE, t1_hit_at = NOW()
                            WHERE trade_id = %s AND t1_hit = FALSE
                        """, (trade_id,))
                        if cur.rowcount > 0:
                            print(f" T1 hit for {trade_id} at LTP {ltp} at {hit_time}")
                            await notify_trade_update(trade_id, "T1", ltp)

        if not t2_hit:
            if (not is_short and ltp >= t2) or (is_short and ltp <= t2):
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE trade_targets
                            SET t2_hit = TRUE, t2_hit_at = NOW()
                            WHERE trade_id = %s AND t2_hit = FALSE
                        """, (trade_id,))
                        if cur.rowcount > 0:
                            print(f" T2 hit for {trade_id} at LTP {ltp} at {hit_time}")
                            await notify_trade_update(trade_id, "T2", ltp)

        if not t3_hit:
            if (not is_short and ltp >= t3) or (is_short and ltp <= t3):
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE trade_targets
                            SET t3_hit = TRUE, 
                                t3_hit_at = NOW()
                            is_monitoring_complete = TRUE
                            WHERE trade_id = %s AND t3_hit = FALSE
                        """,(trade_id,))
                        
                        if cur.rowcount > 0:
                            print(f" T3 hit for {trade_id} at LTP {ltp}")

                            cur.execute("""
                                UPDATE trade_history
                                SET updated_at = NOW()
                                WHERE id = %s
                            """, (trade_id,))
                                    
                            await notify_trade_update(trade_id, "T3", ltp)
                            stop_monitoring = True


        if not stoploss_hit:
            if (not is_short and ltp <= stoploss) or (is_short and ltp >= stoploss):
                print(f" Stoploss hit for {trade_id} at LTP {ltp}")
                updates.append("stoploss_hit = TRUE")
                updates.append("stoploss_hit_at = NOW()")
                stop_monitoring = True

                if not t1_hit:
                    await notify_trade_update(trade_id, "SL", ltp)
                elif t1_hit and not t2_hit:
                    await send_trade_update_to_algoapp(trade_id, "SL", ltp)
                    print(f" SL sent ONLY to AlgoApp for {trade_id}")
                    
                else:
                    print(f" SL ignored for {trade_id} (T2/T3 already hit)")

        # ── SAVE ──
        if updates:
            target_updates = [u for u in updates if not u.startswith("updated_at")]
            with conn.cursor() as cur:
                    if target_updates:
                        cur.execute(f"""
                            UPDATE trade_targets
                            SET {', '.join(target_updates)}
                            WHERE trade_id = %s
                        """, (trade_id,))      
                                    
                    cur.execute("""                    
                        UPDATE trade_history
                        SET updated_at = NOW()
                        WHERE id = %s
                    """, (trade_id,))
                    
                    if stop_monitoring:
                        await check_and_stop_ws_if_needed() 
                    print(f" Updated Trade {trade_id}")

        conn.commit()            