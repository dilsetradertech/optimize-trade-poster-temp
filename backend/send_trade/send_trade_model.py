from pydantic import BaseModel,validator,field_validator,ValidationInfo,ConfigDict
from typing import Optional, Dict, Set, List, Literal

class TradeData(BaseModel):
    id: Optional[str] = None
    scrip: str
    tradeType: str
    entryPrice: float
    stoploss: float
    target1: float
    target2: float
    target3: float
    exchangeID: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    security_id: int
    lot_size: float
    source: str                 # "telegram" | "whatsapp" | "algoapp"
    trade_filter: Optional[str] = None
    chart_url: str  
    reason: str
    position_type: Literal["LONG", "SHORT"] = "LONG"  # NEW
    is_repost: Optional[bool] = False

    @validator("lot_size", pre=True)
    def _cast_lot_size(cls, v):
        return int(float(v)) if v is not None else 0

  
    @field_validator("chart_url", "reason", mode="before")
    def enforce_for_all_and_fill_for_admin(cls, v, info: ValidationInfo):
        user_name = (info.data.get("user_name") or "").lower()

        # Admin default handling
        if user_name in {"support@dilsetrader.in","admin"}:
            if not v:  # blank -> fill default
                if info.field_name == "chart_url":
                    return "https://www.tradingview.com/x/u4m9lKRs/"
                if info.field_name == "reason":
                    return "NA"
            return v

        # Non-admin must provide values
        if not v:
            raise ValueError(f"{info.field_name} is required")
        return v
    

class TradeComment(BaseModel):
    trade_id: str
    comment: str
    model_config = ConfigDict(check_fields=False, extra="ignore")

