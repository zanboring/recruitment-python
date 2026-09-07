from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: EmailStr


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UserInfoResponse(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    skills: str | None
    education: str | None
    experience_years: int | None

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    token: str
    user: UserInfoResponse
