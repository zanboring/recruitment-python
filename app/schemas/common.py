from pydantic import BaseModel
from typing import Generic, TypeVar, Optional

T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: Optional[T] = None

    @staticmethod
    def success(data=None):
        return Result(code=0, message="success", data=data)

    @staticmethod
    def failed(message: str, code: int = 1):
        return Result(code=code, message=message, data=None)