from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    email: EmailStr


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


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
