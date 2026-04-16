from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from user import (
    auth,
    profile,
    settings,
    upload_image,  
)
from instrument import instrument
from token_management import token
from ltp import ltp
from telegram_channel_manage import channel_route


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
app.include_router(channel_route.router)
# app.include_router(user_model.router)

@app.get("/")
async def root():
    return {"message": "Trade Poster is running!"}