import psycopg2
import uuid
from fastapi import FastAPI, APIRouter, HTTPException, Query
from typing import Optional
from pydantic import BaseModel
from passlib.context import CryptContext
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
import os
import random
import httpx
from models.createdb import get_db_connection
from datetime import datetime, timedelta
from user.user_model import (
    UserCreate,
    LoginCheck,
    MobileLogin,
    VerifyOTP,
    LogoutRequest,
    AdminCreate
)
import pytz

load_dotenv()
app = FastAPI(title="Auth")
router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
MSG91_TEMPLATE_ID= os.getenv("MSG91_TEMPLATE_ID")
MSG91_API_KEY = os.getenv("MSG91_API_KEY")

def generate_uuid():
    return str(uuid.uuid4())

def generate_role_id():
    return str(uuid.uuid4())[:8]

def generate_otp():
    return str(random.randint(100000, 999999))

def hash_password(password: str):
    return pwd_context.hash(password)

async def send_otp_msg91(mobile: str):

    otp = str(random.randint(100000, 999999))
    url = "https://control.msg91.com/api/v5/otp"

    payload = {
        "template_id": MSG91_TEMPLATE_ID,
        "mobile": f"91{mobile}",
        "otp": otp
    }
    print(f"{otp} sent to {mobile}")

    headers = {
        "Content-Type": "application/json",
        "authkey": MSG91_API_KEY
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(url, json=payload, headers=headers)

    if res.status_code != 200:
        raise HTTPException(status_code=500, detail=f"OTP send failed: {res.text}")

    return otp

# Verify user session authentication
def validate_session(user_id: str, session_token: str):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """SELECT session_expiry
        FROM users
        WHERE id = %s AND session_token = %s AND is_login = TRUE""", 
        (user_id, session_token))

    user = cur.fetchone()

    cur.close()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")

    expiry = user[0]

    if expiry is None or expiry < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Session expired")
    

@router.post("/admin/create", tags=["User Management"])
async def create_admin(data: AdminCreate):

    conn = get_db_connection()
    cur = conn.cursor()

    admin_id = str(uuid.uuid4())[:8]
    ist = pytz.timezone("Asia/Kolkata")
    created_at = datetime.now(ist)

    try:
        cur.execute(
            "SELECT id FROM users WHERE mobile = %s",
            (data.mobile,)
        )

        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Mobile already exists")

        hashed_password = hash_password(data.password)

        cur.execute(
            """INSERT INTO users (id, username, password, role_id, mobile, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                admin_id,
                data.username,
                hashed_password,
                "admin",
                data.mobile,
                created_at,
            )
        )

        conn.commit()

        return {
            "message": "Admin created successfully",
            "admin_id": admin_id,
            "username": data.username,
            "mobile": data.mobile,
            "created_at": created_at.strftime("%d-%m-%Y %I:%M %p"),
            "role": "admin"
        }
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    finally:
        cur.close()
        conn.close()

@router.post("/analyst/create", tags=["User Management"])
async def create_user(user: UserCreate):
    if user.role_id not in ["admin", "analyst"]:
        raise HTTPException(status_code=400, detail="Invalid role. Choose 'admin' or 'analyst'.")

    conn = get_db_connection()
    cur = conn.cursor()

    user_id = str(uuid.uuid4())[:8]
    hashed_password = pwd_context.hash(user.password)
    ist = pytz.timezone("Asia/Kolkata")
    created_at = datetime.now(ist)


    try:
        cur.execute(
            """INSERT INTO users (id, username, password, role_id, mobile, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (user_id, user.username, hashed_password, user.role_id, user.mobile, created_at),
        )

        cur.execute(
            """INSERT INTO permissions (user_id, permission_name, is_enabled)
               VALUES (%s, 'is_enabled', TRUE)""",
            (user_id,),
        )

        cur.execute(
            """INSERT INTO settings (id, user_id, sl, t1, t2, t3, targetBy)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (str(uuid.uuid4())[:8], user_id, 0.8, 10.0, 20.0, 30.0, "Percentage"),
        )

        conn.commit()
        return {
            "id": user_id,
            "username": user.username,
            "role_id": user.role_id,
            "is_enabled": True,
            "created_at": created_at.strftime("%d-%m-%Y %I:%M %p"),
            "settings": {
                "sl": 0.8,
                "t1": 10.0,
                "t2": 20.0,
                "t3": 30.0,
                "targetBy": "Percentage",
            },
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")

    finally:
        cur.close()
        conn.close()

@router.delete("/roles/{role_id}", tags=["User Management"])
async def delete_role(role_id: str):
    conn = get_db_connection()
    cur = conn.cursor()


    role_exists = conn.execute(
        "SELECT id FROM roles WHERE id = ?", (role_id,)
    ).fetchone()
    if not role_exists:
        conn.close()
        raise HTTPException(status_code=404, detail="Role not found")

    conn.execute("DELETE FROM users WHERE role_id = ?", (role_id,))
    conn.execute("DELETE FROM roles WHERE id = ?", (role_id,))
    conn.close()
    return {"message": f"Role '{role_id}' and associated users deleted"}


# Delete analyst with settings and permissions
@router.delete("/analyst/{user_id}", tags=["User Management"])
async def delete_analyst(user_id: str):

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
    user_exists = cur.fetchone()

    if not user_exists:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Delete settings first (if exists)
        cur.execute("DELETE FROM settings WHERE user_id = %s", (user_id,))

        # Delete permissions
        cur.execute("DELETE FROM permissions WHERE user_id = %s", (user_id,))

        # Delete user
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))

        conn.commit()
        return {
            "message": f"User '{user_id}', settings, and permissions deleted successfully"
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")

    finally:
        cur.close()
        conn.close()

@router.put("/update-role/{id}", tags=["User Management"])
def update_role(
    id: str,
    role_id: Optional[str] = Query(None)
):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    try:
        # Get current role
        cursor.execute(
            "SELECT role_id FROM users WHERE id = %s",
            (id,)
        )
        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        current_role = user["role_id"]

        # Toggle logic
        if current_role == "analyst":
            new_role = "admin"
        elif current_role == "admin":
            new_role = "analyst"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Role '{current_role}' cannot be changed"
            )

        # Update role
        cursor.execute(
            """UPDATE users 
               SET role_id = %s,
                   role_updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')
                WHERE id = %s""",
            (new_role, id)
        )
        conn.commit()

        return {
            "message": "Role updated successfully",
            "id": id,
            "old_role": current_role,
            "new_role": new_role

        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# Get all roles
@router.get("/roles", tags=["User Management"])
async def get_roles():

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM roles;")
            roles = cur.fetchall()

        if not roles:
            raise HTTPException(status_code=404, detail="No roles found.")
        
        return [
            {"role_id": r[0], "role_name": r[1]}
            for r in roles
            ]
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        conn.close()


# Send OTP for mobile login 
@router.post("/login", tags=["Authentication"])
async def login_mobile(data: MobileLogin):

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """SELECT id, username, role_id
            FROM users
            WHERE mobile = %s""",
            (data.mobile,)
        )

        user = cur.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Mobile number not registered")

        # send OTP
        otp = await send_otp_msg91(data.mobile)

        cur.execute(
            """UPDATE users
               SET otp = %s,
                session_token = NULL,
                session_expiry = NULL,
                is_login = FALSE
            WHERE mobile = %s""",
            (otp, data.mobile)
        )

        conn.commit()

        return {
            "message": "OTP sent successfully"
        }

    finally:
        cur.close()
        conn.close()

# Verify OTP and login user
@router.post("/verify-otp",tags=["Authentication"])
def verify_otp(data: VerifyOTP):

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Fetch user by mobile
        cur.execute(
            """SELECT id, otp, role_id, username
               FROM users
               WHERE mobile = %s""",
            (data.mobile,),
        )

        user = cur.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Validate OTP
        if user["otp"] != data.otp:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        # Generate session token
        session_token = str(uuid.uuid4())
        session_expiry = datetime.utcnow() + timedelta(hours=72)

        # Update session details in DB
        cur.execute(
            """UPDATE users
               SET session_token = %s,
                   session_expiry = %s,
                   is_login = TRUE,
                   otp = NULL
               WHERE mobile = %s""",
            (session_token, session_expiry, data.mobile),
        )

        conn.commit()

        return {
            "message": "Login successful",
            "session_token": session_token,
            "user_id": user["id"],
            "username": user["username"],
            "role": user["role_id"],
        }

    finally:
        cur.close()
        conn.close()


#Logout user and clean session
@router.post("/logout", tags=["Authentication"])
async def logout(data: LogoutRequest):

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """UPDATE users
        SET is_login = FALSE,
            session_expiry = NULL,
            session_token = NULL
        WHERE id = %s""",
        (data.user_id,)
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"message": "Logged out successfully"}

@router.get("/session-check",tags=["Authentication"])
async def session_check(user_id: str):

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """SELECT username, role_id, session_expiry
               FROM users
               WHERE id = %s AND is_login = TRUE""", 
            (user_id,))

        user = cur.fetchone()

        if not user:
            raise HTTPException(status_code=401, detail="Not logged in")

        username, role, expiry = user

        if expiry is None or expiry < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Session expired")

        return {
            "valid": True, 
            "username": username,
            "role": role,
            "session_expiry": expiry
        }

    finally:
        cur.close()
        conn.close()

# Get list of all Users
@router.get("/users", tags=["User Management"])
async def get_users():

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """SELECT u.id, u.username, r.name, u.role_id 
            FROM users u 
            JOIN roles r ON u.role_id = r.id"""
        )

    users = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {"id": u[0], "username": u[1], "role": u[2], "role_id": u[3]} 
        for u in users
    ]


# Get analysts and admins with permissions
@router.get("/analysts", tags=["Analyst Permissions"])
async def get_analysts():
    conn = get_db_connection()
    
    try:
        with conn.cursor() as cur:
            # Fetch analysts + admins
            cur.execute(
                """SELECT u.id, u.username, r.name AS role, u.role_id, u.created_at
                FROM users u 
                JOIN roles r ON u.role_id = r.id
                WHERE r.name IN ('analyst', 'admin')"""
            )

            users = cur.fetchall()
            users_with_permissions = []
            ist = pytz.timezone("Asia/Kolkata") 


            for u in users:
                user_id = u[0]
                created_at = u[4]

                if created_at:
                    created_at_ist = created_at.astimezone(ist).strftime("%d-%m-%Y %I:%M %p")
                else:
                    created_at_ist = None


                cur.execute(
                    "SELECT is_enabled FROM permissions WHERE user_id = %s",
                    (user_id,),
                )
                result = cur.fetchone()

                if result is None:
                    cur.execute(
                        """INSERT INTO permissions (user_id, permission_name, is_enabled) 
                           VALUES (%s, %s, TRUE)""",
                        (user_id, "default_permission"),
                    )
                    conn.commit()
                    is_enabled = True
                else:
                    is_enabled = result[0]

                users_with_permissions.append(
                    {
                        "id": user_id,
                        "username": u[1],
                        "role": u[2],
                        "role_id": u[3],
                        "is_enabled": is_enabled,
                        "created_at": created_at_ist,  

                    }
                )

        return users_with_permissions
    
    finally:
        conn.close()

# Update user permissions and login access
@router.patch("/analyst/{user_id}/permissions", tags=["Analyst Permissions"])
async def update_permissions(
    user_id: str,
    is_enabled: bool | None = None,
    can_comment: bool | None = None,
    can_stop: bool | None = None,
    is_active: bool | None = None
):
    
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Check if user exists
        cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="User not found")

        # Ensure the permissions row exists
        cur.execute("SELECT user_id FROM permissions WHERE user_id = %s", (user_id,))
        if not cur.fetchone():
            cur.execute(
                """INSERT INTO permissions (id, user_id, permission_name, is_enabled, can_comment, can_stop)
                   VALUES (gen_random_uuid(), %s, 'is_enabled', TRUE, FALSE, FALSE)""",
                (user_id,),
            )
            conn.commit()

        updates = []
        params = []

        # Handle permissions table fields
        if is_enabled is not None:
            updates.append("is_enabled = %s")
            params.append(is_enabled)

        if can_comment is not None:
            updates.append("can_comment = %s")
            params.append(can_comment)

        if can_stop is not None:
            updates.append("can_stop = %s")
            params.append(can_stop)

        # Update user login access
        if is_active is not None:
            cur.execute(
                "UPDATE users SET is_active = %s WHERE id = %s",
                (is_active, (user_id)),
            )
            conn.commit()

        # If no updates at all
        if not updates and is_active is None:
            raise HTTPException(status_code=400, detail="No valid permission provided")

        # Update permissions table dynamically
        if updates:
            query = f"""UPDATE permissions
                SET {', '.join(updates)}
                WHERE user_id = %s
                RETURNING is_enabled, can_comment, can_stop"""
            params.append(user_id)

            cur.execute(query, tuple(params))
            result = cur.fetchone()
        else:
            # only is_active was changed
            cur.execute(
                """SELECT is_enabled, can_comment, can_stop 
                   FROM permissions WHERE user_id = %s""", 
                (user_id,))
            result = cur.fetchone()

        conn.commit()

        return {
            "message": "Permissions updated successfully",
            "user_id": user_id,
            "permissions": {
                "is_enabled": bool(result[0]),
                "can_comment": bool(result[1]),
                "can_stop": bool(result[2]),
                "is_active": bool(is_active) if is_active is not None else None
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating permissions: {str(e)}")

    finally:
        cur.close()
        conn.close()


# Get user permissions and active status
@router.get("/analyst/{user_id}/permissions", tags=["Analyst Permissions"])
async def get_user_permissions(user_id: str):
   
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Fetch user active status from users table
        cur.execute("SELECT is_active FROM users WHERE id = %s", (user_id,))
        user_row = cur.fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")
        is_active = bool(user_row[0])

        # Fetch permission details
        cur.execute(
            """SELECT is_enabled, can_comment, can_stop
            FROM permissions
            WHERE user_id = %s""",
            (user_id,),
        )
        result = cur.fetchone()

        # If not found, create default permissions
        if not result:
            cur.execute(
                """INSERT INTO permissions (id, user_id, permission_name, is_enabled, can_comment, can_stop)
                VALUES (gen_random_uuid(), %s, 'is_enabled', TRUE, FALSE, FALSE)
                RETURNING is_enabled, can_comment, can_stop""",
                (user_id,),
            )
            conn.commit()
            result = cur.fetchone()

        return {
            "user_id": user_id,
            "permissions": {
                "is_enabled": bool(result[0]),
                "can_comment": bool(result[1]),
                "can_stop": bool(result[2]),
                "is_active": is_active  
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching permissions: {str(e)}")

    finally:
        cur.close()
        conn.close()



