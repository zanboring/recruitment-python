"""推荐算法的 Jaccard 相似度与技能归一化。"""

# 技能别名映射：把常见变体统一为规范名，提升字面不同但语义相同的技能匹配率。
# 例如「Java开发」「Java工程师」→「java」，「Vue.js」「Vue3」→「vue」。
SKILL_ALIASES = {
    # 语言/框架的大小写与写法变体
    "python3": "python",
    "js": "javascript",
    "golang": "go",
    "vuejs": "vue",
    "reactjs": "react",
    "nodejs": "node",
    "springboot": "spring",
    "springmvc": "spring",
    "springcloud": "spring",
    "mybatisplus": "mybatis",
    # 中文带后缀的变体
    "java开发": "java",
    "java工程师": "java",
    "前端开发": "前端",
    "后端开发": "后端",
    "全栈开发": "全栈",
    "数据分析师": "数据分析",
}


def normalize_skill(skill: str) -> str:
    """技能名归一化：小写 + 去除空格/点/连字符/下划线 + 别名映射。

    使「Java」「Java开发」「JAVA」以及「Spring Boot」「SpringBoot」这类
    字面写法不同但指向同一技能的名称能被 Jaccard 正确匹配。
    """
    if not skill:
        return ""
    s = skill.strip().lower()
    for ch in (" ", ".", "-", "_"):
        s = s.replace(ch, "")
    return SKILL_ALIASES.get(s, s)


def jaccard_similarity(set1: set, set2: set) -> float:
    if not set1 and not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def _parse_skills(skills: str) -> set:
    """把逗号分隔的技能字符串解析为归一化后的集合。"""
    if not skills:
        return set()
    result = set()
    for s in skills.split(","):
        norm = normalize_skill(s)
        if norm:
            result.add(norm)
    return result


def skill_similarity(user_skills: str, job_skills: str) -> float:
    """用户技能与岗位技能的 Jaccard 相似度（先做技能归一化）。"""
    user_skill_set = _parse_skills(user_skills)
    job_skill_set = _parse_skills(job_skills)
    return jaccard_similarity(user_skill_set, job_skill_set)
