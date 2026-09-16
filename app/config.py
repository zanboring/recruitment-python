import json
import os
import re
import sys
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def interpolate_env_vars(content: str) -> str:
    def replace_var(match):
        var_name = match.group(1)
        default = match.group(2)
        return os.getenv(var_name, default if default else "")

    pattern = r'\$\{(\w+)(?::([^}]*))?\}'
    return re.sub(pattern, replace_var, content)



# ---- 运行时配置持久化（前端设置栏填写 → 写 config.env → 热生效）----
# 通用版用户换电脑时无需碰代码/config 文件：软件启动后在「设置」页填入
# API Key 即可，保存即写入 exe 同目录 config.env（配置加载链同一文件，天然生效）；
# 源码模式写入项目根 config.env。空 key 会回写空值并触发下次加载跳过。
# 允许通过设置栏修改的键白名单（其余键禁止写入，防止越权篡改）。
RUNTIME_EDITABLE_KEYS = (
    "ai_provider",
    "deepseek_api_key", "deepseek_api_url", "deepseek_model",
    "zhipuai_api_key", "zhipuai_api_url", "zhipuai_model",
    "ollama_enabled", "ollama_base_url",
    "ollama_model", "ollama_code_model", "ollama_tool_model",
)


def runtime_env_file() -> str:
    """返回设置栏写入的配置文件路径（与加载链最高优先级一致）。"""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent / "config.env")
    return str(Path(__file__).resolve().parent.parent / "config.env")


def masked(value: str) -> str:
    """脱敏展示：保留前 4 后 4，中间以 **** 代替；空串返回空。"""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return len(value) * "*"
    return value[:4] + "****" + value[-4:]


def save_runtime_config(pairs: dict) -> dict:
    """把用户设置持久化到 config.env 并热更新 settings 单例。

    参数 pairs 为 {小写配置键: 值}；仅白名单键允许写入，静默过滤其余。
    返回 {"written": [键...], "enabled": bool} 便于前端确认生效。

    设计要点：
    1. 不重写文件里已有的无关键（只增改白名单键），避免破坏其他配置；
    2. 空值写空串（覆盖旧的 key，等价于清除）；
    3. 写文件后同步更新 settings，本次进程立即生效（无需重启）。
    """
    path = runtime_env_file()
    existing = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                sline = line.strip()
                if not sline or sline.startswith("#") or "=" not in sline:
                    continue
                k, v = sline.split("=", 1)
                existing[k.strip()] = v.strip()

    written = []
    for key, value in pairs.items():
        if key not in RUNTIME_EDITABLE_KEYS:
            continue  # 白名单之外静默忽略
        existing[key] = "" if value is None else str(value)
        written.append(key)

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for k, v in existing.items():
            # 落盘统一大写键名（与 .env / config.template.env 的书写习惯一致）
            fh.write(f"{k.upper()}={v}\n")

    # 热生效：同步进程环境变量 + 直接赋值 settings 单例（无需重启）
    for key in written:
        raw = existing[key]
        env_key = key.upper()
        if key == "ollama_enabled":
            bool_val = raw.lower() in ("1", "true", "yes", "on")
            os.environ[env_key] = "true" if bool_val else "false"
            setattr(settings, key, bool_val)
        else:
            os.environ[env_key] = raw
            setattr(settings, key, raw)

    logger = __import__("logging").getLogger(__name__)
    logger.info("设置栏更新配置文件 %s：%s", path, ",".join(written))
    return {"written": written, "file": path}

def _candidate_env_files() -> list:
    """按优先级返回候选配置文件路径。

    通用版（发布软件）场景下，用户把 config.env 放在 exe 同目录即可，
    不需要碰任何代码 —— 「别的电脑只放 config.env 配好 API key 即用」。
    优先级：exe 同级 config.env > 项目根 config.env > 项目根 .env。
    """
    candidates = []
    # 兜底 .env（本地调试版）放在最前，会被后续的 config.env 覆盖
    candidates.append(str(Path(__file__).resolve().parent.parent / ".env"))
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后：exe 所在目录 config.env（最高优先级）
        candidates.append(str(Path(sys.executable).resolve().parent / "config.env"))
    else:
        # 源码运行：项目根 config.env（最高优先级）
        candidates.append(str(Path(__file__).resolve().parent.parent / "config.env"))
    return candidates


def load_env_with_interpolation(env_file: str = "") -> dict:
    """读取候选配置文件，后面的文件覆盖前面的同名变量。

    设计成「候选列表逐个叠加」而非「只读一个」：通用版用户的 config.env
    可只写要覆盖的 key（API Key 等），其余配置继续从 .env 兜底；
    未携带任何文件时返回 {}（全部用环境变量 / 默认值）。
    """
    files = [env_file] if env_file else _candidate_env_files()
    env_vars = {}
    for f in files:
        if not f or not os.path.exists(f):
            continue
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
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
    # 数据库类型：mysql（默认）| postgres | sqlite。
    # 显式设置 DB_URL 时以 DB_URL 为准（优先级更高）。
    db_type: str = "mysql"
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

    # ---- 熔断器（防级联雪崩）----
    # 云端连续失败 N 次后进入 OPEN，冷却期内快速失败（不发起真实请求），
    # 半开后放行一个试探请求。false 可整体关闭。
    ai_circuit_breaker_enabled: bool = True
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_cooldown_seconds: int = 30

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
    crawl_adaptive_delay_factor: tuple = (8, 16)
    crawl_proxy: str = ""
    crawl_timeout: int = 45

    # ---- 岗位存活核查（job_checker）----
    # 默认开启：启动后后台慢速核查已存 URL 的岗位是否仍在线
    job_checker_enabled: bool = True
    job_checker_batch_size: int = 10        # 每轮核查条数（慢速小批）
    checker_delay_min: float = 8.0         # 两条之间随机延迟下限（秒）
    checker_delay_max: float = 15.0        # 随机延迟上限（秒）
    job_checker_round_interval: int = 4    # 两轮之间间隔（小时）

    server_port: int = 8080

    # ---- Redis 缓存（可选）----
    # 配置 REDIS_URL（如 redis://localhost:6379/0）后启用 Redis 缓存；
    # 留空或连接失败时自动降级为进程内内存缓存，业务无感知。
    redis_url: str = ""
    redis_cache_ttl: int = 600
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

    # ---- 定时任务（APScheduler，时区 Asia/Shanghai）----
    # 原先触发时间写死在 app/scheduler.py（爬取 02:00、日报 06:30），
    # 换部署环境必须改代码。这里改为配置化，并把开关也一并放开。
    scheduled_crawl_enabled: bool = True
    scheduled_crawl_hour: int = 2
    scheduled_crawl_minute: int = 0

    report_enabled: bool = True
    report_hour: int = 6
    report_minute: int = 30

    # ---- 日报推送 Webhook（可选）----
    # 默认关闭：不配置就只在本地产出 Excel，不发任何外部请求。
    # 支持 wecom（企业微信机器人）/ dingtalk（钉钉机器人）/ generic（通用 JSON）。
    report_webhook_enabled: bool = False
    report_webhook_type: str = "wecom"
    report_webhook_url: str = ""
    # 推送内容里最多列几个城市/技能，避免消息过长被机器人截断
    report_webhook_top_n: int = 5

    # ---- 版本与更新检查 ----
    # 指向 GitHub 仓库（owner/name，也接受完整 URL）。用于「检查更新」接口。
    update_repo: str = "zanboring/recruitment-python"
    # 默认开启，但**只在接口被调用时才发请求**（不做启动时自动联网）：
    # 绿色版可能跑在内网/离网机器上，启动即联网只会带来无谓等待与失败日志。
    update_check_enabled: bool = True

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


_config_files = _candidate_env_files()
# 两层来源语义不同：
#   .env        —— 本地调试默认值，可用 setdefault（平台环境变量优先）
#   config.env  —— 用户配置层（手动放置或前端设置栏写入），必须强制覆盖
#                   .env 插值占位，否则设置栏填的 Key 永远不会生效。
_env_defaults: dict = {}
_env_overrides: dict = {}
for _f in _config_files:
    _vals = load_env_with_interpolation(_f)
    if str(_f).endswith("config.env"):
        _env_overrides.update(_vals)
    else:
        _env_defaults.update(_vals)

# 平台环境变量必须赢过版本库里的 .env（防止用开发值覆盖生产密钥）
for _key, _value in _env_defaults.items():
    os.environ.setdefault(_key, _value)
# 用户配置层强制生效：设置栏保存的 Key 在此处覆盖 .env 插值占位
os.environ.update(_env_overrides)

settings = Settings()


def validate_production_settings() -> None:
    """Fail early instead of serving production traffic with demo secrets."""
    if settings.app_env.lower() not in {"production", "prod"}:
        return
    if settings.jwt_secret == "dev_only_jwt_secret_change_in_production":
        raise RuntimeError("JWT_SECRET must be configured in production")
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters in production")
