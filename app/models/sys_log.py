from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func

from app.database import Base


class SysLog(Base):
    __tablename__ = "sys_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50))
    action = Column(String(255))
    method = Column(String(255))
    uri = Column(String(255))
    ip = Column(String(50))
    params = Column(Text)
    success = Column(Integer, default=1)
    error_msg = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
