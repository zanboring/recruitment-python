import json
import os
import re
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def interpolate_env_vars(content: str) -> str:
    def replace_var(match):
        var_name = match.group(1)
        default = match.group(2)
        return os.getenv(var_name, default if default else "")

    pattern = r'\$\{(\w+)(?::([^}]*))?\}'
    return re.sub(pattern, replace_var, content)


def load_env_with_interpolation(env_file: str = ".env") -> dict:
    env_vars = {}
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = interpolate_env_vars(value.strip())
    return env_vars


class Settings(BaseSettings):
    # ``production`` enables fail-fast checks at application startup.
    app_env: str = "development"
    db_url: str = ""
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "recruitment_db"
    db_username: str = "root"
    db_password: str = ""

    # 开发环境默认密钥，仅用于本地演示；生产部署必须通过环境变量 JWT_SECRET 覆盖
    jwt_secret: str = "dev_only_jwt_secret_change_in_production"
    jwt_expiration: int = 86400000

    # ---- AI 对话模型（OpenAI 兼容协议）----
    # 主服务商：deepseek（默认）| zhipu。另一个自动作为备选，
    # 实现「多供应商容灾」—— 主服务商密钥过期或额度用尽时链路不中断。
    ai_provider: str = "deepseek"

    # DeepSeek（默认主模型）。密钥留空则自动降级到本地 Ollama。
    # 申请地址：https://platform.deepseek.com/api_keys
    deepseek_api_key: str = ""
    deepseek_api_url: str = "https://api.deepseek.com/chat/completions"
    deepseek_model: str = "deepseek-flash"
    # 思考模式强度：none 关闭 / low / high / max。
    # 招聘问答场景用 none 即可，开启会显著增加延迟与 token 消耗。
    deepseek_reasoning_effort: str = "none"

    # 智谱 GLM（备选服务商，同为 OpenAI 兼容协议）
    zhipuai_api_key: str = ""
    zhipuai_api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    zhipuai_model: str = "glm-4-flash"

    embedding_enabled: bool = True
    embedding_model: str = "embedding-3"
    embedding_api_url: str = "https://open.bigmodel.cn/api/paas/v4/embeddings"
    embedding_dimensions: int = 1024

    # 向量化后端：
    #   auto    有智谱密钥则用云端 embedding-3，否则自动改用本地 Ollama（默认，最省心）
    #   zhipu   强制云端智谱 embedding-3（需 ZHIPUAI_API_KEY）
    #   ollama  强制本地 Ollama（离线、零成本、数据不出内网，需先 ollama pull 向量模型）
    embedding_backend: str = "auto"
    ollama_embedding_model: str = "nomic-embed-text"

    # 知识库检索策略，取值：
    #   auto      语义优先，无命中或异常时降级关键词（默认）
    #   semantic  仅语义向量检索
    #   keyword   仅关键词检索（无需密钥，可离线）
    #   hybrid    向量 + 关键词的 RRF 融合
    # 三种策略的检索质量对比见 scripts/eval_rag.py
    rag_retrieval_strategy: str = "auto"

    # ---- 本地模型（Ollama）----
    # 本地不是「一个备胎」，而是按能力分工的两类模型：
    #   ollama_model       语言类 —— 对话生成、分析报告（语言理解与表达更强）
    #   ollama_code_model  代码类 —— 工具识别、JSON 结构化抽取（小而快）
    #   ollama_tool_model  显式指定工具识别模型；留空则自动用 code_model，再回退 model
    # 分工依据见 app/services/ollama_client.py 的实测对比表，
    # 可用 scripts/bench_local_models.py 在本机复现。
    ollama_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b"
    ollama_code_model: str = "qwen2.5-coder:7b"
    ollama_tool_model: str = ""

    # ---- ReAct Agent ----
    # 多步推理 Agent 的最大步数上限。每步都是一次完整 LLM 调用（有成本、有延迟），
    # 必须给循环一个上界，防止模型反复调用工具造成死循环或成本失控。
    # 达到上限仍无 Final Answer 时，Agent 会强制基于已有 Observation 收尾。
    agent_max_steps: int = 5

    # ---- Function Calling 的工具识别前置过滤 ----
    # 朴素实现对每条消息都发起一次「是否需要查库」的 LLM 调用（实测约 2.9s/次，
    # 云端则多一次 token 消耗），而大量消息本就不涉及数据库。
    # 过滤规则保守（仅在「短消息且无领域信号」时跳过），可整体关闭以便回退。
    # 详细说明与判定表见 app/services/tool_service.py::should_detect_tool
    ai_tool_prefilter_enabled: bool = True
    ai_tool_prefilter_max_chars: int = 24

    # ---- 知识库自动入库 ----
    # 每次问答都会把回答沉淀进知识库，形成「AI 生成内容 → 被当作知识召回 →
    # 再次喂给 AI」的闭环。答错时错误会被固化并反复出现，因此提供总开关。
    ai_auto_learn_enabled: bool = True

    # ---- 输出长度上限（成本与延迟的硬边界）----
    # 原实现不给云端请求传 max_tokens，Ollama 的本地对话也不传 num_predict
    # （Ollama 默认 -1 = 不限），于是单次回答的输出长度没有任何上界：
    # 按 token 计费的云端模型成本不可控，本地模型则可能让用户等上几分钟。
    # 这里给一个默认上限，分析报告这类需要长输出的场景单独放宽。
    ai_max_output_tokens: int = 1024
    ai_analysis_max_output_tokens: int = 2048
    # 工具识别只需输出一行 JSON 或 NONE，128 足够
    ai_tool_detect_max_tokens: int = 128

    # ---- Token 用量与成本统计 ----
    # 记录每次 LLM / embedding 调用的 token 消耗、耗时与降级路径，用于成本核算。
    # 单价按「元 / 百万 token」计，默认值仅供估算，请按实际采购价调整。
    ai_usage_log_enabled: bool = True
    usage_price_deepseek_input: float = 2.0
    usage_price_deepseek_output: float = 8.0
    usage_price_zhipu_input: float = 0.1
    usage_price_zhipu_output: float = 0.1
    # 本地模型无 API 费用，此处仅用于把「省下多少」折算成可比数字
    usage_price_local: float = 0.0

    crawl_max_jobs: int = 200
    crawl_retry_times: int = 4
    crawl_delay_min: int = 12
    crawl_delay_max: int = 18
    crawl_timeout: int = 45

    server_port: int = 8080
    cors_origins: list[str] = ["http://localhost:5173"]

    # ---- 接口限流（滑动窗口，单进程内存实现）----
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_default_per_minute: int = 120
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_minute: int = 5
    rate_limit_ai_per_minute: int = 20

    # 是否信任 X-Forwarded-For 作为客户端 IP。
    # 默认 False（安全优先）：代理头由客户端完全可控，若直接用于限流维度，
    # 攻击者「换个头就换一份配额」，登录接口的防爆破会被彻底绕过。
    # 只有应用确实部署在可信反向代理（Nginx / 网关）之后、且代理会重写该头时才开启。
    rate_limit_trust_forwarded_for: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def resolved_embedding_backend(self) -> str:
        """把 auto 解析为具体向量化后端。

        刻意不使用「先试 A 失败再试 B」的隐式回退：显式解析让实际走到哪个后端
        一目了然（也会记进日志），排查问题时不必猜。
        """
        backend = (self.embedding_backend or "auto").strip().lower()
        if backend != "auto":
            return backend
        return "zhipu" if self.zhipuai_api_key else "ollama"


interpolated_env = load_env_with_interpolation()
# A deployment platform's environment variables must win over values in a
# checked-out .env file.  The old ``update`` call inverted that precedence and
# could accidentally replace a production secret with a development value.
for _key, _value in interpolated_env.items():
    os.environ.setdefault(_key, _value)

settings = Settings()


def validate_production_settings() -> None:
    """Fail early instead of serving production traffic with demo secrets."""
    if settings.app_env.lower() not in {"production", "prod"}:
        return
    if settings.jwt_secret == "dev_only_jwt_secret_change_in_production":
        raise RuntimeError("JWT_SECRET must be configured in production")
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters in production")
