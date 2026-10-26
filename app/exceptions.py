from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppException(Exception):
    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code


async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(status_code=exc.code, content={"code": exc.code, "message": exc.message, "data": None})


async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"code": 500, "message": "服务器内部错误", "data": None})


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "message": exc.detail, "data": None})