from fastapi import APIRouter, Query
from typing import Optional
from models.createdb import get_db_connection
from .models import Trade
from .analytics_service import calculate_analytics

router = APIRouter()


@router.get("/analytics")
def get_analytics(
    user_id: Optional[str] = Query(None),
    segment: Optional[str] = Query(None)
):

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 🔥 FETCH LAST 14 DAYS (for current + previous comparison)
        query = """
        SELECT th.id, th.scrip, th.entryprice, th.target1, th.target2, th.target3,
               th.stoploss,
               tt.t1_hit, tt.t2_hit, tt.t3_hit, tt.stoploss_hit,
               tt.t1_hit_at, tt.t2_hit_at, tt.t3_hit_at, tt.stoploss_hit_at,
               tt.partial_profit, tt.partial_loss,
               th.updated_at, th.user_id, th.created_at, th.position_type
        FROM trade_history th
        LEFT JOIN trade_targets tt ON th.id = tt.trade_id
        WHERE th.created_at >= CURRENT_DATE - INTERVAL '13 days'
          AND tt.trade_id IS NOT NULL
          AND tt.is_monitoring_complete = TRUE
          AND th.source = 'telegram'
        """

        values = []
        filters = []

        # 🔥 USER FILTER
        if user_id:
            filters.append("th.user_id = %s")
            values.append(user_id)

        # 🔥 SEGMENT FILTER
        if segment:
            mapping = {
                "INDEX": "OPTIDX",
                "STOCK": "OPTSTK",
                "MCX": "OPTFUT",
                "CNC": "EQUITY"
            }
            inst = mapping.get(segment.upper())
            if inst:
                filters.append("th.instrument = %s")
                values.append(inst)

        if filters:
            query += " AND " + " AND ".join(filters)

        # 🔥 EXECUTE QUERY
        cursor.execute(query, tuple(values))
        rows = cursor.fetchall()

        # 🔥 CONVERT TO MODEL
        trades = [
            Trade(
                id=r[0], scrip=r[1], entryprice=r[2],
                target1=r[3], target2=r[4], target3=r[5],
                stoploss=r[6],
                t1_hit=r[7], t2_hit=r[8], t3_hit=r[9], stoploss_hit=r[10],
                t1_hit_at=r[11], t2_hit_at=r[12], t3_hit_at=r[13], stoploss_hit_at=r[14],
                partial_profit=r[15] or 0,
                partial_loss=r[16] or 0,
                updated_at=r[17],
                user_id=r[18],
                created_at=r[19],
                position_type=r[20] or "LONG"
            )
            for r in rows
        ]

        # 🔥 SAFE RETURN
        if not trades:
            return {
                "statsData": {},
                "weeklyData": [],
                "tradingSummary": {}
            }

        # 🔥 SEND TO ANALYTICS SERVICE
        return calculate_analytics(trades)

    finally:
        cursor.close()
        conn.close()