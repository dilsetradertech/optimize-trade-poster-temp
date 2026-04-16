from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from user import (
    auth,
    profile,
    settings,
    upload_image,  
)
from instrument import instrument


app = FastAPI()

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(upload_image.router)
app.include_router(instrument.router)
# app.include_router(user_model.router)

@app.get("/")
async def root():
    return {"message": "Trade Poster is running!"}