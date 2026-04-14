from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from user import (
    auth,
    profile,
    settings,
    upload_image,
)


app = FastAPI()

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(upload_image.router)
# app.include_router(user_model.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE", "PUT", "PATCH"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Trade Poster is running!"}