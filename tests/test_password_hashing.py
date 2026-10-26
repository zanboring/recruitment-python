"""密码哈希测试。

回归的核心问题：``hash_password`` 原先直接 ``pwd.encode()[:72]`` 截断，
而 schema 允许 128 个字符 —— 实测「100 个 A」与「72 个 A」的密码完全等价，
用户以为自己设了更长的密码，实际后 28 个字符从未参与校验。

这类问题不会报错、不会告警，只在安全审计时才会被发现，因此需要测试锁死。
"""
import bcrypt
import pytest

from app.utils.security import hash_password, verify_password


# ---------- 核心：不再静默截断 ----------

def test_长密码不与截断后的短密码等价():
    """100 个 A 的哈希必须拒绝 72 个 A —— 这正是修复前会通过的错误校验。"""
    hashed = hash_password("A" * 100)

    assert verify_password("A" * 100, hashed) is True
    assert verify_password("A" * 72, hashed) is False
    assert verify_password("A" * 99, hashed) is False


def test_远超上限的长密码仍能完整校验():
    """SHA-256 预哈希后固定 44 字节，任意长度都能完整参与校验。"""
    pwd = "P@ssw0rd" * 500          # 4000 字符

    hashed = hash_password(pwd)
    assert verify_password(pwd, hashed) is True
    assert verify_password(pwd[:-1], hashed) is False


def test_含中文的长密码不被切断():
    """按字节截断可能切断多字节字符，预哈希方案从根上避免了这个问题。"""
    pwd = "密码" * 50                # 100 字符 / 300 字节

    hashed = hash_password(pwd)
    assert verify_password(pwd, hashed) is True
    assert verify_password("密码" * 50 + "x", hashed) is False


# ---------- 哈希格式 ----------

def test_哈希带算法前缀且长度可入库():
    hashed = hash_password("Pass@1234")

    # 带前缀才能区分历史哈希，实现平滑升级
    assert hashed.startswith("sha256$")
    # user.password 是 String(255)
    assert len(hashed) < 255


def test_同一密码两次哈希不同():
    """bcrypt 每次生成随机盐，相同密码不应产生相同哈希。"""
    assert hash_password("Pass@1234") != hash_password("Pass@1234")


# ---------- 向后兼容 ----------

def test_历史哈希仍可校验():
    """改造前写入的账号不能失效。

    历史格式就是「直接 bcrypt + 72 字节截断」，这里按原样造一条来验。
    """
    legacy = bcrypt.hashpw(("A" * 100).encode()[:72], bcrypt.gensalt()).decode()
    assert not legacy.startswith("sha256$")

    assert verify_password("A" * 100, legacy) is True
    assert verify_password("wrong", legacy) is False


# ---------- 异常输入 ----------

@pytest.mark.parametrize("plain,hashed", [
    ("", "sha256$whatever"),
    ("x", ""),
    ("x", "not-a-bcrypt-hash"),
    ("x", "sha256$garbage"),
    (None, "sha256$whatever"),
    ("x", None),
])
def test_异常输入返回False而不是抛异常(plain, hashed):
    """认证入口不该因为一条脏数据而 500。"""
    assert verify_password(plain, hashed) is False
