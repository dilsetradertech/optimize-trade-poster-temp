from pydantic import BaseModel

class ChannelCreate(BaseModel):
    channel_id: str
    channel_key: str
    channel_name: str = ""
    allow_mcx: bool = False
    allow_index: bool = False
    allow_stock: bool = False
    allow_btst: bool = False
    allow_equity: bool = False
    allow_selling: bool = False
    allow_future:bool=False
    allow_button: bool = True

class ChannelUpdate(BaseModel):
    channel_key: str | None = None
    channel_name: str | None = None
    allow_mcx: bool | None = None
    allow_index: bool | None = None
    allow_stock: bool | None = None
    allow_btst: bool | None = None
    allow_equity: bool | None = None
    allow_selling: bool | None = None   
    allow_future:bool | None = None
    allow_button: bool | None = None

