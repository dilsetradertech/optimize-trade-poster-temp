import psycopg2
import uuid
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from models.createdb import get_db_connection
import os
from user.user_model import (
    ProfileResponse,
    CreateProfileRequest,
    UpdateProfileRequest
)
# Load environment variables
load_dotenv()
app = FastAPI(title="User Profile API")
router = APIRouter()

@router.post("/profile", response_model=ProfileResponse, tags=["Profile Management"])
async def create_user_profile(request: CreateProfileRequest):

    conn = get_db_connection()
    cur = conn.cursor()

    profile_id = str(uuid.uuid4())[:8]

    try:
        cur.execute(
            """INSERT INTO profiles (id, user_id, firstName, lastName, mobileNo, profileImage)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (profile_id, request.user_id, request.firstName, request.lastName, request.mobileNo, request.profileImage)
        )
        conn.commit()

        return {
            "user_id": request.user_id,
            "firstName": request.firstName,
            "lastName": request.lastName,
            "mobileNo": request.mobileNo,
            "profileImage": request.profileImage
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Error creating profile: {str(e)}")

    finally:
        cur.close()
        conn.close()


# Get Profile API
@router.get("/profile/{user_id}", response_model=ProfileResponse, tags=["Profile Management"])
async def get_user_profile(user_id: str):
    """Retrieve a user's profile."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT user_id, firstName, lastName, mobileNo, profileImage FROM profiles WHERE user_id = %s",
            (user_id,)
        )
        profile = cur.fetchone()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found for the user")

        return {
            "user_id": profile[0],
            "firstName": profile[1],
            "lastName": profile[2],
            "mobileNo": profile[3],
            "profileImage": profile[4]
        }

    finally:
        cur.close()
        conn.close()


# Update Profile API
@router.put("/profile/{user_id}", response_model=ProfileResponse, tags=["Profile Management"])
async def update_user_profile(user_id: str, request: UpdateProfileRequest):
    """Update a user's profile."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT user_id FROM profiles WHERE user_id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Profile not found for the user")

        cur.execute(
            """UPDATE profiles
               SET firstName = %s, lastName = %s, mobileNo = %s, profileImage = %s
            WHERE user_id = %s""",
            
            (request.firstName, request.lastName, request.mobileNo, request.profileImage, user_id)
        )
        conn.commit()

        return {
            "user_id": user_id,
            "firstName": request.firstName,
            "lastName": request.lastName,
            "mobileNo": request.mobileNo,
            "profileImage": request.profileImage
        }

    finally:
        cur.close()
        conn.close()


# Delete Profile API
@router.delete("/profile/{user_id}", tags=["Profile Management"])
async def delete_user_profile(user_id: str):
    """Delete a user's profile based on user_id."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT user_id FROM profiles WHERE user_id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Profile not found for the user")

        cur.execute("DELETE FROM profiles WHERE user_id = %s", (user_id,))
        conn.commit()

        return {"message": f"Profile for user {user_id} successfully deleted"}

    finally:
        cur.close()
        conn.close()

# Include Router
app.include_router(router)