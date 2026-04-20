import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from user import (auth, profile, settings, upload_image)
from instrument import instrument
from token_management import token
from ltp import ltp,dhan_ws
from telegram_channel_manage import channel_route
from monitoring import stop_monitoring
from send_trade import (sendTradeRoute,repost)
from history import trade
from helpers import draft
from helpers import reason
from analytics import analytics
from HitDashboard import target_hit ,stoploss_hit


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PUT", "PATCH"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(upload_image.router)
app.include_router(instrument.router)
app.include_router(token.router)
app.include_router(ltp.router)
app.include_router(dhan_ws.router)
app.include_router(channel_route.router)
app.include_router(stop_monitoring.router)
app.include_router(channel_route.router)
app.include_router(sendTradeRoute.router)
app.include_router(repost.router)
app.include_router(trade.router)
app.include_router(draft.router)
app.include_router(reason.router)
app.include_router(analytics.router) 
app.include_router(target_hit.router) 
app.include_router(stoploss_hit.router) 



@app.on_event("startup")
async def startup_event():
    print("🚀 Starting Dhan WebSocket monitoring...")
    asyncio.create_task(dhan_ws.auto_start_ws())

@app.get("/")
async def root():
    return {"message": "Trade Poster is running!"}