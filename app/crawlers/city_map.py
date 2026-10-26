"""BOSS 直聘城市编码映射。

**为什么必须是白名单，而不是「查不到就用默认值」**

原实现对未收录城市返回北京的编码（``101010100``），后果是：搜索「南昌」时
实际请求的是**北京**的城市编码，抓回来的是北京岗位，却被记在「南昌」这条
爬取任务名下，城市分布统计随之失真。整个过程不报错、不留任何痕迹 ——
属于最难发现的一类数据污染（数据看起来是好的，只是城市是错的）。

因此现在：未收录城市返回 ``None``，由调用方显式报错并给出支持清单。
**宁可让任务明确失败，也不要静默返回错城市的假数据。**

新增城市只需在 ``CITY_CODE_MAP`` 里补一行 —— 编码取自 BOSS 直聘城市筛选页
URL 的 ``city=`` 查询参数，不要凭猜测填写。
"""
from typing import List, Optional

CITY_CODE_MAP = {
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "武汉": "101200100",
    "南京": "101190100",
    "长沙": "101250100",
    "重庆": "101040100",
    "西安": "101110100",
    "天津": "101030100",
    "苏州": "101190400",
    "郑州": "101180100",
    "青岛": "101120200",
    "东莞": "101280500",
    "佛山": "101280800",
    "合肥": "101220100",
    "厦门": "101230200",
    "大连": "101070100",
}


class UnsupportedCityError(ValueError):
    """请求的城市不在已收录范围内（此时继续爬取会得到其他城市的数据）。"""


def supported_cities() -> List[str]:
    """返回已收录的城市清单（供接口暴露与前端下拉框使用）。"""
    return sorted(CITY_CODE_MAP)


def get_city_code(city_name: str) -> Optional[str]:
    """返回城市对应的 BOSS 城市编码；未收录或为空时返回 ``None``。

    注意：**不要**给这个函数加「默认值回退」—— 回退的代价是把别的城市的
    数据当成目标城市的数据入库，详见模块文档。
    """
    if not city_name:
        return None
    return CITY_CODE_MAP.get(str(city_name).strip())


def require_city_code(city_name: str) -> str:
    """取城市编码，未收录时抛出带支持清单的明确错误。

    错误信息里直接列出支持的城市，使用者不必再去翻代码。
    """
    if not str(city_name or "").strip():
        raise UnsupportedCityError(
            f"必须指定城市。当前已收录：{', '.join(supported_cities())}"
        )
    code = get_city_code(city_name)
    if code is None:
        raise UnsupportedCityError(
            f"城市「{city_name}」未收录，无法爬取 —— 继续执行会抓到其他城市的数据。"
            f"当前已收录：{', '.join(supported_cities())}。"
            f"如需支持，请在 app/crawlers/city_map.py 的 CITY_CODE_MAP 中补充其城市编码。"
        )
    return code
