def jaccard_similarity(set1: set, set2: set) -> float:
    if not set1 and not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def skill_similarity(user_skills: str, job_skills: str) -> float:
    user_skill_set = set(s.strip() for s in user_skills.split(",") if s.strip()) if user_skills else set()
    job_skill_set = set(s.strip() for s in job_skills.split(",") if s.strip()) if job_skills else set()
    return jaccard_similarity(user_skill_set, job_skill_set)
