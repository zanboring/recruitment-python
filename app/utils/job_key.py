"""岗位唯一键（job_key）的统一生成规则。

**为什么必须统一**：job_key 是岗位去重的唯一依据（数据库上有 unique 约束）。
历史实现里存在两套口径 ——

  - 管理端新增：sha256(平台 + 标题 + 公司)
  - 爬虫入库：  sha256(BOSS 的 jobId)

同一岗位从两个入口进来会得到两个不同的键，于是同一份岗位在库里出现两条记录，
统计口径（岗位总数、公司排行、薪资分布）随之全部虚高。

现在统一为「平台 + 标题 + 公司 + 城市」四要素指纹：
- 带城市是因为「某公司在长沙和北京各招一个 Java 开发」是两个真实岗位；
- 用 "|" 分隔各字段，避免 ("ab","c") 与 ("a","bc") 拼出同一个字符串；
- 文本统一去空白 + 转小写，让「Java 开发」与「java 开发」判为同一岗位。
"""
import hashlib


def generate_job_key(
    source_site: str,
    title: str,
    company_name: str = "",
    city: str = "",
) -> str:
    """生成岗位指纹，格式 {平台}_{sha256(四要素)}。

    返回值恒以 `{source_site}_` 开头，前端与统计逻辑依赖该前缀约定。
    """
    parts = [
        (source_site or "").strip().lower(),
        (title or "").strip().lower(),
        (company_name or "").strip().lower(),
        (city or "").strip().lower(),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{source_site}_{digest}"
