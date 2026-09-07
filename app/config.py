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
    db_url: str = ""
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "recruitment_db"
    db_username: str = "root"
    db_password: str = ""

    jwt_secret: str
    jwt_expiration: int = 86400000

    zhipuai_api_key: str = ""
    zhipuai_api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    ollama_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2:7b"

    crawl_max_jobs: int = 200
    crawl_retry_times: int = 4
    crawl_delay_min: int = 12
    crawl_delay_max: int = 18
    crawl_timeout: int = 45

    server_port: int = 8080
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [o.strip() for o in v.split(",") if o.strip()]
        return v


interpolated_env = load_env_with_interpolation()
os.environ.update(interpolated_env)

settings = Settings()
