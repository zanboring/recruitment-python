"""安全工具：密码哈希与 JWT 签发/校验。

密码哈希方案说明（**为什么不是直接用 bcrypt**）

bcrypt 只使用输入的前 72 字节。原实现直接 ``pwd.encode()[:72]`` 截断，而
``schemas/auth.py`` 允许 128 个字符 —— 于是：

- 用户以为自己设了一个更长的密码，实际只有前 72 字节参与哈希；
- 实测 100 个 ``A`` 与 72 个 ``A`` 的密码**完全等价**，可以互相登录。

这种「看起来生效、实际被静默削弱」的行为是最不该出现在认证环节的。修法是通行做法
（Django、passlib 的 ``bcrypt_sha256`` 同理）：先对密码做一次 SHA-256，再交给 bcrypt。
SHA-256 的 digest 经 base64 编码后固定 44 字节，永不触达 72 字节上限，
既保留了完整熵、也彻底消除了截断。

**向后兼容**：新哈希带 ``sha256$`` 前缀；校验时若没有该前缀，说明是历史哈希，
仍按「直接 bcrypt + 截断」的老路径校验，因此老账号不会失效。
"""
import base64
import hashlib

from jose import jwt, JWTError
import bcrypt
from datetime import datetime, timedelta, timezone

from app.config import settings

# bcrypt 的输入长度上限（字节）
_BCRYPT_MAX_BYTES = 72

# 新哈希的算法前缀；没有该前缀说明是改造前的历史哈希
_SHA256_PREFIX = "sha256$"


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(milliseconds=settings.jwt_expiration)
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        raise ValueError("Invalid token")


def _sha256_b64(pwd: str) -> str:
    """把任意长度的密码压成定长 44 字节的 base64（永不超 bcrypt 上限）。

    用 base64 而非 hex：hex 是 64 字节，虽然也没超标，但一旦换成 SHA-512
    就会溢出；base64 的膨胀率更低，留有余量。
    """
    digest = hashlib.sha256(pwd.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def hash_password(pwd: str) -> str:
    """生成带算法前缀的密码哈希。

    空密码不做特殊处理 —— 长度与复杂度校验属于 schema 层的职责，
    这里只负责「把给定的字符串安全地哈希掉」。
    """
    hashed = bcrypt.hashpw(_sha256_b64(pwd).encode("utf-8"), bcrypt.gensalt())
    return _SHA256_PREFIX + hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。

    支持两种格式：
    - ``sha256$<bcrypt>``  —— 当前方案（无长度截断）
    - ``<bcrypt>``         —— 历史哈希（72 字节截断），保证老账号仍可登录

    任何格式异常（哈希被截断、字段为空、非法 base64）都返回 False，
    不向上抛异常 —— 认证入口不该因为一条脏数据而 500。
    """
    if not plain or not hashed:
        return False
    try:
        if hashed.startswith(_SHA256_PREFIX):
            stored = hashed[len(_SHA256_PREFIX):].encode("utf-8")
            return bcrypt.checkpw(_sha256_b64(plain).encode("utf-8"), stored)
        # 历史哈希：沿用原来的截断逻辑校验，避免老账号失效
        return bcrypt.checkpw(
            plain.encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False
