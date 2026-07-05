from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


ProviderName = Literal["ollama", "openai_compatible", "anthropic", "gemini"]


class ModelProfile(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    provider: ProviderName
    model: str = Field(min_length=1)
    base_url: str
    api_key_env: str | None = None
    timeout: float = Field(default=120.0, gt=0)

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


class ModelConfiguration(BaseModel):
    active: str = "local-ollama"
    profiles: list[ModelProfile]


DEFAULT_PROFILES = [
    ModelProfile(
        name="local-ollama",
        provider="ollama",
        model="qwen2.5-coder:7b",
        base_url="http://127.0.0.1:11434",
    ),
    ModelProfile(
        name="lm-studio",
        provider="openai_compatible",
        model="local-model",
        base_url="http://127.0.0.1:1234/v1",
    ),
    ModelProfile(
        name="openai",
        provider="openai_compatible",
        model="gpt-5-mini",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    ),
    ModelProfile(
        name="anthropic",
        provider="anthropic",
        model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    ModelProfile(
        name="gemini",
        provider="gemini",
        model="gemini-3.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_env="GEMINI_API_KEY",
    ),
]


class ModelRegistry:
    """Persistent profiles containing connection metadata, never secret values."""

    def __init__(self, path: Path | str = "sentinel_models.json"):
        self.path = Path(path)

    def load(self) -> ModelConfiguration:
        if not self.path.exists():
            return ModelConfiguration(active="local-ollama", profiles=DEFAULT_PROFILES)
        return ModelConfiguration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, configuration: ModelConfiguration) -> None:
        self.path.write_text(
            configuration.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    def list(self) -> list[ModelProfile]:
        return self.load().profiles

    def get(self, name: str | None = None) -> ModelProfile:
        configuration = self.load()
        selected = name or configuration.active
        for profile in configuration.profiles:
            if profile.name == selected:
                return profile
        raise KeyError(f"Unknown model profile: {selected}")

    def use(self, name: str) -> ModelProfile:
        configuration = self.load()
        profile = next((item for item in configuration.profiles if item.name == name), None)
        if profile is None:
            raise KeyError(f"Unknown model profile: {name}")
        configuration.active = name
        self.save(configuration)
        return profile

    def add(self, profile: ModelProfile) -> None:
        configuration = self.load()
        configuration.profiles = [
            item for item in configuration.profiles if item.name != profile.name
        ] + [profile]
        self.save(configuration)

    def safe_view(self) -> dict:
        configuration = self.load()
        return json.loads(configuration.model_dump_json())
