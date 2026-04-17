from pydantic import BaseModel
from typing import Optional


class TradeDraft(BaseModel):
    scrip: Optional[str] = None
    tradeType: Optional[str] = None
    entryPrice: Optional[float] = None
    stoploss: Optional[float] = None
    target1: Optional[float] = None
    target2: Optional[float] = None
    target3: Optional[float] = None
    exchangeID: Optional[str] = None
    security_id: Optional[int] = None
    lot_size: Optional[int] = None
    instrument_name: Optional[str] = None
    reason: Optional[str] = None
    chart_url: Optional[str] = None
    position_type: Optional[str] = "LONG"
    user_id: Optional[str] = None