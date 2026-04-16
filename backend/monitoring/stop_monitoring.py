
import os, uuid, httpx, json
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pytz import timezone
from dotenv import load_dotenv  
from ltp.ltp import get_ltp
from models.createdb import get_db_connection
from routes.send_trade import (
    trade_message_ids,
    _channels_for_trade,
    send_trade_update_to_algoapp
)

load_dotenv()
router = APIRouter()

ALGO_API_KEY       = os.getenv("ALGOAPP_API_KEY")
ALGO_API_URL       = os.getenv("ALGOAPP_API_BASE_URL")
TELEGRAM_BOT_TOKEN        = os.getenv("TELEGRAM_TOKEN")
IST = timezone("Asia/Kolkata")

print(f"🔑 ALGO_API_KEY: {'SET' if ALGO_API_KEY else 'NOT SET'}")

# ─────────────────────────── COMMON HELPERS
def _parse_message_map(raw):
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}

def _calculate_pl(entry, ltp):
    TICK = 0.05
    diff = ltp - entry

    if abs(diff) <= TICK:
        return 0.0, 0.0, " Close This Position | Exit Cost to Cost"
    elif diff > 0:
        return diff, 0.0, f" Partial Profit Booked from {entry:.2f} → {ltp:.2f}"
    else:
        return 0.0, -diff, f" Close Position | Partial Loss Booked from {entry:.2f} → {ltp:.2f}"

# ─────────────────────────── TELEGRAM ────────────────────────────────────────

async def _push_exit_notice(trade_id: str, text: str, channels: set[str]):
    if not channels:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT telegram_message_id, telegram_message_map
            FROM trade_history WHERE id = %s
            """, (trade_id,))
        row = cur.fetchone()

    fallback_mid, raw_map = (row or (None, None))
    message_map = _parse_message_map(raw_map)

    if trade_id not in trade_message_ids and message_map:
        trade_message_ids[trade_id] = message_map

    async with httpx.AsyncClient() as client:
        for cid in channels:
            mid = (
                trade_message_ids.get(trade_id, {}).get(str(cid))
                or message_map.get(str(cid))
                or fallback_mid
            )

            payload = {
                "chat_id": cid,
                "text": text,
                "parse_mode": "HTML",
                **({"reply_to_messgae_id": int(mid)} if mid else {})
            }

            r = await client.post(url, json=payload)
            if r.status_code != 200:
                print(f" Failed to send exit notice to {cid}: {r.text}")


# ─────────────────────────── ALGOAPP ────────────────────────────────────────

async def notify_algoapp_stop(trade_id: str, partial_profit: float, partial_loss: float):
    if not ALGO_API_URL:
        return
    
    payload = {"tradeId": trade_id}

    pf, pl = float(partial_profit or 0), float(partial_loss or 0)

    # Send ONLY one (never both)
    if pf > 0:
        payload["PF"] = f"{pf:.2f}"
    elif pl > 0:
        payload["PL"] = f"{pl:.2f}"
    # else → cost-to-cost → send nothing extra

    headers = {"Content-Type": "application/json"}
    if ALGO_API_KEY:
        headers["x-api-key"] = ALGO_API_KEY

    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(
                f"{ALGO_API_URL.rstrip('/')}/trades/stop",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(15.0, connect=5.0)
            )
            r.raise_for_status()
        except Exception as e:
            print(f"Failed to notify algoapp: {e}")


# ─────────────────────────── PUT /stop-monitoring-now/{trade_id}
@router.put("/stop-monitoring-now/{trade_id}", tags=["Telegram"])
async def stop_monitoring_trade(trade_id: str):
    try:
        tid = str(uuid.UUID(trade_id))
    except ValueError:
        raise HTTPException(400, "Invalid trade ID format")

    # fetch trade details
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT th.entryprice, th.security_id, th.scrip, th.exchangeid,
                   th.tradetype, th.position_type,
                   tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit,
                   th.source, tt.partial_profit, tt.partial_loss
              FROM trade_history th
              JOIN trade_targets tt ON th.id = tt.trade_id
             WHERE th.id = %s
        """, (tid,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(404, "Trade not found")

    (entry, sec_id, scrip, exch, ttype, position_type,
        t1_hit, t2_hit, t3_hit, sl_hit,
        source, existing_pp, existing_pl) = row

    entry = float(entry)
    already_hit = any((t1_hit, t2_hit, t3_hit, sl_hit))

    # If any target/SL already hit
    if already_hit:
        # mark monitoring complete
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE trade_targets
                SET is_monitoring_complete = TRUE,
                    completed_at = NOW()
                WHERE trade_id = %s
            """, (tid,))
            cur.execute("""
                UPDATE trade_history
                SET updated_at = NOW()
                WHERE id = %s
            """, (tid,))
            conn.commit()

        # send correct level to AlgoApp
        if source == "algoapp":
            if sl_hit:
                await send_trade_update_to_algoapp(tid, "SL", 0)
            elif t3_hit:
                await send_trade_update_to_algoapp(tid, "T3", 0)
            elif t2_hit:
                await send_trade_update_to_algoapp(tid, "T2", 0)
            elif t1_hit:
                # send T1
                await send_trade_update_to_algoapp(tid, "T1", 0)
                # also call stop API
                await notify_algoapp_stop(tid, existing_pp or 0, existing_pl or 0)

        return {
            "message": "Monitoring stopped (manual exit).",
            "partial_profit": existing_pp,
            "partial_loss": existing_pl,
            "completed_at": datetime.now(IST).isoformat(),
        }

    # No target/SL hit → calculate partial P/L
    ltp = await get_ltp(sec_id)
    if isinstance(ltp, dict) and "error" in ltp:
        raise HTTPException(500, ltp["error"])

    partial_profit, partial_loss, notice = _calculate_pl(entry, ltp)

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE trade_targets
               SET is_monitoring_complete = TRUE,
                   partial_profit = %s,
                   partial_loss   = %s,
                   completed_at   = NOW()
             WHERE trade_id = %s
        """, (partial_profit, partial_loss, tid))

        cur.execute("UPDATE trade_history SET updated_at = NOW() WHERE id = %s", (tid,))
        conn.commit()

        await notify_algoapp_stop(tid, partial_profit, partial_loss)

        if source == "telegram":
            channels = _channels_for_trade(scrip, exch, ttype, position_type)
            await _push_exit_notice(tid, notice, channels)
    
    return {
        "message": "Monitoring stopped (manual exit).",
        "partial_profit": partial_profit,
        "partial_loss": partial_loss,
        "completed_at": datetime.now(IST).isoformat(),
    }


@router.put("/stop-monitoring-all/{user_id}", tags=["Telegram"])
async def stop_monitoring_all(user_id: str):

    # ---------------------------------------------------
    # Fetch user role
    # ---------------------------------------------------
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT role_id FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(404, "User not found")

    role = row[0]   # "admin" or "analyst"

    # ---------------------------------------------------
    # Build trade query based on role
    # ---------------------------------------------------
    if role == "admin" or role == "superadmin":
        # ADMIN → stop ALL active trades
        trade_query = """
            SELECT th.id, th.entryprice, th.security_id, th.scrip, th.exchangeid,
                   th.tradetype, th.position_type, tt.t1_hit, tt.t2_hit, tt.t3_hit,
                   tt.stoploss_hit, th.source, th.user_id
            FROM trade_history th
            JOIN trade_targets tt ON th.id = tt.trade_id
            WHERE tt.is_monitoring_complete = FALSE
        """
        params = ()
    else:
        # ANALYST → stop ONLY their trades
        trade_query = """
            SELECT th.id, th.entryprice, th.security_id, th.scrip, th.exchangeid,
                   th.tradetype, th.position_type, tt.t1_hit, tt.t2_hit, tt.t3_hit,
                   tt.stoploss_hit, th.source, th.user_id
            FROM trade_history th
            JOIN trade_targets tt ON th.id = tt.trade_id
            WHERE tt.is_monitoring_complete = FALSE
              AND th.user_id = %s
        """
        params = (user_id,)

    # ---------------------------------------------------
    #  Execute trade fetch
    # ---------------------------------------------------
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(trade_query, params)
        rows = cur.fetchall()

    if not rows:
        return {"message": "No trades to stop for this user"}
    
    results = []

    # ---------------------------------------------------
    #  MAIN LOOP — Same logic as stop-monitoring-now
    # ---------------------------------------------------
    for row in rows:

        (trade_id, entry, sec_id, scrip, exch, ttype,
            position_type,
            t1_hit, t2_hit, t3_hit, sl_hit,
            source, trade_user_id) = row

        entry = float(entry)
        already_hit = any((t1_hit, t2_hit, t3_hit, sl_hit))

        # --------------------------
        # CASE A — TARGET/SL ALREADY HIT
        # --------------------------
        if already_hit:

            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute("""
                    UPDATE trade_targets
                    SET is_monitoring_complete = TRUE,
                        completed_at = NOW()
                    WHERE trade_id = %s
                """, (trade_id,))
                cur.execute("""
                    UPDATE trade_history
                    SET updated_at = NOW()
                    WHERE id = %s
                """, (trade_id,))
                conn.commit()
            results.append({"trade_id": trade_id, "status": "SL/T hit"})
            continue

        # --------------------------
        # CASE B — PARTIAL EXIT (manual stop)
        # --------------------------
        ltp = await get_ltp(sec_id)
        partial_profit, partial_loss, notice = _calculate_pl(entry, ltp)

        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE trade_targets
                SET is_monitoring_complete = TRUE,
                    partial_profit = %s,
                    partial_loss  = %s,
                    completed_at = NOW()
                WHERE trade_id = %s
            """, (partial_profit, partial_loss, trade_id))

            cur.execute("UPDATE trade_history SET updated_at = NOW() WHERE id=%s", (trade_id,))
            conn.commit()

        # SEND NOTICE
        if source == "telegram":
            channels = _channels_for_trade(scrip, exch, ttype, position_type)
            await _push_exit_notice(trade_id, notice, channels)

        results.append({
            "trade_id": trade_id,
            "partial_profit": partial_profit,
            "partial_loss": partial_loss,
        })

    # ---------------------------------------------------
    return {
        "message": "Stop-all executed successfully",
        "role": role,
        "results": results
    }
