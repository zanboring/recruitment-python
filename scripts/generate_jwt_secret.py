"""生成 JWT 签名密钥。

用法：
    python scripts/generate_jwt_secret.py

输出一串 32 字符的随机字符串（大小写字母 + 数字），把它填到 Windows
用户环境变量 ``JWT_SECRET`` 即可。字符集只保留字母数字，避免 ``.env``
里出现引号、井号、等号之类需要转义的特殊字符。

用标准库 ``secrets`` 而不是 ``random``：``secrets`` 是密码学安全的随机源，
适合做签名密钥；``random`` 是伪随机，种子可预测，不能用于安全场景。
"""

from __future__ import annotations

import secrets
import string

# HS256 推荐至少 256 bit（32 字节）。这里输出 32 个字符，
# 字母数字表 62 个符号 ≈ 5.95 bit/字符，32 字符 ≈ 190 bit，够用。
# 想要更长就改下面这个数字。
SECRET_LENGTH = 32


def generate_jwt_secret(length: int = SECRET_LENGTH) -> str:
    """生成 ``length`` 个字符的随机密钥（字母+数字）。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


if __name__ == "__main__":
    secret = generate_jwt_secret()
    print("JWT_SECRET=" + secret)
    print()
    print("把上面这一整行（或只等号右边那串）加到 Windows 用户环境变量 JWT_SECRET：")
    print("  Win+R -> sysdm.cpl -> 高级 -> 环境变量 -> 用户变量 -> 新建")
    print("配完记得重开终端 / IDE 才生效。")
