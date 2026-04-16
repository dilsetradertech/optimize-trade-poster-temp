
import os, uuid, psycopg2, httpx,json,asyncio
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pytz import timezone
from ltp.ltp import get_ltp
from dotenv import load_dotenv  
from models.createdb import get_db_connection   
load_dotenv()

# reuse thread-map & channel-routing from send_trade.py
from send_trade.sendTradeFun import (
    trade_message_ids,
    _channels_for_trade,
    send_trade_update_to_algoapp
)


router = APIRouter()

ALGO_API_KEY       = os.getenv("ALGOAPP_API_KEY")
ALGO_API_URL       = os.getenv("ALGOAPP_API_BASE_URL")

print(f"🔑 ALGO_API_KEY: {'SET' if ALGO_API_KEY else 'NOT SET'}")

TELEGRAM_BOT_TOKEN        = os.getenv("TELEGRAM_TOKEN")

IST = timezone("Asia/Kolkata")

# ─────────────────────────── internal helper
async def _push_exit_notice(trade_id: str, text: str, channels: set[str]):
    if not channels:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Fetch message IDs once
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT telegram_message_id, telegram_message_map
            FROM trade_history
            WHERE id = %s
        """, (trade_id,))
        row = cur.fetchone()

    fallback_mid, telegram_message_map = (row or (None, None))

    message_map = {}
    if telegram_message_map:
        try:
            message_map = (
                telegram_message_map
                if isinstance(telegram_message_map, dict)
                else json.loads(telegram_message_map)
            )
        except Exception:
            message_map = {}

    if trade_id not in trade_message_ids and message_map:
        trade_message_ids[trade_id] = message_map

    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        tasks = []
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
            }
            if mid:
                payload["reply_to_message_id"] = int(mid)

            tasks.append(client.post(url, json=payload))

        await asyncio.gather(*tasks, return_exceptions=True)
#Algo app Stop 
async def notify_algoapp_stop(trade_id: str, partial_profit: float, partial_loss: float):

    if not ALGO_API_URL:
        print("⚠️ ALGO_API_URL not configured")
        return

    url = f"{ALGO_API_URL.rstrip('/')}/trades/stop"

    # Always send tradeId
    payload = {"tradeId": trade_id}

    # Safe float conversion
    pf = float(partial_profit or 0)
    pl = float(partial_loss or 0)

    # Send ONLY one (never both)
    if pf > 0:
        payload["PF"] = f"{pf:.2f}"
    elif pl > 0:
        payload["PL"] = f"{pl:.2f}"
    # else → cost-to-cost → send nothing extra

    headers = {"Content-Type": "application/json"}

    if ALGO_API_KEY:
        headers["x-api-key"] = ALGO_API_KEY

    print("➡ Calling AlgoApp:", url)
    print("📦 Payload:", payload)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(15.0, connect=5.0)
            )
            print(f"⬅ AlgoApp Response [{response.status_code}]: {response.text}")

            response.raise_for_status()

            print(f"🛑 AlgoApp Stop Notified | TradeID: {trade_id}")

        except httpx.HTTPStatusError as exc:
            print(f"❌ AlgoApp Stop failed [{exc.response.status_code}] {exc.response.text}")
        except httpx.HTTPError as exc:
            print("❌ AlgoApp Stop HTTP error:", exc)

@router.put("/stop-monitoring-now/{trade_id}", tags=["Telegram"])
async def stop_monitoring_trade(trade_id: str):
    # Validate UUID
    try:
        tid = str(uuid.UUID(trade_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trade ID format")

    # Single DB connection
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT th.entryprice,
                   th.security_id,
                   th.scrip,
                   th.exchangeid,
                   th.tradetype,
                   th.position_type,
                   tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit,
                   th.source,
                   COALESCE(tt.partial_profit, 0),
                   COALESCE(tt.partial_loss, 0)
            FROM trade_history th
            JOIN trade_targets tt ON th.id = tt.trade_id
            WHERE th.id = %s
        """, (tid,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Trade not found")

        (
            entry, sec_id, scrip, exch, ttype,
            position_type, t1_hit, t2_hit, t3_hit, sl_hit,
            source, existing_pp, existing_pl
        ) = row

        entry = float(entry)
        already_hit = any((t1_hit, t2_hit, t3_hit, sl_hit))

        # Update DB immediately
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

    # Determine partial P/L
    partial_profit, partial_loss = existing_pp, existing_pl
    notice = "🛑 Trade monitoring stopped."

    if not already_hit:
        # Fetch LTP
        ltp = await get_ltp(sec_id)
        diff = ltp - entry
        TICK = 0.05

        if abs(diff) <= TICK:
            partial_profit = partial_loss = 0.0
            notice = "🚨 Close This Position | Exit Cost to Cost"
        elif diff > 0:
            partial_profit, partial_loss = diff, 0.0
            notice = f"✅ Partial Profit Booked from {entry:.2f} → {ltp:.2f}"
        else:
            partial_profit, partial_loss = 0.0, -diff
            notice = f"❌ Close Position | Partial Loss Booked from {entry:.2f} → {ltp:.2f}"

        # Update P/L
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE trade_targets
                SET partial_profit = %s,
                    partial_loss = %s
                WHERE trade_id = %s
            """, (partial_profit, partial_loss, tid))
            conn.commit()

    # Background notifications (non-blocking)
    if source == "telegram":
        channels = _channels_for_trade(scrip, exch, ttype, position_type)
        asyncio.create_task(_push_exit_notice(tid, notice, channels))

    if source == "algoapp":
        level = (
            "SL" if sl_hit else
            "T3" if t3_hit else
            "T2" if t2_hit else
            "T1"
        )
        asyncio.create_task(send_trade_update_to_algoapp(tid, level, 0))
        asyncio.create_task(notify_algoapp_stop(tid, partial_profit, partial_loss))

    return {
        "message": "Monitoring stopped successfully",
        "partial_profit": partial_profit,
        "partial_loss": partial_loss,
        "completed_at": datetime.now(IST).isoformat(),
    }

    # # No target/SL hit → calculate partial P/L
    #     # No target/SL hit → calculate partial P/L
    # ltp = await get_ltp(sec_id)
    # if isinstance(ltp, dict) and "error" in ltp:
    #     raise HTTPException(500, ltp["error"])

    # TICK = 0.05
    # diff = ltp - entry
    # if abs(diff) <= TICK:
    #     partial_profit = partial_loss = 0.0
    #     notice = "🚨 Close This Position | Exit Cost to Cost"
    # elif diff > 0:
    #     partial_profit, partial_loss = diff, 0.0
    #     notice = f"✅ Partial Profit Booked from {entry:.2f} → {ltp:.2f}\nSafe Traders Book Profit, Risky Hold With Strict SL."
    # else:
    #     partial_profit, partial_loss = 0.0, -diff
    #     notice = f"❌ Close Position | Partial Loss Booked from {entry:.2f} → {ltp:.2f}"

    # # ✅ Always update trade & send Telegram notice
    # with get_db_connection() as conn, conn.cursor() as cur:
    #     cur.execute("""
    #         UPDATE trade_targets
    #            SET is_monitoring_complete = TRUE,
    #                partial_profit = %s,
    #                partial_loss   = %s,
    #                completed_at   = NOW()
    #          WHERE trade_id = %s
    #     """, (partial_profit, partial_loss, tid))
    #     cur.execute("UPDATE trade_history SET updated_at = NOW() WHERE id = %s", (tid,))
    #     conn.commit()
    # await notify_algoapp_stop(tid, partial_profit, partial_loss)

    # # ✅ Use unified channel routing logic
    # if source == "telegram":
    #     channels = _channels_for_trade(scrip, exch, ttype, position_type)

    #     print(f"📤 Stop-monitoring channels selected: {channels}")
    #     await _push_exit_notice(tid, notice, channels)

    # return {
    #     "message": "Monitoring stopped (manual exit).",
    #     "partial_profit": partial_profit,
    #     "partial_loss": partial_loss,
    #     "completed_at": datetime.now(IST).isoformat(),
    # }

@router.put("/stop-monitoring-all/{user_id}", tags=["Telegram"])
async def stop_monitoring_all(user_id: str):

    # ---------------------------------------------------
    # 1️⃣ Fetch user role
    # ---------------------------------------------------
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT role_id FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(404, "User not found")

    role = row[0]   # "admin" or "analyst"

    # ---------------------------------------------------
    # 2️⃣ Build trade query based on role
    # ---------------------------------------------------
    if role == "admin" or role == "superadmin":
        # ADMIN → stop ALL active trades
        trade_query = """
            SELECT th.id,
                   th.entryprice,
                   th.security_id,
                   th.scrip,
                   th.exchangeid,
                   th.tradetype,
                   th.position_type,
                   tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit,
                   th.source,
                   th.user_id
            FROM trade_history th
            JOIN trade_targets tt ON th.id = tt.trade_id
            WHERE tt.is_monitoring_complete = FALSE
        """
        params = ()
    else:
        # ANALYST → stop ONLY their trades
        trade_query = """
            SELECT th.id,
                   th.entryprice,
                   th.security_id,
                   th.scrip,
                   th.exchangeid,
                   th.tradetype,
                   th.position_type,
                   tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit,
                   th.source,
                   th.user_id
            FROM trade_history th
            JOIN trade_targets tt ON th.id = tt.trade_id
            WHERE tt.is_monitoring_complete = FALSE
              AND th.user_id = %s
        """
        params = (user_id,)

    # ---------------------------------------------------
    # 3️⃣ Execute trade fetch
    # ---------------------------------------------------
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(trade_query, params)
        rows = cur.fetchall()

    if not rows:
        return {"message": "No trades to stop for this user"}

    results = []

    # ---------------------------------------------------
    # 4️⃣ MAIN LOOP — Same logic as stop-monitoring-now
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
        ltp_response = await get_ltp(sec_id)
        if isinstance(ltp_response, dict):
            error_msg = ltp_response.get("error", "Unknown LTP error")
            print(f"❌ LTP fetch failed for security_id {sec_id}: {error_msg}")

            # Fallback to cost-to-cost exit to avoid breaking the stop-all flow
            ltp = entry
        else:
            try:
                ltp = float(ltp_response)
            except (TypeError, ValueError):
                print(f"❌ Invalid LTP value for security_id {sec_id}: {ltp_response}")
                ltp = entry  # Safe fallback

        TICK = 0.05
        diff = ltp - entry

        if abs(diff) <= TICK:
            partial_profit = partial_loss = 0
            notice = "🚨 Close This Position | Exit Cost to Cost"
        elif diff > 0:
            partial_profit = diff
            partial_loss = 0
            notice = f"✅ Partial Profit Booked from {entry:.2f} → {ltp:.2f}"
        else:
            partial_profit = 0
            partial_loss = -diff
            notice = f"❌ Close Position | Partial Loss Booked from {entry:.2f} → {ltp:.2f}"

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
