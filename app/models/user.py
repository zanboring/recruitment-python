from sqlalchemy import Boolean, Column, Integer, String, DateTime
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, unique=True)
    password = Column(String(255), nullable=False)
    email = Column(String(100))
    role = Column(String(20), nullable=False, default="USER")
    skills = Column(String(500))
    education = Column(String(20))
    experience_years = Column(Integer)
    login_fail_count = Column(Integer, default=0)
    locked_until = Column(DateTime)
    # 账号启用状态。前端用户管理页需要「启用/禁用」开关（配套 Java 版的 UserVO.enabled）。
    # 禁用后不允许登录，且已签发的 token 立即失效（见 dependencies.get_current_user）。
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
