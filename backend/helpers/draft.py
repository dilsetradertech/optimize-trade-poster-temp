from fastapi import APIRouter, HTTPException, Query
from datetime import datetime
import uuid
import pytz
from dotenv import load_dotenv
from helpers.draft_model import TradeDraft
from models.createdb import get_db_connection

router = APIRouter(prefix="/drafts", tags=["Drafts"])
load_dotenv()

IST = pytz.timezone("Asia/Kolkata")

# ================= HELPER FUNCTION =================
def is_empty_draft(data: TradeDraft) -> bool:
    return not any([
        data.scrip,
        data.tradeType,
        data.entryPrice,
        data.stoploss,
        data.target1,
        data.target2,
        data.target3,
        data.reason,
        data.chart_url,
    ])


# ================= POST: SAVE DRAFT =================
@router.post("/")
def save_draft(data: TradeDraft):  
    if is_empty_draft(data):
        raise HTTPException(
            status_code=400,
            detail="Empty draft cannot be saved. Provide at least one valid field."
        )
    
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        draft_id = str(uuid.uuid4())

        query = """
        INSERT INTO draft (
            id, scrip, tradetype, entryprice, stoploss,
            target1, target2, target3, exchangeid,
            security_id, lot_size, instrument_name,
            reason, chart_url, position_type,
            user_id, draft, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        values = (
            draft_id,
            data.scrip,
            data.tradeType,
            data.entryPrice,
            data.stoploss,
            data.target1,
            data.target2,
            data.target3,
            data.exchangeID,
            data.security_id,
            data.lot_size,
            data.instrument_name,
            data.reason,
            data.chart_url,
            data.position_type,
            data.user_id,
            True,
            datetime.now(IST),
        )

        cursor.execute(query, values)
        conn.commit()

        return {"message": "Draft saved successfully", "draft_id": draft_id}
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# ================= GET: FETCH DRAFTS =================
@router.get("/")
def get_drafts(user_id: str = Query(...)):   
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if user_id:
            query = """
                SELECT id, scrip, tradetype, entryprice, stoploss,
                       target1, target2, target3, exchangeid,
                       security_id, lot_size, instrument_name,
                       reason, chart_url, position_type,
                       user_id, created_at
                FROM draft
                WHERE user_id = %s AND draft = TRUE
                ORDER BY created_at DESC
                """
            cursor.execute(query, (user_id,))
        else:
            query = """
                SELECT id, scrip, tradetype, entryprice, stoploss,
                       target1, target2, target3, exchangeid,
                       security_id, lot_size, instrument_name,
                       reason, chart_url, position_type,
                       user_id, created_at
                FROM draft
                WHERE draft = TRUE
                ORDER BY created_at DESC
            """
            cursor.execute(query)

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        drafts = [dict(zip(columns, row)) for row in rows]

        return {
            "count": len(drafts),
            "data": drafts
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# ================= DELETE: REMOVE DRAFT =================
@router.delete("/{draft_id}")
def delete_draft(draft_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM draft WHERE id = %s", (draft_id,))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Draft not found")

        conn.commit()
        return {"message": "Draft deleted successfully"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()