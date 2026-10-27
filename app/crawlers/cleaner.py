import re
from typing import List, Dict

SENIOR_KEYWORDS = ["高级", "资深", "主管", "经理", "总监", "架构师", "专家", "负责人", "Leader"]
INVALID_KEYWORDS = ["培训", "外包", "中介", "刷单", "诈骗", "博彩", "贷款", "保险销售", "兼职", "模特"]

SKILL_KEYWORDS_FULL = {
    "后端": ["Java", "Spring", "SpringBoot", "Spring Cloud", "MyBatis", "MySQL", "Redis", "Go", "PHP", ".NET", "C#", "C++", "Node.js", "Python", "Rust", "Kafka", "RabbitMQ"],
    "前端": ["Vue", "React", "Angular", "TypeScript", "JavaScript", "HTML5", "CSS3", "Webpack", "Vite", "小程序", "Uni-app", "jQuery"],
    "数据/AI": ["Python", "机器学习", "深度学习", "TensorFlow", "PyTorch", "Hadoop", "Spark", "Flink", "LLM", "NLP", "数据分析", "数据挖掘"],
    "运维": ["Linux", "Docker", "Kubernetes", "CI/CD", "Jenkins", "AWS", "阿里云", "Nginx", "Zabbix"],
    "测试": ["自动化测试", "Selenium", "JMeter", "Postman", "pytest", "Junit", "LoadRunner"],
    "其他": ["Git", "Maven", "Gradle", "Scrum", "系统设计", "微服务", "分布式", "高并发", "网络安全", "信息安全"],
}

SKILL_KEYWORDS = []
for category, skills in SKILL_KEYWORDS_FULL.items():
    SKILL_KEYWORDS.extend(skills)


def _build_skill_matcher(skill: str):
    """为一个技能词编译匹配器。

    **为什么不能一律用子串**：短英文词会命中别的单词内部 —— 最典型的是 ``Go``
    命中 ``Django``（这个项目里 Django 岗位极常见），于是每一条 Django 岗位都
    被额外打上 Go 标签，技能分布、技能图谱与「按技能搜索」全部被污染，而且
    没有任何报错，看起来只是"数据有点怪"。

    规则：
    - 纯字母/数字且长度 ≤3（Go / PHP / Vue / AWS / LLM / Git）→ 加词边界，避免
      ``\\bgo\\b`` 命中 ``django`` / ``logo`` / ``cargo``。
    - 含特殊字符的（C++ / C# / .NET / Node.js / CI/CD）→ 保持子串匹配，因为
      ``\\b`` 在这些符号边界上不成立，加边界反而永远命中不了。
    - 中文技能词（数据分析 / 机器学习）→ 子串匹配，中文没有词边界概念。
    """
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", skill) and len(skill) <= 3:
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(skill)}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(re.escape(skill), re.IGNORECASE)


# 预编译：词表固定，每次调用重新编译是纯浪费（爬取时每条岗位都会走一遍）
_SKILL_MATCHERS = {skill: _build_skill_matcher(skill) for skill in SKILL_KEYWORDS}

# 兼容既有引用（历史上是纯子串正则，容易被误用到判断逻辑里）
SKILL_PATTERNS = list(_SKILL_MATCHERS.values())


def is_senior_job(title: str, experience: str) -> bool:
    text = title + experience
    for kw in SENIOR_KEYWORDS:
        if kw in text:
            return True
    if "5" in experience and "年" in experience:
        return True
    if "10" in experience and "年" in experience:
        return True
    return False


def is_invalid_job(title: str, description: str) -> bool:
    text = title + description
    for kw in INVALID_KEYWORDS:
        if kw in text:
            return True
    return False


def is_high_salary(min_salary: float) -> bool:
    return min_salary and min_salary > 30000


def parse_salary(salary_str: str) -> tuple[int, int]:
    """薪资文本 → 月薪区间；实现见 ``app.utils.salary``（唯一一份）。

    历史坑：这里原先只认 ``8K-12K`` 一种写法，而 BOSS 的真实文本是
    ``15-25K·13薪`` —— 正则不匹配，于是**整站薪资全部落成 0**。
    表现是岗位能正常入库、列表能显示，只是薪资列全空、薪资分布图与
    「高薪岗位过滤」静默失效，全程没有任何报错。现在统一委托，别再改回本地正则。
    """
    from app.utils.salary import parse_salary_to_monthly

    return parse_salary_to_monthly(salary_str)


def extract_skills(title: str, description: str) -> str:
    """从标题与描述里抽取技能标签（最多 8 个，逗号分隔）。

    匹配规则见 :func:`_build_skill_matcher` —— 短英文词走词边界，
    否则 ``Go`` 会把每一条 ``Django`` 岗位都污染成双技能。
    """
    text = f"{title} {description}"
    found = []
    all_skills = []
    for category, skills in SKILL_KEYWORDS_FULL.items():
        all_skills.extend(skills)
    # 长词优先，避免 "Spring" 抢先占位后 "SpringBoot" 被挤掉
    all_skills.sort(key=len, reverse=True)
    for skill in all_skills:
        if skill in found:
            continue
        matcher = _SKILL_MATCHERS.get(skill)
        if matcher is not None and matcher.search(text):
            found.append(skill)
        if len(found) >= 8:
            break
    return ",".join(found)


def generate_job_key(source_site: str, title: str, company_name: str = "", city: str = "") -> str:
    """岗位指纹生成（薄封装）。

    实现已统一到 app.utils.job_key，保证爬虫入库与管理端新增用同一套口径，
    否则同一岗位会因两个入口算出不同的键而重复入库。
    """
    from app.utils.job_key import generate_job_key as _generate

    return _generate(source_site, title, company_name, city)


def deduplicate_jobs(jobs: List[Dict]) -> List[Dict]:
    seen = set()
    result = []
    for job in jobs:
        job_key = job.get("job_key", "")
        if job_key and job_key not in seen:
            seen.add(job_key)
            result.append(job)
    return result


def clean_job_data(job: Dict) -> Dict:
    cleaned = {
        "title": job.get("title", "").strip(),
        "company_name": job.get("company_name", "").strip(),
        "city": job.get("city", "").strip(),
        "experience": job.get("experience", "").strip(),
        "education": job.get("education", "").strip(),
        "salary": job.get("salary", "").strip(),
        "skills": job.get("skills", "").strip(),
        "source_site": job.get("source_site", "").strip(),
        "job_key": job.get("job_key", "").strip(),
        "url": (job.get("url") or "").strip(),
        "description": job.get("description", "").strip()[:5000],
    }

    min_salary, max_salary = parse_salary(cleaned["salary"])
    cleaned["min_salary"] = min_salary
    cleaned["max_salary"] = max_salary

    if not cleaned["skills"]:
        cleaned["skills"] = extract_skills(cleaned["title"], cleaned["description"])

    return cleaned