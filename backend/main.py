from fastapi import FastAPI
from user import (
    auth,
    profile,
    settings,
    upload_image,
    user_model
)


app = FastAPI()

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(upload_image.router)
# app.include_router(user_model.router)
