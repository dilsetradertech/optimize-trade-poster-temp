import json
import os
import struct
import asyncio
import time
import psycopg2
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed
from monitoring.bg_monitoring import process_trade_logic

LTP_QUEUE: asyncio.Queue = asyncio.Queue(maxsize=5000)
LISTENER_LOCK = asyncio.Lock()
router = APIRouter()
FRONTEND_TRADES: dict[int, set] = {}
LTP_STORE: dict[int, float] = {}

DHAN_PARTNER_ID = os.getenv("DHAN_PARTNER_ID")
DHAN_PARTNER_SECRET = os.getenv("DHAN_PARTNER_SECRET")

# ==============================
# FIX 1: WARMUP GUARD
# On every fresh WS connection (including reconnects),
# ignore all ticks for the first 5 seconds.
# Those are stale buffered prices from Dhan — NOT live prices.
# This prevents false T1/T2 hits after restart or reconnect.
# ==============================
WS_CONNECTED_AT: float = 0.0       # epoch time of last WS connect
WARMUP_SECONDS: float = 5.0        # ignore ticks for this long after connect

# ==============================
# DB — thread pool (non-blocking)
# ==============================
def _get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
    )

# ==============================
# GLOBAL STATE
# ==============================
SEGMENT_CACHE: dict[int, str] = {}
DHAN_WS = None
DHAN_WS_LOCK = asyncio.Lock()
DHAN_SUBSCRIBED: set = set()
CONNECTED_CLIENTS: set = set()
DHAN_LISTENER_STARTED = False
SECURITY_SEGMENT_MAP: dict[int, str] = {}
LISTENER_TASK = None
WORKER_TASK = None
WATCHDOG_TASK = None   # FIX 2: watchdog that revives dead tasks

# ==============================
# SEGMENT DISCOVERY
# ==============================
async def discover_segments_bulk(security_ids: list[int]) -> dict[int, str]:
    unknown_ids = [sid for sid in security_ids if sid not in SEGMENT_CACHE]

    if unknown_ids:
        def _query():
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT security_id, exchange_segment
                FROM trade_history
                WHERE security_id = ANY(%s)
                AND exchange_segment IS NOT NULL
            """, (unknown_ids,))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return rows

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, _query)
        for sec_id, segment in rows:
            SEGMENT_CACHE[int(sec_id)] = segment

    return {sid: SEGMENT_CACHE.get(sid) for sid in security_ids}

# ==============================
# PARSER
# ==============================
def parse_dhan_binary(data: bytes):
    try:
        if len(data) < 12:
            return None
        packet_type = data[0]
        if packet_type not in (2, 4):
            return None
        security_id = struct.unpack("<I", data[4:8])[0]
        ltp = struct.unpack("<f", data[8:12])[0]
        if ltp <= 0 or ltp > 100000:
            return None
        return security_id, round(ltp, 2)
    except Exception as e:
        print("Parse error:", e)
        return None

# ==============================
# DHAN WS CONNECTION
# FIX 3: On every fresh connect, record the timestamp
# so the warmup guard knows when to start trusting ticks.
# Also re-subscribes all known instruments on reconnect.
# ==============================
async def get_dhan_ws():
    global DHAN_WS, WS_CONNECTED_AT

    if DHAN_WS is not None:
        try:
            await DHAN_WS.ping()
            return DHAN_WS
        except Exception:
            pass

    async with DHAN_WS_LOCK:
        if DHAN_WS is not None:
            try:
                await DHAN_WS.ping()
                return DHAN_WS
            except Exception:
                pass

        dhan_ws_url = (
            f"wss://api-feed.dhan.co"
            f"?version=2"
            f"&token={DHAN_PARTNER_SECRET}"
            f"&clientId={DHAN_PARTNER_ID}"
            f"&authType=3"
        )
        DHAN_WS = await websockets.connect(
            dhan_ws_url,
            ping_interval=10,
            ping_timeout=5
        )

        # Record connection time — warmup guard uses this
        WS_CONNECTED_AT = time.monotonic()
        print(f" Connected to Dhan Partner WS | warmup {WARMUP_SECONDS}s started")

        # FIX 3: Re-subscribe all instruments after every reconnect.
        # Without this, after a disconnect all subscriptions are lost
        # and no ticks arrive even though the WS appears connected.
        if SECURITY_SEGMENT_MAP:
            await _resubscribe_all(DHAN_WS)

        return DHAN_WS


async def _resubscribe_all(ws):
    """Re-send all subscriptions to Dhan after a reconnect."""
    segment_map: dict[str, list[str]] = {}
    for sec_id, segment in SECURITY_SEGMENT_MAP.items():
        segment_map.setdefault(segment, []).append(str(sec_id))

    for segment, sec_ids in segment_map.items():
        payload = {
            "RequestCode": 15,
            "InstrumentCount": len(sec_ids),
            "InstrumentList": [
                {"ExchangeSegment": segment, "SecurityId": sid}
                for sid in sec_ids
            ]
        }
        await ws.send(json.dumps(payload))
        print(f"♻️  Re-subscribed after reconnect → {segment} | {sec_ids}")

# ==============================
# DHAN LISTENER (recv only)
# ==============================
async def dhan_listener():
    global DHAN_WS
    print("🎧 Dhan listener started")
    try:
        while True:
            try:
                ws = await get_dhan_ws()
                msg = await ws.recv()

                if not isinstance(msg, (bytes, bytearray)):
                    await asyncio.sleep(0)
                    continue

                parsed = parse_dhan_binary(msg)
                if not parsed:
                    await asyncio.sleep(0)
                    continue

                sec_id, ltp = parsed

                # FIX 1: Skip stale buffered ticks during warmup window
                elapsed = time.monotonic() - WS_CONNECTED_AT
                if elapsed < WARMUP_SECONDS:
                    # Still update LTP_STORE so frontend gets current price,
                    # but DO NOT process trade logic with potentially stale data
                    LTP_STORE[sec_id] = ltp
                    await asyncio.sleep(0)
                    continue

                LTP_STORE[sec_id] = ltp

                try:
                    LTP_QUEUE.put_nowait((sec_id, ltp))
                except asyncio.QueueFull:
                    pass

                await asyncio.sleep(0)

            except ConnectionClosed:
                print("⚠ Dhan WS closed. Retrying in 3s...")
                DHAN_WS = None
                await asyncio.sleep(3)

            except Exception as e:
                print(f"Listener error: {e}")
                await asyncio.sleep(3)

    except asyncio.CancelledError:
        print("Dhan listener cancelled")

async def ltp_worker():
    print("⚙️  LTP worker started")
    try:
        while True:
            sec_id, ltp = await LTP_QUEUE.get()

            try:
                asyncio.create_task(process_trade_logic(sec_id, ltp))
            except Exception as e:
                print(f"❌ process_trade_logic error: {e}")

            if sec_id in FRONTEND_TRADES:
                dead = set()
                for client in list(FRONTEND_TRADES[sec_id]):
                    try:
                        if client.client_state == WebSocketState.CONNECTED:
                            await client.send_json({"security_id": sec_id, "ltp": ltp})
                        else:
                            dead.add(client)
                    except Exception:
                        dead.add(client)

                FRONTEND_TRADES[sec_id] -= dead
                if not FRONTEND_TRADES[sec_id]:
                    FRONTEND_TRADES.pop(sec_id, None)

            LTP_QUEUE.task_done()
            await asyncio.sleep(0)

    except asyncio.CancelledError:
        print("LTP worker cancelled")

# ==============================
# FIX 2: WATCHDOG
# Checks every 30s if listener/worker tasks are alive.
# If a task died silently (unhandled exception escaped the loop),
# the watchdog restarts it automatically.
# This is why your WS "stops working" after some time —
# the task crashes and nobody restarts it.
# ==============================
async def watchdog():
    global LISTENER_TASK, WORKER_TASK, DHAN_LISTENER_STARTED
    print("🐕 Watchdog started")
    try:
        while True:
            await asyncio.sleep(30)

            # Check listener
            if LISTENER_TASK is None or LISTENER_TASK.done():
                exc = LISTENER_TASK.exception() if LISTENER_TASK and not LISTENER_TASK.cancelled() else None
                print(f"🔁 Watchdog: listener task dead (exc={exc}), restarting...")
                LISTENER_TASK = asyncio.create_task(dhan_listener())

            # Check worker
            if WORKER_TASK is None or WORKER_TASK.done():
                exc = WORKER_TASK.exception() if WORKER_TASK and not WORKER_TASK.cancelled() else None
                print(f"🔁 Watchdog: worker task dead (exc={exc}), restarting...")
                WORKER_TASK = asyncio.create_task(ltp_worker())

            # Check if we have active trades but WS is gone
            if SECURITY_SEGMENT_MAP and DHAN_WS is None:
                print("🔁 Watchdog: WS is None but trades exist, reconnecting...")
                try:
                    await get_dhan_ws()
                except Exception as e:
                    print(f"🔁 Watchdog reconnect failed: {e}")

    except asyncio.CancelledError:
        print("Watchdog cancelled")

# ==============================
# ENSURE LISTENER + WORKER + WATCHDOG STARTED
# ==============================
async def ensure_listener_started():
    global DHAN_LISTENER_STARTED, LISTENER_TASK, WORKER_TASK, WATCHDOG_TASK

    async with LISTENER_LOCK:
        if DHAN_LISTENER_STARTED:
            return
        DHAN_LISTENER_STARTED = True
        LISTENER_TASK = asyncio.create_task(dhan_listener())
        WORKER_TASK = asyncio.create_task(ltp_worker())
        WATCHDOG_TASK = asyncio.create_task(watchdog())
        print("🚀 Listener + Worker + Watchdog tasks created")

# ==============================
# SUBSCRIBE HELPER
# ==============================
async def subscribe_instruments(dhan_ws, segment_map: dict[str, list[str]]):
    for segment, sec_ids in segment_map.items():
        new_ids = [sid for sid in sec_ids if sid not in DHAN_SUBSCRIBED]
        if not new_ids:
            continue

        payload = {
            "RequestCode": 15,
            "InstrumentCount": len(new_ids),
            "InstrumentList": [
                {"ExchangeSegment": segment, "SecurityId": sid}
                for sid in new_ids
            ]
        }
        await dhan_ws.send(json.dumps(payload))
        print(f"📡 Subscribed {segment} | {new_ids}")

        for sid in new_ids:
            DHAN_SUBSCRIBED.add(sid)
            SECURITY_SEGMENT_MAP[int(sid)] = segment

async def subscribe_new_trade(security_id: int, segment: str):
    sec_id_str = str(security_id)
    if sec_id_str in DHAN_SUBSCRIBED:
        await ensure_listener_started()
        return
    ws = await get_dhan_ws()
    payload = {
        "RequestCode": 15,
        "InstrumentCount": 1,
        "InstrumentList": [{"ExchangeSegment": segment, "SecurityId": sec_id_str}]
    }
    await ws.send(json.dumps(payload))
    DHAN_SUBSCRIBED.add(sec_id_str)
    SECURITY_SEGMENT_MAP[int(sec_id_str)] = segment
    print(f"🆕 Subscribed → {segment} | {sec_id_str}")
    await ensure_listener_started()

# ==============================
# SERVER-SIDE KEEPALIVE PING (for frontend clients)
# ==============================
async def ws_keepalive(websocket: WebSocket, stop_event: asyncio.Event):
    try:
        while not stop_event.is_set():
            await asyncio.sleep(20)
            if stop_event.is_set():
                break
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_json({"type": "ping"})
            except Exception:
                break
    except asyncio.CancelledError:
        pass

# ==============================
# FRONTEND WEBSOCKET
# ==============================
@router.websocket("/ws/ltp")
async def ltp_websocket(websocket: WebSocket):
    await websocket.accept()
    CONNECTED_CLIENTS.add(websocket)

    stop_event = asyncio.Event()
    keepalive_task = asyncio.create_task(ws_keepalive(websocket, stop_event))
    registered_sids: list[int] = []

    try:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        except asyncio.TimeoutError:
            await websocket.send_json({"error": "Timeout: no security_ids received"})
            return

        payload = json.loads(raw)
        security_ids = list(set(map(int, payload.get("security_ids", []))))

        if not security_ids:
            await websocket.send_json({"error": "No security_ids provided"})
            return

        print(f" Frontend connected | securities={security_ids}")

        for sid in security_ids:
            ltp = LTP_STORE.get(sid)
            if ltp is not None:
                await websocket.send_json({"security_id": sid, "ltp": ltp})

        segment_lookup = await discover_segments_bulk(security_ids)
        segment_map: dict[str, list[str]] = {}
        for sec_id, segment in segment_lookup.items():
            if segment:
                segment_map.setdefault(segment, []).append(str(sec_id))

        if not segment_map:
            await websocket.send_json({"error": "No valid instruments found"})
            return

        dhan_ws = await get_dhan_ws()
        await subscribe_instruments(dhan_ws, segment_map)

        for sec_ids in segment_map.values():
            for sid in sec_ids:
                sid_int = int(sid)
                FRONTEND_TRADES.setdefault(sid_int, set()).add(websocket)
                registered_sids.append(sid_int)

        await ensure_listener_started()

        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                try:
                    data = json.loads(msg)
                    if data.get("type") == "pong":
                        pass
                    elif data.get("security_ids"):
                        new_ids = list(set(map(int, data["security_ids"])))
                        new_lookup = await discover_segments_bulk(new_ids)
                        new_seg_map: dict[str, list[str]] = {}
                        for sec_id, segment in new_lookup.items():
                            if segment:
                                new_seg_map.setdefault(segment, []).append(str(sec_id))
                        if new_seg_map:
                            dhan_ws = await get_dhan_ws()
                            await subscribe_instruments(dhan_ws, new_seg_map)
                            for sec_ids in new_seg_map.values():
                                for sid in sec_ids:
                                    sid_int = int(sid)
                                    FRONTEND_TRADES.setdefault(sid_int, set()).add(websocket)
                                    registered_sids.append(sid_int)
                except Exception:
                    pass

            except asyncio.TimeoutError:
                try:
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.send_json({"type": "ping"})
                    else:
                        break
                except Exception:
                    break

            except WebSocketDisconnect:
                print("🔴 Client disconnected")
                break

            except Exception as e:
                print(f"🔴 Client receive error: {e}")
                break

    except Exception as e:
        print(f"❌ WS setup error: {e}")

    finally:
        stop_event.set()
        keepalive_task.cancel()

        for sid in registered_sids:
            clients = FRONTEND_TRADES.get(sid)
            if clients:
                clients.discard(websocket)
                if not clients:
                    FRONTEND_TRADES.pop(sid, None)

        CONNECTED_CLIENTS.discard(websocket)
        try:
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close()
        except Exception:
            pass
        print(f"🧹 Cleaned up client | was watching: {registered_sids}")

# ==============================
# AUTO-START ON SERVER BOOT
# ==============================
async def auto_start_ws():
    await asyncio.sleep(1)
    print("🚀 Auto-starting WS monitoring...")

    ws = await get_dhan_ws()

    def _fetch_active_trades():
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT th.security_id, th.exchange_segment
            FROM trade_targets tt
            JOIN trade_history th ON tt.trade_id = th.id
            WHERE tt.is_monitoring_complete = false
            AND th.security_id IS NOT NULL
            AND th.exchange_segment IS NOT NULL
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch_active_trades)
    print(f"Active trades at startup: {rows}")

    if not rows:
        print("⚠️ No active trades at startup")
        await ensure_listener_started()
        return

    segment_map: dict[str, list[str]] = {}
    for sec_id, segment in rows:
        segment_map.setdefault(segment, []).append(str(sec_id))

    await subscribe_instruments(ws, segment_map)
    await ensure_listener_started()

# ==============================
# DEBUG / STATUS ROUTES
# ==============================
@router.get("/ws-ltp",tags=["Websocket Management"])
async def test_ltp():
    async def run_test():
        ws = await get_dhan_ws()

        def _fetch():
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT th.security_id, th.exchange_segment
                FROM trade_targets tt
                JOIN trade_history th ON tt.trade_id = th.id
                WHERE tt.is_monitoring_complete = false
                AND th.security_id IS NOT NULL
                AND th.exchange_segment IS NOT NULL
            """)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return rows

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, _fetch)
        if not rows:
            print("⚠️ No active trades")
            return

        segment_map: dict[str, list[str]] = {}
        for sec_id, segment in rows:
            segment_map.setdefault(segment, []).append(str(sec_id))

        await subscribe_instruments(ws, segment_map)
        await ensure_listener_started()

    asyncio.create_task(run_test())
    return {"message": "Subscription started."}


@router.get("/ws/test",tags=["Websocket Management"])
async def test_ws():
    return {
        "message": "Dhan WS route is active",
        "queue_size": LTP_QUEUE.qsize(),
        "subscribed_count": len(DHAN_SUBSCRIBED),
        "listener_running": DHAN_LISTENER_STARTED,
    }

@router.get("/ws/status",tags=["Websocket Management"])
async def ws_status():
    listener_alive = LISTENER_TASK is not None and not LISTENER_TASK.done()
    worker_alive = WORKER_TASK is not None and not WORKER_TASK.done()
    watchdog_alive = WATCHDOG_TASK is not None and not WATCHDOG_TASK.done()
    return {
        "subscribed_securities": list(DHAN_SUBSCRIBED),
        "ltp_store_count": len(LTP_STORE),
        "queue_size": LTP_QUEUE.qsize(),
        "frontend_clients": {str(k): len(v) for k, v in FRONTEND_TRADES.items()},
        "connected_clients_count": len(CONNECTED_CLIENTS),
        "listener_running": listener_alive,
        "worker_running": worker_alive,
        "watchdog_running": watchdog_alive,
        "warmup_seconds": WARMUP_SECONDS,
        "ws_connected": DHAN_WS is not None,
    }

@router.post("/ws/refresh",tags=["Websocket Management"])
async def refresh_ws():
    global DHAN_WS, DHAN_SUBSCRIBED, SECURITY_SEGMENT_MAP
    global LISTENER_TASK, WORKER_TASK, WATCHDOG_TASK
    global DHAN_LISTENER_STARTED

    print("🔄 Manual WS refresh triggered...")

    # 1. Cancel running tasks
    try:
        if LISTENER_TASK:
            LISTENER_TASK.cancel()
        if WORKER_TASK:
            WORKER_TASK.cancel()
        if WATCHDOG_TASK:
            WATCHDOG_TASK.cancel()
    except Exception as e:
        print("❌ Error cancelling tasks:", e)

    # 2. Close WS connection
    try:
        if DHAN_WS:
            await DHAN_WS.close()
    except Exception as e:
        print("❌ Error closing WS:", e)

    # 3. Reset globals
    DHAN_WS = None
    DHAN_SUBSCRIBED.clear()
    SECURITY_SEGMENT_MAP.clear()
    DHAN_LISTENER_STARTED = False

    # 4. Clear LTP queue (optional but safe)
    while not LTP_QUEUE.empty():
        try:
            LTP_QUEUE.get_nowait()
            LTP_QUEUE.task_done()
        except Exception:
            break

    print("🧹 Cleared WS state")

    # 5. Reconnect + auto start
    try:
        await auto_start_ws()
        print("✅ WS fully restarted")
        return {"status": "success", "message": "WS refreshed successfully"}
    except Exception as e:
        print("❌ WS restart failed:", e)
        return {"status": "error", "message": str(e)}