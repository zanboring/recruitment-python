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

SKILL_PATTERNS = [re.compile(re.escape(skill), re.IGNORECASE) for skill in SKILL_KEYWORDS]


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
    if not salary_str:
        return 0, 0
    match = re.match(r'(\d+)[Kk]-(\d+)[Kk]', salary_str)
    if match:
        return int(match.group(1)) * 1000, int(match.group(2)) * 1000
    match = re.match(r'(\d+)[Kk]', salary_str)
    if match:
        return int(match.group(1)) * 1000, int(match.group(1)) * 1000
    return 0, 0


def extract_skills(title: str, description: str) -> str:
    text = (title + " " + description).lower()
    found = []
    all_skills = []
    for category, skills in SKILL_KEYWORDS_FULL.items():
        all_skills.extend(skills)
    all_skills.sort(key=len, reverse=True)
    for skill in all_skills:
        if skill.lower() in text and skill not in found:
            found.append(skill)
        if len(found) >= 8:
            break
    return ",".join(found)


import hashlib


def generate_job_key(source_site: str, job_id: str) -> str:
    return f"{source_site}_{hashlib.sha256(job_id.encode()).hexdigest()}"


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
        "description": job.get("description", "").strip()[:5000],
    }

    min_salary, max_salary = parse_salary(cleaned["salary"])
    cleaned["min_salary"] = min_salary
    cleaned["max_salary"] = max_salary

    if not cleaned["skills"]:
        cleaned["skills"] = extract_skills(cleaned["title"], cleaned["description"])

    return cleaned