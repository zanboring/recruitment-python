CITY_COEFFICIENTS = {
    "北京": 1.80,
    "上海": 1.75,
    "深圳": 1.65,
    "杭州": 1.45,
    "广州": 1.40,
    "南京": 1.35,
    "成都": 1.30,
    "武汉": 1.25,
    "长沙": 1.05,
}

EXPERIENCE_COEFFICIENTS = {
    "10年以上": 1.80,
    "5-10年": 1.50,
    "3-5年": 1.20,
    "1-3年": 0.90,
    "应届": 0.70,
    "1年以下": 0.70,
}

EDUCATION_COEFFICIENTS = {
    "博士": 1.70,
    "硕士": 1.35,
    "本科": 1.00,
    "大专": 0.80,
}

HIGH_PAY_SKILLS = ["算法", "大数据", "人工智能", "机器学习", "深度学习", "Go", "架构师"]
INDUSTRY_BASE_SALARY = 12000
MIN_SALARY = 5000
MAX_SALARY = 100000


def get_city_coefficient(city: str) -> float:
    return CITY_COEFFICIENTS.get(city, 1.00)


def get_experience_coefficient(experience: str) -> float:
    for key in EXPERIENCE_COEFFICIENTS:
        if key in experience:
            return EXPERIENCE_COEFFICIENTS[key]
    return 1.00


def get_education_coefficient(education: str) -> float:
    for key in EDUCATION_COEFFICIENTS:
        if key in education:
            return EDUCATION_COEFFICIENTS[key]
    return 1.00


def calculate_skill_premium(skills: str) -> float:
    if not skills:
        return 0.0
    premium = 0.0
    for skill in HIGH_PAY_SKILLS:
        if skill in skills:
            premium += 0.05
    return min(premium, 0.15)


def predict_salary(
    city: str = "",
    education: str = "",
    experience: str = "",
    skills: str = ""
) -> dict:
    city_coeff = get_city_coefficient(city)
    exp_coeff = get_experience_coefficient(experience)
    edu_coeff = get_education_coefficient(education)
    skill_premium = calculate_skill_premium(skills)

    predicted = INDUSTRY_BASE_SALARY * (city_coeff * 0.4 + exp_coeff * 0.35 + edu_coeff * 0.25) * (1 + skill_premium)
    predicted = max(MIN_SALARY, min(MAX_SALARY, predicted))

    min_sal = predicted * 0.85
    max_sal = predicted * 1.15

    return {
        "min_salary": round(min_sal, 2),
        "max_salary": round(max_sal, 2),
        "avg_salary": round(predicted, 2),
        "city_coefficient": city_coeff,
        "experience_coefficient": exp_coeff,
        "education_coefficient": edu_coeff,
        "skill_premium": round(skill_premium * 100, 1)
    }
