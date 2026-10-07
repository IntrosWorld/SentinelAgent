import json
import os
import re
from typing import Any, Protocol

from sentinel_agent.egress.client import EgressClient, EgressError

from sentinel_agent.schemas import Plan
from sentinel_agent.models import ModelProfile


class ModelUnavailable(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    """Extract one JSON object even if a small model wraps it in prose."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Model response did not contain a JSON object")
    return json.loads(candidate[start : end + 1])


def _plan_prompt(instruction: str, available_actions: dict[str, str], state: dict[str, Any]) -> str:
    schema = Plan.model_json_schema()
    return f"""You are the planner for a safety-aware computer agent.
Return JSON only. Never invent an action. Use the fewest actions that complete the goal.
Do not include login credentials. Sensitive actions are allowed in a plan because a separate
policy engine will require human approval.

AVAILABLE ACTIONS:
{json.dumps(available_actions, indent=2)}

CURRENT STATE:
{json.dumps(state, indent=2)}

USER INSTRUCTION:
{instruction}

REQUIRED JSON SCHEMA:
{json.dumps(schema)}
"""


class PlannerModel(Protocol):
    source: str
    model: str

    async def health(self) -> dict[str, Any]: ...

    async def create_plan(
        self, instruction: str, available_actions: dict[str, str], state: dict[str, Any]
    ) -> Plan: ...


class OllamaPlanner:
    source = "ollama"
    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def health(self) -> dict[str, Any]:
        try:
            async with EgressClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                names = [m["name"] for m in response.json().get("models", [])]
            return {"online": True, "model": self.model, "installed": self.model in names, "models": names}
        except (EgressError, KeyError) as exc:
            return {"online": False, "model": self.model, "installed": False, "error": str(exc)}

    async def create_plan(
        self, instruction: str, available_actions: dict[str, str], state: dict[str, Any]
    ) -> Plan:
        prompt = _plan_prompt(instruction, available_actions, state)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            # A compact budget matters on 16 GB CPU/shared-memory laptops.
            "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 700},
        }
        try:
            async with EgressClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
        except EgressError as exc:
            raise ModelUnavailable(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc

        try:
            return Plan.model_validate(_extract_json(response.json()["response"]))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise ModelUnavailable(f"The local model returned an invalid plan: {exc}") from exc


class ProviderPlanner:
    """Planner adapter for OpenAI-compatible, Anthropic, and Gemini endpoints."""

    def __init__(self, profile: ModelProfile):
        self.profile = profile
        self.model = profile.model
        self.source = f"{profile.provider}:{profile.name}"

    def _headers(self) -> dict[str, str]:
        if self.profile.api_key_env and not self.profile.api_key:
            raise ModelUnavailable(
                f"Environment variable {self.profile.api_key_env} is not set"
            )
        if self.profile.provider == "anthropic":
            return {
                "x-api-key": self.profile.api_key or "",
                "anthropic-version": "2023-06-01",
            }
        if self.profile.provider == "gemini":
            return {"x-goog-api-key": self.profile.api_key or ""}
        return ({"Authorization": f"Bearer {self.profile.api_key}"} if self.profile.api_key else {})

    async def health(self) -> dict[str, Any]:
        base = self.profile.base_url.rstrip("/")
        result = {
            "online": False,
            "installed": False,
            "profile": self.profile.name,
            "provider": self.profile.provider,
            "model": self.model,
        }
        try:
            headers = self._headers()
            async with EgressClient(timeout=min(self.profile.timeout, 10)) as client:
                if self.profile.provider in {"ollama", "openai_compatible"}:
                    url = f"{base}/models" if self.profile.provider == "openai_compatible" else f"{base}/api/tags"
                    response = await client.get(url, headers=headers)
                    data = response.json()
                    raw_models = data.get("data", []) if self.profile.provider == "openai_compatible" else data.get("models", [])
                    names = [item.get("id") or item.get("name") for item in raw_models]
                    result.update(online=True, installed=self.model in names, models=names)
                else:
                    # Avoid a billable generation for cloud health checks; valid configuration is ready.
                    result.update(online=True, installed=True, configured=True)
        except (EgressError, KeyError, ModelUnavailable, ValueError) as exc:
            result["error"] = str(exc)
        return result

    async def create_plan(
        self, instruction: str, available_actions: dict[str, str], state: dict[str, Any]
    ) -> Plan:
        prompt = _plan_prompt(instruction, available_actions, state)
        base = self.profile.base_url.rstrip("/")
        provider = self.profile.provider
        if provider == "openai_compatible":
            url = f"{base}/chat/completions"
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }
        elif provider == "anthropic":
            url = f"{base}/v1/messages"
            payload = {
                "model": self.model,
                "max_tokens": 900,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            }
        elif provider == "gemini":
            url = f"{base}/models/{self.model}:generateContent"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            }
        else:
            raise ModelUnavailable(f"Unsupported provider: {provider}")
        try:
            async with EgressClient(timeout=self.profile.timeout) as client:
                response = await client.post(url, headers=self._headers(), json=payload)
            body = response.json()
            if provider == "openai_compatible":
                text = body["choices"][0]["message"]["content"]
            elif provider == "anthropic":
                text = next(item["text"] for item in body["content"] if item["type"] == "text")
            else:
                text = body["candidates"][0]["content"]["parts"][0]["text"]
            return Plan.model_validate(_extract_json(text))
        except (EgressError, KeyError, StopIteration, ValueError, json.JSONDecodeError) as exc:
            raise ModelUnavailable(
                f"{self.profile.name} could not create a valid plan: {exc}"
            ) from exc


def planner_from_profile(profile: ModelProfile) -> PlannerModel:
    if profile.provider == "ollama":
        return OllamaPlanner(profile.base_url, profile.model, profile.timeout)
    return ProviderPlanner(profile)
