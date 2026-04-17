from fastapi import APIRouter, HTTPException
import uuid, json
from send_trade.sendTradeRoute import send_trade, TradeData, get_db_connection, _telegram_send_multiple

router = APIRouter()

@router.post("/trade/repost/{trade_id}", tags=["Repost"])
async def repost_trade(trade_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT 
                th.scrip, th.tradetype, th.entryprice, th.stoploss, th.exchangeid,
                th.target1, th.target2, th.target3,
                th.user_id, th.user_name, th.source, th.position_type,
                th.chart_url, th.reason,
                th.security_id, th.lot_size,
                tt.is_monitoring_complete
            FROM trade_history th
            LEFT JOIN trade_targets tt 
            ON th.id = tt.trade_id
            WHERE th.id = %s
        """, (trade_id,))

        trade = cursor.fetchone()
        if not trade:
            raise HTTPException(status_code=404, detail= "Trade not found")

        (
            scrip, tradeType, entryPrice, stoploss, exchangeID,
            t1, t2, t3,
            user_id, user_name, source, position_type,
            chart_url, reason,
            security_id, lot_size,
            is_completed
        ) = trade

        if is_completed:
            raise HTTPException(400, "Cannot repost completed trade")

        new_trade_id = str(uuid.uuid4())

        trade_data = TradeData(
            id=new_trade_id,
            scrip=scrip,
            tradeType=tradeType,
            entryPrice=float(entryPrice),
            stoploss=float(stoploss),
            target1=float(t1),
            target2=float(t2),
            target3=float(t3),
            exchangeID=exchangeID,
            user_id=user_id,
            user_name=user_name,
            source=source,
            position_type=position_type,
            chart_url=chart_url,
            reason=reason,
            security_id=int(security_id),
            lot_size=float(lot_size)
        )

        print(" Reposting trade...")
        print(f" Source: {source}")

        # TELEGRAM & ALGOAPP BOTH USE send_trade()
        return await send_trade(trade_data)

    except Exception as e:
        print(" Repost error:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()