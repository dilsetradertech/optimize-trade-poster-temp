from send_trade.send_trade_model import TradeData, TradeComment
from models.createdb import get_db_connection
import os, uuid, pytz, httpx, psycopg2,json,asyncio,re,duckdb
from datetime import datetime
from collections import defaultdict
from sqlalchemy.orm import Session 
from typing import Dict, Any ,Optional,List,Set 
from pytz import timezone
from dotenv import load_dotenv
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton,Message
from aiogram import Bot
from instrument.instrument import get_security_id

load_dotenv()

DUCKDB_FILE = "instrument/options_trade_poster.db"
TABLE_NAME = "instruments"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

INDIA_TZ = timezone("Asia/Kolkata")
IST = pytz.timezone("Asia/Kolkata")
now = datetime.now(INDIA_TZ)

BASE_URL = os.getenv("ALGOAPP_API_BASE_URL")

def get_telegram_channels_from_db():
    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT channel_key, channel_id,
                    allow_mcx,
                    allow_index,
                    allow_btst,
                    allow_stock,
                    allow_equity,
                    allow_selling,
                    allow_future,
                    allow_button
                FROM telegram_channels

            """)
            rows = cur.fetchall()

        return {
            row[0].upper(): {
                "id": row[1],
                "allow_mcx": row[2],
                "allow_index": row[3],
                "allow_btst": row[4],
                "allow_stock": row[5],
                "allow_equity": row[6],
                "allow_selling": row[7],
                "allow_future":row[8],
                "allow_button": row[9],
            }
            for row in rows
        }

    except Exception as e:
        print("❌ Failed to fetch Telegram channels:", e)
        return {}
channels = get_telegram_channels_from_db()
trade_message_ids: Dict[str, Dict[str, int]] = defaultdict(dict)

# ── Buttons ───────────────────────────────────────────
CHANNEL_BUTTONS = {
    "PROD_NSE":   {"extend": {"text": "Extend My Plan! ✅", "url": "https://www.dilsetrader.in/subscriptions/premium-dilsetrader"}},

    "PROD_MCX":   {"extend": {"text": "Extend My Plan! ✅", "url": "https://www.dilsetrader.in/subscriptions/mcx-commodity-trading?code=VIP50"}},

    "PROD_BTST":  {"extend": {"text": "Extend My Plan! ✅", "url": "https://www.dilsetrader.in/subscriptions/btst-vip?code=BTST"}},

    "PROD_STOCK": {"extend": {"text": "Extend My Plan! ✅", "url": "https://www.dilsetrader.in/subscriptions/stock-options-vip?code=VIP50"}},
}
UPGRADE_BUTTON = {"text": "Upgrade My Plan! ⭐", "url": "https://www.dilsetrader.in/subscriptions/vip-group-complete-package?code=VIP60"}



ALGOAPP_TRADE_URL = f"{BASE_URL}/trades"
ALGO_NOTIFY_URL=f"{BASE_URL}/admin/notification/signal"
# ALGOAPP_TRADE_URL = "https://seagirt-arched-neely.ngrok-free.dev/api/trades"
ALGOAPP_API_KEY   = os.getenv("ALGOAPP_API_KEY", "").strip()  


def get_trade_meta(symbol: str, exchange: str):
    try:
        with duckdb.connect(DUCKDB_FILE, read_only=True) as conn:
            row = conn.execute(
                f"""
                SELECT SEM_INSTRUMENT_NAME
                FROM {TABLE_NAME}
                WHERE LOWER(SEM_CUSTOM_SYMBOL) = LOWER(?)
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()

        if not row:
            return None, False, None

        instrument = row[0]

        is_future = instrument in ("FUTIDX", "FUTSTK", "FUTCOM")

        if instrument in ("OPTFUT"):
            category = "MCX"
        elif instrument in ("OPTIDX"):
            category = "INDEX"
        elif instrument in ("OPTSTK"):
            category = "STOCK"
        elif instrument == "EQUITY":
            category = "EQUITY"
        else:
            category = None

        return instrument, is_future, category

    except Exception as e:
        print("❌ DuckDB meta error:", e)
        return None, False, None

def _channels_for_trade(scrip: str, exchange: str, trade_type: str, position_type: str = None) -> Set[str]:
    s = (scrip or "").upper()
    e = (exchange or "").upper()
    t = (trade_type or "").strip().lower()
    p_type = (position_type or "").strip().upper()

    instrument, is_future_trade, trade_category = get_trade_meta(s, e)

    all_channels = get_telegram_channels_from_db()
    selected_channels: Set[str] = set()

    print(
        f"[ROUTING] scrip={s} exch={e} tradeType={t} "
        f"| instrument={instrument} category={trade_category} "
        f"| is_future={is_future_trade} | position_type={p_type}"
    )

    # 🟣 PRIORITY 1 — FUTURE (HIGHEST)
    if is_future_trade:
        for ch in all_channels.values():
            if ch.get("allow_future"):
                selected_channels.add(ch["id"])

        print(f"📤 Final selected channels (FUTURE ONLY): {selected_channels}")
        return selected_channels

    # 🟥 PRIORITY 2 — OPTION SELLING / SHORT
    if p_type == "SHORT":
        for ch in all_channels.values():
            if ch.get("allow_selling"):
                selected_channels.add(ch["id"])

        print(f"📤 Final selected channels (SHORT ONLY): {selected_channels}")
        return selected_channels

    # normal routing
    for key, ch in all_channels.items():
        cid = ch["id"]

        # 🟤 MCX
        if trade_category == "MCX":
            if ch.get("allow_mcx"):
                selected_channels.add(cid)
            continue

        # 🔵 INDEX
        if trade_category == "INDEX":
            if t == "intraday" and ch.get("allow_index"):
                selected_channels.add(cid)
            elif t == "btst" and ch.get("allow_btst"):
                selected_channels.add(cid)
            continue

        # 🟡 STOCK
        if trade_category == "STOCK":
            if t == "intraday" and ch.get("allow_stock"):
                selected_channels.add(cid)
            elif t == "btst" and ch.get("allow_btst"):
                selected_channels.add(cid)
            continue

        # 🟢 EQUITY
        if trade_category == "EQUITY":
            if t in ("cnc", "intraday") and ch.get("allow_equity"):
                selected_channels.add(cid)
            elif t == "btst" and ch.get("allow_btst"):
                selected_channels.add(cid)
            continue

    print(f"📤 Final selected channels: {selected_channels}")
    return selected_channels

def _get_contract_lot_size(symbol: str, security_id: int | None = None) -> int:  # fetch lot size for algoapp
    with duckdb.connect(DUCKDB_FILE, read_only=True) as conn:
        if security_id:
            row = conn.execute(
                f"SELECT SEM_LOT_UNITS FROM {TABLE_NAME} WHERE SEM_SMST_SECURITY_ID = ? LIMIT 1",
                (security_id,),
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT SEM_LOT_UNITS FROM {TABLE_NAME}
                WHERE lower(SEM_CUSTOM_SYMBOL) = lower(?) OR lower(SEM_INSTRUMENT_NAME) = lower(?)
                LIMIT 1
                """,
                (symbol, symbol),
            ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def _telegram_send(chats: Set[str], text: str, *,
                         reply_to: Optional[int] = None,
                         trade_id: Optional[str] = None,
                         buttons: Optional[list[dict]] = None) -> Dict[str, int]:
    if not chats:
        return {}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    sent: Dict[str, int] = {}

    async with httpx.AsyncClient() as client:
        for cid in chats:
            payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
            if buttons:
                payload["reply_markup"] = {"inline_keyboard": [[b] for b in buttons]}
            if reply_to:
                payload["reply_to_message_id"] = reply_to

            r = await client.post(url, json=payload)
            if r.status_code == 200:
                message_id = r.json()["result"]["message_id"]
                sent[str(cid)] = message_id
                if trade_id:
                    trade_message_ids[trade_id][str(cid)] = message_id
            else:
                print("❌ Telegram:", r.text)

    return sent
trade_message_ids: Dict[str, Dict[str, int]] = defaultdict(dict)
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = Bot(BOT_TOKEN)

async def _telegram_send_multiple(
    channels: list[int | str],
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
    *,
    trade_id: str | None = None,   # NEW
) -> dict[int | str, int]:
    """
    Sends a message to each channel and returns a map: {channel: message_id}.
    Also saves mapping to trade_message_ids if trade_id is provided.
    """
    results: dict[int | str, int] = {}
    for chat in channels:
        try:
            msg = await bot.send_message(
                chat_id=chat,
                text=text,
                parse_mode=parse_mode,
                reply_markup=keyboard
            )
            results[chat] = msg.message_id
            if trade_id:
                trade_message_ids[trade_id][str(chat)] = msg.message_id  # <- store
        except Exception as e:
            print(f"❌ Failed to send to {chat}: {e!r}")
    return results

def map_algo_notification_type(trade: TradeData) -> str:
    instrument, is_future_trade, category = get_trade_meta(
        trade.scrip,
        trade.exchangeID
    )

    t = trade.tradeType.lower()

    # 🟤 MCX
    if category == "MCX":
        return "MCX"

    # 🔵 INDEX
    if category == "INDEX":
        return "Index"

    # 🟣 FUTURE (optional override if needed)
    if is_future_trade:
        return "Futures"

    # 🟡 STOCK OPTIONS
    if category == "STOCK":
        return "stockOptions"

    # 🔵 BTST
    if t == "btst":
        return "btst"

    # 🟢 DEFAULT
    return "Swing"


def normalize_tradingview_image(url: str | None) -> str | None:
    if not url:
        return None

    # Already correct snapshot URL
    if "s3.tradingview.com/snapshots" in url:
        return url

    match = re.search(r"tradingview\.com/x/([A-Za-z0-9]+)/?", url)
    if not match:
        return url 

    code = match.group(1)          # HHdN7gHj
    first_char = code[0].lower()   # h

    return f"https://s3.tradingview.com/snapshots/{first_char}/{code}.png"

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

async def send_trade_update_to_telegram(trade_id: str, level: str, price: float):
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT scrip, exchangeID, tradeType, telegram_message_id, telegram_message_map, position_type
            FROM trade_history
            WHERE id=%s
            """,
            (trade_id,)
        )

        row = cur.fetchone()

    if not row:
        print("Trade not found:", trade_id)
        return False

    # Unpack all fields
    scrip, exchangeID, tradeType, fallback_mid, telegram_message_map, position_type = row


    # 🩵 NEW: ensure message map loaded from DB
    message_map = {}
    if telegram_message_map:
        if isinstance(telegram_message_map, dict):
            message_map = telegram_message_map
        else:
            try:
                message_map = json.loads(telegram_message_map)
            except Exception:
                message_map = {}

    # 🩵 NEW: refresh runtime cache if empty
    if trade_id not in trade_message_ids and message_map:
        trade_message_ids[trade_id] = message_map

    # Determine channels as usual
    channels = _channels_for_trade(scrip, exchangeID, tradeType, position_type)


    if not channels:
        return False
    buttons_per_channel = {}
    if level == "T3":
        all_channels = get_telegram_channels_from_db()

        for key, ch in all_channels.items():
            cid = ch["id"]

            # normalize key for lookup
            normalized_key = key.upper().replace("TELEGRAM_CHANNEL_", "")
            extend_btn = CHANNEL_BUTTONS.get(normalized_key, {}).get("extend")

            # fallback protection
            if not extend_btn and "MCX" in normalized_key:
                extend_btn = CHANNEL_BUTTONS.get("PROD_MCX", {}).get("extend")
            elif not extend_btn and "NSE" in normalized_key:
                extend_btn = CHANNEL_BUTTONS.get("PROD_NSE", {}).get("extend")
            elif not extend_btn and "BTST" in normalized_key:
                extend_btn = CHANNEL_BUTTONS.get("PROD_BTST", {}).get("extend")
            elif not extend_btn and "STOCK" in normalized_key:
                extend_btn = CHANNEL_BUTTONS.get("PROD_STOCK", {}).get("extend")

            btns = []
            if extend_btn:
                btns.append({
                    "text": extend_btn["text"],
                    "url": extend_btn["url"]
                })

            btns.append({
                "text": UPGRADE_BUTTON["text"],
                "url": UPGRADE_BUTTON["url"]
            })
            buttons_per_channel[cid] = btns

        # send only to relevant channels
        channels = list(trade_message_ids.get(trade_id, {}).keys())
        if not channels:
            channels = [ch["id"] for ch in all_channels.values()]

    # Prepare text
    if level in ("T1", "T2", "T3"):
        text = f"🎯 {level} hit for <b>{scrip}</b> at <b>{price:.2f}</b> 🚀"
    elif level == "SL":
        text = f"⚠️ Stoploss hit for <b>{scrip}</b>. <b>CLOSE THIS POSITION! Wait for Next Trade</b>"
    else:
        return False

    async with httpx.AsyncClient() as client:
        for cid in channels:
            msg_map = trade_message_ids.get(trade_id, {})
            mid = msg_map.get(str(cid)) or msg_map.get(int(cid))
            if not mid:
                mid = fallback_mid  # fallback if no per-channel entry
    

            payload = {"chat_id": cid, "text": text, "parse_mode": "HTML"}
            if level == "T3" and cid in buttons_per_channel:
                btns = buttons_per_channel[cid]
                print(f"💬 Sending T3 buttons to {cid}: {buttons_per_channel[cid]}")

                # 🩵 Force both buttons into one row, even if one is missing
                inline_keyboard = [btns] if len(btns) > 1 else [[btns[0]]]

                payload["reply_markup"] = {
                    "inline_keyboard": inline_keyboard
                }


            if mid:
                payload["reply_to_message_id"] = int(mid)
            else:
                print(f"⚠️ No reply_to_message_id for {cid} — sending as new message")

            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload
            )
    return True


async def send_trade_update_to_algoapp(trade_id: str, level: str, ltp: float):
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM trade_history WHERE id=%s", (trade_id,))
        if not cur.fetchone():
            print("Trade not found:", trade_id)
            return False

    if level not in {"T1", "T2", "T3", "SL"}:
        print(f"⚠️ Invalid trade level: {level}")
        return False

    url = ALGOAPP_TRADE_URL + "/update"
    print({"url": url})
    headers = {"Content-Type": "application/json"}
    if ALGOAPP_API_KEY:
        headers["x-api-key"] = ALGOAPP_API_KEY  

    payload = {
        "tradeId": trade_id,
        "level": level,
        "ltp": float(ltp)
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(15.0, connect=5.0)
            )
            response.raise_for_status()
            print(
                f"⛳ ⛳AlgoApp Update Sent | "
                f"TradeID: {trade_id} | "
                f"Level: {level} | "
                f"LTP: {ltp}"
            )
            return True

        except httpx.HTTPStatusError as exc:
            print(
                f"❌ AlgoApp Update failed "
                f"[{exc.response.status_code}] {exc.response.text}"
            )
        except httpx.HTTPError as exc:
            print("❌ AlgoApp HTTP error:", exc)

    return False

