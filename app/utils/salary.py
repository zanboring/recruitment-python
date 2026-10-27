"""薪资文本 → 月薪区间的**唯一实现**。

## 为什么要独立成模块

项目里曾有两套解析：``crawlers/job51.py`` 的 ``parse_salary_to_monthly``
（覆盖万/千/年包/单值，带完整测试）与 ``crawlers/cleaner.py`` 的
``parse_salary``（只认 ``8K-12K`` 一种写法）。后者是 BOSS 走的那条路，而
BOSS 的真实文本是 ``15-25K·13薪`` —— 正则不匹配，**整站薪资全部落成 0**。
症状很隐蔽：岗位能入库、列表能显示，只是薪资列全是「面议」，
薪资分布图与「高薪岗位过滤」静默失效，没有任何报错。

两处维护必然漂移，所以实现收敛到这里，两个爬虫都只是调用方。

## 覆盖的写法（均来自真实页面样本）

    15-25K        → (15000, 25000)   BOSS 最常见；单位只写在后半段，前半段继承
    15-25K·13薪   → (15000, 25000)   「13薪」是年终奖倍数，不影响月薪区间
    8K-12K        → (8000, 12000)    两端都写单位
    1-1.5万       → (10000, 15000)
    8千-1.2万     → (8000, 12000)    两端单位不同
    6-8千         → (6000, 8000)
    15-25万/年    → (12500, 20833)   年包折算成月
    2万           → (20000, 20000)   单值
    8000-12000元/月 → (8000, 12000)
    面议 / 保密 / 空 → (0, 0)
    200-300元/天  → (0, 0)           非月薪口径

## 设计原则

解析不出来**一律返回 (0, 0)**，绝不为「看起来有数据」而猜一个数字：
薪资是这个系统所有分析与可视化的输入，假数据的破坏力远大于空值。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

# 单位倍率。不同站点写法不统一（万 / w / 千 / k），统一到这里。
_UNIT_MULTIPLIERS = {
    "万": 10000,
    "w": 10000,
    "千": 1000,
    "k": 1000,
    "元": 1,
}

_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|w|W|千|k|K|元)?")

# 非月薪口径（日薪 / 时薪 / 周薪）无法与月薪区间直接比较，一律丢弃
_NON_MONTHLY_HINTS = ("/天", "元/天", "/时", "小时", "/周")

# 明确表示"没有可比较的数字"的词，命中即返回 (0, 0)
_UNKNOWN_HINTS = ("面议", "保密", "薪资面议")


def parse_salary_to_monthly(raw) -> Tuple[int, int]:
    """把薪资文本解析成**月薪**区间（元）。解析不了返回 ``(0, 0)``。

    :param raw: 任意类型（页面抽出来的可能是 str / None / 数字），内部统一转 str。
    """
    if raw is None:
        return 0, 0
    text = str(raw).strip()
    if not text:
        return 0, 0
    if any(hint in text for hint in _UNKNOWN_HINTS):
        return 0, 0
    if any(hint in text for hint in _NON_MONTHLY_HINTS):
        return 0, 0

    # 「15-25万/年」要折成月薪；但「3-5年经验」这类不该被当成薪酬口径，
    # 故先剔除"年经验"再判断是否含"年"。
    annual = "/年" in text or "年薪" in text or "年" in text.replace("年经验", "")

    # 去掉「·13薪」「（xx）」等尾巴，以及单位说明，只留数字与量级单位
    cleaned = text.split("·")[0].split("（")[0].split("(")[0]
    for noise in ("年薪", "月薪", "元/月", "/月", "元/年", "/年", "元", "/"):
        cleaned = cleaned.replace(noise, " ")
    cleaned = cleaned.strip()

    tokens = [t for t in re.split(r"[-~～至]", cleaned) if t.strip()]
    if not tokens:
        return 0, 0

    parsed = [_parse_amount(t) for t in tokens[:2]]
    parsed = [p for p in parsed if p is not None]
    if not parsed:
        return 0, 0

    if len(parsed) == 1:
        low_value, low_unit = parsed[0]
        high_value, high_unit = parsed[0]
    else:
        (low_value, low_unit), (high_value, high_unit) = parsed[0], parsed[1]

    # 「15-25K」「1-1.5万」：量级单位只写在后半段，前半段继承后半段的单位。
    # 用「前半段数值 < 100 且自身没有量级单位」判定，避免把「8000-12000」误判。
    if low_unit == 1 and high_unit != 1 and low_value < 100:
        low_unit = high_unit

    low = low_value * low_unit
    high = high_value * high_unit
    if high < low:  # 「2-1万」这种写反的自动纠正
        low, high = high, low

    if annual:
        low, high = low / 12, high / 12

    return int(round(low)), int(round(high))


def _parse_amount(token: str) -> Optional[Tuple[float, int]]:
    """解析 ``'1.5万'`` → ``(1.5, 10000)``；无法解析返回 ``None``。"""
    match = _AMOUNT_RE.search(str(token).strip())
    if not match:
        return None
    unit = (match.group(2) or "").lower()
    return float(match.group(1)), _UNIT_MULTIPLIERS.get(unit, 1)
