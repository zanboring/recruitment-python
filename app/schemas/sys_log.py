from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class SysLogResponse(BaseModel):
    id: int
    username: Optional[str] = None
    action: Optional[str] = None
    method: Optional[str] = None
    uri: Optional[str] = None
    ip: Optional[str] = None
    params: Optional[str] = None
    success: int = 1
    error_msg: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
