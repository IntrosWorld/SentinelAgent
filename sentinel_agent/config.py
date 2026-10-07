from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="SENTINEL_", extra="ignore"
    )

    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5-coder:7b"
    model_timeout: float = 120.0
    max_steps: int = 10
    allow_real_browser: bool = False
    models_file: str = "sentinel_models.json"
    egress_enabled: bool = True
    egress_port: int = 0  # OS-assigned loopback port; supports concurrent runs.
    egress_cloud_low: str = "allow"
    egress_redact_high: bool = False
    egress_low_enabled: bool = True
    egress_approved_sites: list[str] = ["www.youtube.com"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
