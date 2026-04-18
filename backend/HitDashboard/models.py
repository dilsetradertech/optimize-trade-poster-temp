from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class TradeHistory(BaseModel):
    id: str
    scrip: str
    tradeType: str
    entryPrice: float
    stoploss: float
    exchangeID: str
    target1: float
    target2: float
    target3: float
    created_at: datetime
    updated_at: datetime
    user_name: Optional[str] = None
    firstname: Optional[str] = None
    position_type: Optional[str] = None
    exchange_segment: Optional[str] = None
    t1_hit: Optional[bool] = None
    t2_hit: Optional[bool] = None
    t3_hit: Optional[bool] = None
    stoploss_hit: Optional[bool] = None
    is_monitoring_complete: Optional[bool] = None


class TradeTargets(BaseModel):
    trade_id: str
    t1: float | None = None
    t2: float | None = None
    t3: float | None = None
    t1_hit: Optional[bool] = None
    t2_hit: Optional[bool] = None
    t3_hit: Optional[bool] = None
    stoploss_hit: Optional[bool] = None
    is_monitoring_complete: Optional[bool] = None