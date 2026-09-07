"""jaccard 相似度与技能归一化的单元测试（纯函数，无需数据库）。

运行方式（项目根目录）：
    python -m pytest tests/test_jaccard.py -v
"""
from app.recommender.jaccard import jaccard_similarity, normalize_skill, skill_similarity


class TestNormalizeSkill:
    def test_大小写归一(self):
        assert normalize_skill("Java") == "java"
        assert normalize_skill("PYTHON") == "python"

    def test_分隔符归一(self):
        assert normalize_skill("Spring Boot") == "spring"
        assert normalize_skill("SpringBoot") == "spring"
        assert normalize_skill("Vue.js") == "vue"
        assert normalize_skill("Node.js") == "node"

    def test_中文后缀别名(self):
        assert normalize_skill("Java开发") == "java"
        assert normalize_skill("前端开发") == "前端"

    def test_空输入(self):
        assert normalize_skill("") == ""
        assert normalize_skill("  ") == ""

    def test_未命中别名返回自身(self):
        assert normalize_skill("Redis") == "redis"


class TestSkillSimilarity:
    def test_字面相同(self):
        assert skill_similarity("Java,Python", "Java,Python") == 1.0

    def test_别名归一后匹配(self):
        # 「Java开发」归一为「java」，与「Java」匹配
        assert skill_similarity("Java开发", "Java") == 1.0

    def test_部分匹配(self):
        # {java, python} 与 {java} => 交集 1 / 并集 2 = 0.5
        assert abs(skill_similarity("Java,Python", "Java") - 0.5) < 1e-9

    def test_完全不同(self):
        assert skill_similarity("Java", "Python") == 0.0

    def test_空技能(self):
        assert skill_similarity("", "") == 0.0
        assert skill_similarity("Java", "") == 0.0


class TestJaccard:
    def test_基础(self):
        assert abs(jaccard_similarity({1, 2}, {2, 3}) - 1 / 3) < 1e-9

    def test_都为空(self):
        assert jaccard_similarity(set(), set()) == 0.0
