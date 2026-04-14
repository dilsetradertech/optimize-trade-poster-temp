from fastapi import APIRouter, HTTPException, Query
import duckdb

DUCKDB_FILE = "instrument/options_trade_poster.db"
TABLE_NAME = "instruments"

router = APIRouter()

@router.get("/instruments", tags=["Market Feed"])
async def get_instruments(
    query: str = Query(..., description="Search by SEM_CUSTOM_SYMBOL"),
    skip: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=1000),
):
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter is required")

    try:
        with duckdb.connect(DUCKDB_FILE, read_only=True) as conn:

            query_words = query.lower().split()

            search_conditions = " AND ".join(
                [
                    f"""
                    EXISTS (
                        SELECT 1
                        FROM (
                            SELECT unnest(string_split(lower(SEM_CUSTOM_SYMBOL), ' ')) AS word
                        ) t
                        WHERE word = '{word}'
                    )
                    """
                    for word in query_words
                ]
            )

            sql = f"""
                SELECT DISTINCT
                    SEM_CUSTOM_SYMBOL,
                    SEM_SMST_SECURITY_ID,
                    SEM_EXM_EXCH_ID,
                    SEM_INSTRUMENT_NAME,
                    SEM_LOT_UNITS
                FROM {TABLE_NAME}
                WHERE {search_conditions}
                ORDER BY 
                    CASE 
                        WHEN SEM_INSTRUMENT_NAME = 'EQUITY' THEN 0
                        ELSE 1
                    END,
                    SEM_EXPIRY_DATE ASC NULLS LAST,
                    SEM_CUSTOM_SYMBOL
                LIMIT ? OFFSET ?;
            """

            rows = conn.execute(sql, (limit, skip)).fetchall()

        instruments = [
            {
                "symbol": row[0],                 # ✅ SAME KEY
                "security_id": int(row[1]),
                "exchange_id": row[2],
                "instrument_name": row[3],
                "lot_size": row[4],
            }
            for row in rows
        ]
        searchingdata = {
            "instruments": instruments,
            "skip": skip,
            "limit": limit,
        }
        return searchingdata
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get(
    "/instruments/security_id",
    tags=["Market Feed"],
    summary="Return security_id, symbol, instrument_name, exchange_id and lot_size",
)
async def get_security_id(
    symbol: str = Query(..., description="Exact SEM_CUSTOM_SYMBOL or SEM_INSTRUMENT_NAME")
):
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol query parameter is required")

    sql = f"""
        SELECT
            SEM_CUSTOM_SYMBOL,
            SEM_SMST_SECURITY_ID,
            SEM_EXM_EXCH_ID,
            SEM_INSTRUMENT_NAME,
            SEM_LOT_UNITS
        FROM {TABLE_NAME}
        WHERE lower(SEM_CUSTOM_SYMBOL) = lower(?)
           OR lower(SEM_INSTRUMENT_NAME) = lower(?)
        LIMIT 1;
    """
    try:
        with duckdb.connect(DUCKDB_FILE, read_only=True) as conn:
            row = conn.execute(sql, (symbol, symbol)).fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Symbol not found")

        data = {
            "symbol": row[0],                    # ✅ SAME KEY
            "security_id": int(row[1]),
            "exchange_id": row[2],
            "instrument_name": row[3],
            "lot_size": row[4],
        }

        return data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
