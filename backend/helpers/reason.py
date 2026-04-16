from fastapi import APIRouter, HTTPException
from dotenv import load_dotenv
from models.createdb import get_db_connection
import pytz

router =APIRouter()

ist = pytz.timezone("Asia/Kolkata")
load_dotenv()

@router.post("/reasons/create", tags=["Reasons"])
def create_reason(reason: str, user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    #  role
    cursor.execute("SELECT role_id FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    allowed_roles = ["superadmin", "admin", "analyst"]

    if user[0].strip().lower() not in allowed_roles:
        raise HTTPException(status_code=403, detail="Not allowed to create reason")

    # Insert reason
    cursor.execute("""
        INSERT INTO trade_reasons (reason, created_by)
        VALUES (%s, %s)
        RETURNING id, reason
    """, (reason, user_id))

    data = cursor.fetchone()
    conn.commit()

    return {
        "message": "Reason created successfully",
        "data": {
            "id": data[0],
            "reason": data[1]
        }
    }

@router.get("/reasons", tags=["Reasons"])
def get_reasons(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
 
    cursor.execute("""
        SELECT reason, created_by
        FROM trade_reasons
        WHERE created_by = %s
        ORDER BY created_at DESC
    """, (user_id,))

    rows = cursor.fetchall()

    reasons = []
    for r in rows:
        reasons.append({
            "reason": r[0],
            "created_by": r[1]
        })

    return {
        "count": len(reasons),
        "data": reasons
    }