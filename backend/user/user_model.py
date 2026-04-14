from pydantic import BaseModel

class UserCreate(BaseModel):
    username: str
    password: str
    mobile: str
    role_id: str = "analyst"

class LoginCheck(BaseModel):
    username: str
    password: str
    mobile: str

class MobileLogin(BaseModel):
    mobile: str

class VerifyOTP(BaseModel):
    mobile: str
    otp: str

class LogoutRequest(BaseModel):
    user_id: str

class AdminCreate(BaseModel):
    username: str
    mobile: str
    password: str

class ProfileResponse(BaseModel):
    firstName: str
    lastName: str
    mobileNo: str
    profileImage: str
    user_id: str

class CreateProfileRequest(BaseModel):
    user_id: str
    firstName: str
    lastName: str
    mobileNo: str
    profileImage: str

class UpdateProfileRequest(BaseModel):
    firstName: str
    lastName: str
    mobileNo: str
    profileImage: str

class SettingsResponse(BaseModel):
    sl: float
    t1: float
    t2: float
    t3: float
    targetBy: str
    user_id: str

class UpdateSettingsRequest(BaseModel):
    sl: float
    t1: float
    t2: float
    t3: float
    targetBy: str
