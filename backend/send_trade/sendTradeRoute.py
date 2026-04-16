
import asyncio,httpx, json
from models.createdb import get_db_connection
from fastapi import APIRouter, HTTPException, Form, UploadFile, File
from typing import Optional
import uuid
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton,Message
from aiogram import Bot, Dispatcher
from send_trade.send_trade_model import TradeData, TradeComment
from ltp.dhan_ws import subscribe_new_trade
import os
from dotenv import load_dotenv  
load_dotenv()

from send_trade.sendTradeFun import (
    get_trade_meta,
    _channels_for_trade,
    _telegram_send_multiple,
    _get_contract_lot_size,
    normalize_tradingview_image,
    map_algo_notification_type,
    get_telegram_channels_from_db,
    send_algoapp_notification,
    trade_message_ids,
    CHANNEL_BUTTONS,
    UPGRADE_BUTTON,
    ALGOAPP_TRADE_URL,
    ALGO_NOTIFY_URL,
    ALGOAPP_API_KEY,
    IST,
)

TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_TOKEN")
router = APIRouter()
async def send_algoapp_notification(trade: TradeData):
    notification_type = map_algo_notification_type(trade)

    payload = {
        "title": f"Buy {trade.scrip}",
        "body": f" Target 1: {trade.target1}\n Target 2: {trade.target2}, Target 3: {trade.target3}",
        "type": notification_type,
        "image": normalize_tradingview_image(trade.chart_url),
    }
    print(f"🔔 Prepared AlgoApp notification payload:")
    headers = {"Content-Type": "application/json"}
    if ALGOAPP_API_KEY:
        headers["x-api-key"] = ALGOAPP_API_KEY

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                ALGO_NOTIFY_URL,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
            resp.raise_for_status()
            print("✅ ✅ ✅AlgoApp notification sent successfully:")
            print("🔔 AlgoApp notification sent:")

        except httpx.HTTPStatusError as e:
            print(
                "❌ AlgoApp notification failed:",
                e.response.status_code,
                e.response.text,
            )


@router.post("/send-trade", tags=["Telegram"])
async def send_trade(trade: TradeData):
    # print("📦 RAW REQUEST BODY:", trade.dict())   # 👈 ADD THIS
    print("🎯 position_type received:", trade.position_type)  # 👈 ADD THIS

    d = trade.dict()
    print("📦 Parsed TradeData:", d)  # 👈 ADD THIS
    source = d["source"]
    instrument, is_future_trade, trade_category = get_trade_meta(
        trade.scrip,
        trade.exchangeID
    )
    
    print("📦 DB Instrument:", instrument)

    # STEP 4
    if instrument in ("FUTCOM", "OPTFUT"):
        exchange_segment = "MCX_COMM"

    elif instrument in ("FUTIDX", "OPTIDX", "FUTSTK", "OPTSTK"):
        exchange_segment = "NSE_FNO" if trade.exchangeID.upper() == "NSE" else "BSE_FNO"

    elif instrument == "EQUITY":
        exchange_segment = "NSE_EQ" if trade.exchangeID.upper() == "NSE" else "BSE_EQ"

    else:
        exchange_segment = None

    d["exchange_segment"] = exchange_segment
    d["instrument"] = instrument

    print("📦 Segment calculated:", exchange_segment)
    d["trade_filter"] = d.get("trade_filter") or "default"

    # Keep numeric types intact for DB and external APIs
    trade_id = trade.id or str(uuid.uuid4())
    d["id"] = trade_id
    now = datetime.now(IST)
    d["trade_given_at"] = now.strftime("%I:%M %p || %Y-%m-%d")

    if source == "telegram":
        entry_price_range = f"{trade.entryPrice*0.98:.1f} - {trade.entryPrice*1.02:.1f}"
        stoploss_str = f"{trade.stoploss:.1f}"
        t1_str = f"{trade.target1:.1f}"
        t2_str = f"{trade.target2:.1f}"
        t3_str = f"{trade.target3:.1f}"

        channels = _channels_for_trade(d["scrip"], d["exchangeID"], d["tradeType"],d["position_type"])
        if not channels:
            raise HTTPException(400, "No Telegram channel configured for this trade")

        action_lable="BUY" if trade.position_type=="LONG" else "SELL"
        Type_lable = "Options Buying" if d["position_type"] == "LONG" else "Options Selling"

        body = f"""
        <b>New F&O Trade Alert :🔥🔥🔊🔊</b>\n
        📌 <b>Trade Details:</b>
        • Enter:  <code>{d['scrip']}</code>
        • Action:  <code>{action_lable}</code>
        • Trade Type:  <b>{d['tradeType']}</b> || {Type_lable}
        • Entry Price Range:  <b>{entry_price_range}</b>
        • Stop Loss:  <b>{stoploss_str}</b>
        • Target 1:  <b>{t1_str}</b>
        • Target 2:  <b>{t2_str}</b>
        • Target 3:  <b>{t3_str}</b>\n
        ⏳ <b>Trade Given at:</b>  {d['trade_given_at']}\n
        📝 <b>Rationale:</b> {d.get('reason') or 'Not Provided'}\n
        📊 <b>Chart:</b> <a href="{d.get('chart_url') or '#'}">View Analysis</a>\n
        ✅ <b>Disclaimer:</b> Trade & invest after reading <a href="https://dilsetrader.in/disclaimer/">DISCLAIMER</a>.\n
        📣 <em>Message by:</em> Gokul Chhabra (SEBI RA: INH000014827)"""
        
        deep_link = f"https://t.me/Auto_Trade_VIP_Bot?start=SO_{d['id']}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Place Order", url=deep_link)]
        ])
        print(f"trade type is {d['tradeType']}")
        # partition channels (handle int/str IDs)
        def _as_int(cid):
            try:
                return int(cid)
            except Exception:
                return None

        all_channels = get_telegram_channels_from_db()

        # 1️⃣ Get only channels relevant to this trade
        channels = _channels_for_trade(
                d["scrip"],
                d["exchangeID"],
                d["tradeType"],
                d["position_type"]
            )


        # 2️⃣ Among these channels, split by 'allow_button'
        with_button = [
            ch["id"]
            for ch in all_channels.values()
            if ch["id"] in channels and ch.get("allow_button")
        ]

        plain = [
            ch["id"]
            for ch in all_channels.values()
            if ch["id"] in channels and not ch.get("allow_button")
        ]

        print(f"📩 With button: {with_button}")
        print(f"📩 Without button: {plain}")

        msg_map = {}

        # send plain (no button)
        if plain:
            r = await _telegram_send_multiple(plain, body, trade_id=d["id"])
            if r:
                msg_map.update(r)

        # send with inline keyboard
        if with_button:
            r = await _telegram_send_multiple(with_button, body, keyboard=keyboard, trade_id=d["id"])
            if r:
                msg_map.update(r)

        # ✅ Save both the first message ID (for fallback) and full channel map
        d["telegram_message_id"] = next(iter(msg_map.values()))
        d["telegram_message_map"] = msg_map

    elif source == "algoapp":
        if trade.position_type == "SHORT":
            print("⚠️ Detected SHORT position - not send on algo app")
            # 🔥 Skip DB insert if repost
        # if not d.get("is_repost"):
        #     _save_trade_history(d)
        # else:
        #     print("♻️ Skipping DB insert for repost")
        #     return {
        #     "message": "SHORT trade saved locally. AlgoApp skipped.",
        #     "id": d["id"]
        # }
        headers = {"Content-Type": "application/json"}
        if ALGOAPP_API_KEY:
            headers["x-api-key"] = ALGOAPP_API_KEY

        posted_at_iso = datetime.now(IST).isoformat(timespec="seconds")
        lot_val = int(trade.lot_size)
        exchange_segment = d["exchange_segment"]

        print("📦 Using saved segment:", exchange_segment)  
        
        trade_type = (
            "MARGIN" if trade.tradeType.upper() == "BTST"
            else "INTRADAY" if trade.tradeType.upper() == "INTRADAY"
            else trade.tradeType.upper()
        )
        
        instrument = instrument or "UNKNOWN"
        
        print(f"============{instrument}=================")
        algo_payload = {
            "tradeId": d["id"],
            "symbol": str(trade.scrip),
            "tradeType": trade_type,
            "securityId": str(trade.security_id),
            "entryPrice": float(trade.entryPrice),
            "sl": float(trade.stoploss),
            "t1": float(trade.target1),
            "t2": float(trade.target2),
            "t3": float(trade.target3),
            "lot": lot_val,
            "exchangeSegment": exchange_segment,  # 👈 This is now remapped
            "instrument": instrument,  # 👈 This is now detected
            "postedAt": posted_at_iso,
        }

        print(f"algo payload of lot size: {algo_payload}")
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    ALGOAPP_TRADE_URL,
                    json=algo_payload,
                    headers=headers,
                    timeout=httpx.Timeout(20.0, connect=10.0)
                )
                resp.raise_for_status()
                print("✅ AlgoApp trade forwarded:", resp.json() if "application/json" in resp.headers.get("content-type", "") else resp.text)
                d["telegram_message_id"] = None
                await send_algoapp_notification(trade)
                print("AlgoApp notification triggered after trade submission.")
            except httpx.HTTPStatusError as exc:
                body = exc.response.text
                print(f"❌ AlgoApp POST failed: {exc.response.status_code} {exc.request.url}\nBody: {body}")
                d["telegram_message_id"] = None

            except httpx.HTTPError as exc:
                print("❌ AlgoApp HTTP error:", exc)
                d["telegram_message_id"] = None


    # Save locally (numeric fields intact)
    _save_trade_history(d)
    # await send_to_custom_group(d)



    asyncio.create_task(
        subscribe_new_trade(d["security_id"], d["exchange_segment"])
    )
    return {"message": "Trade data saved successfully", "id": d["id"]}

@router.post("/trade-comment", tags=["Comment-Monitoring"])
async def post_trade_comment(
    user_id: str = Form(...),
    trade_id: str = Form(...),
    comment: Optional[str] = Form(None),
    image: UploadFile = File(None)
):
    trade_id = trade_id.strip()
    user_id = user_id.strip()
    comment = comment.strip()

    if not trade_id:
        raise HTTPException(status_code=400, detail="Trade ID is required")
    if not comment and not image:
        raise HTTPException(status_code=400, detail="Comment or image required")

    # 🔍 Fetch trade info
# 🔍 Check if user is admin — skip permission check for admins
    with get_db_connection() as conn, conn.cursor() as cur:
        # Check user role
        cur.execute(
            """
            SELECT role_id 
            FROM users 
            WHERE id = %s
            """,
            (user_id,),
        )
        user_role = cur.fetchone()

        # 🟢 Admins can always comment
        if user_role and user_role[0] in ["admin", "superadmin"]:
            print(f"✅ Admin user {user_id} bypassed permission check.")
        else:
            # Analysts must have can_comment=True in permissions table
            cur.execute(
                """
                SELECT can_comment 
                FROM permissions 
                WHERE user_id = %s
                """,
                (user_id,),
            )
            permission = cur.fetchone()

            if not permission or not permission[0]:
                print(f"🚫 Permission denied for user: {user_id}")
                raise HTTPException(
                    status_code=403,
                    detail="Permission denied — You are not allowed to comment.",
                )
        cur.execute(
            """
            SELECT scrip, exchangeID, tradeType, telegram_message_id, telegram_message_map, position_type
            FROM trade_history
            WHERE id = %s
            """,
            (trade_id,),
        )
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Trade ID {trade_id} not found")

    scrip, exchangeID, tradeType, telegram_message_id, telegram_message_map, position_type = row


    if isinstance(telegram_message_map, dict):
        message_map = telegram_message_map
    elif telegram_message_map:
        try:
            message_map = json.loads(telegram_message_map)
        except Exception:
            message_map = {}
    else:
        message_map = {}


    # ✅ Update runtime cache
    if message_map:
        trade_message_ids[trade_id] = message_map

    # 🧭 Find Telegram channels
    channels = _channels_for_trade(scrip, exchangeID, tradeType, position_type)
    if not channels:
        raise HTTPException(status_code=400, detail="No Telegram channels found for this trade")

    print(f"📩 Sending comment for trade {trade_id} to channels: {channels}")

    # 🖼️ Read image bytes if provided
    image_bytes = await image.read() if image else None

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        for cid in channels:
            # ✅ Smart lookup for reply_to_message_id
            mid = (
                trade_message_ids.get(trade_id, {}).get(str(cid))
                or trade_message_ids.get(trade_id, {}).get(cid)
                or message_map.get(str(cid))
                or message_map.get(cid)
                or telegram_message_id  # fallback directly from trade_history
            )

            print(f"🧠 Replying in channel {cid} with message_id: {mid}")

            try:
                if image_bytes:
                    # 🖼️ Send photo
                    files = {"photo": image_bytes}
                    data = {
                        "chat_id": cid,
                        "caption": comment or "",
                        "parse_mode": "HTML",
                    }
                    if mid:
                        data["reply_to_message_id"] = int(mid)

                    r = await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                        data=data,
                        files=files
                    )
                else:
                    # 💬 Send text comment
                    payload = {
                        "chat_id": cid,
                        "text": comment,
                        "parse_mode": "HTML",
                    }
                    if mid:
                        payload["reply_to_message_id"] = int(mid)

                    r = await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json=payload,
                    )

                if r.status_code == 200:
                    print(f"✅ Comment sent to {cid} (reply_to: {mid})")
                else:
                    print(f"❌ Failed to send comment to {cid}: {r.text}")

            except httpx.ConnectTimeout:
                print(f"⏳ Timeout connecting to Telegram API for {cid}")
            except Exception as e:
                print(f"❌ Error sending comment to {cid}: {e}")

    return {"message": "Comment (with optional image) sent successfully", "trade_id": trade_id}

def _save_trade_history(data: dict):
    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trade_history
                (id, scrip, tradetype, entryprice, stoploss, target1, target2, target3,
                 exchangeid,user_id, user_name, security_id, lot_size, source,
                 chart_url, reason, telegram_message_map, position_type,exchange_segment,instrument)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                data["id"],
                data["scrip"],
                data["tradeType"],
                data["entryPrice"],
                data["stoploss"],
                data["target1"],
                data["target2"],
                data["target3"],
                data["exchangeID"],
                data["user_id"],
                data["user_name"],
                data["security_id"],
                data["lot_size"],
                data["source"],
                data.get("chart_url"),
                data.get("reason"),
                json.dumps(data.get("telegram_message_map", {})),
                data["position_type"], 
                data.get("exchange_segment"),
                data.get("instrument")
            ))

            cur.execute("""
                INSERT INTO trade_targets
                (trade_id, t1, t1_hit, t2, t2_hit, t3, t3_hit, source, t1_hit_at, t2_hit_at, t3_hit_at, stoploss_hit, stoploss_hit_at)
                VALUES (%s, %s, FALSE, %s, FALSE, %s, FALSE, %s, %s, %s, %s, FALSE, %s)
            """, (
                data["id"],
                data["target1"],
                data["target2"],
                data["target3"],
                data["source"],
                data.get("t1_hit_at"),
                data.get("t2_hit_at"),
                data.get("t3_hit_at"),
                data.get("stoploss_hit_at")
            ))

            conn.commit()
            cur.execute(
                "SELECT exchange_segment FROM trade_history WHERE id=%s",
                (data["id"],)
            )
            print("🔎 DB VERIFY:", cur.fetchone())
    except Exception as e:
        print("❌ DB insert error:", e)
        raise