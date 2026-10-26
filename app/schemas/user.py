from pydantic import BaseModel, EmailStr, field_serializer
from datetime import datetime
from app.schemas.auth import UserInfoResponse


class UserUpdateRequest(BaseModel):
    email: EmailStr | None = None
    skills: str | None = None
    education: str | None = None
    experience_years: int | None = None


class UserResponse(UserInfoResponse):
    login_fail_count: int
    enabled: bool = True
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()

    model_config = {"from_attributes": True}