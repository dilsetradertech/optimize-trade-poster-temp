from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class Trade(BaseModel):
    id: str
    scrip: str
    entryprice: float
    target1: float
    target2: float
    target3: float
    stoploss: float

    t1_hit: bool
    t2_hit: bool
    t3_hit: bool
    stoploss_hit: bool

    t1_hit_at: Optional[datetime]
    t2_hit_at: Optional[datetime]
    t3_hit_at: Optional[datetime]
    stoploss_hit_at: Optional[datetime]

    partial_profit: float = 0
    partial_loss: float = 0

    updated_at: datetime
    created_at: datetime
    user_id: str
    position_type: Optional[str] = "LONG"