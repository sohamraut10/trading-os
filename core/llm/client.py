"""
Shared LLM client abstraction for SentimentAgent and TradeJournal, which both
need a "generate text from a prompt" call and a graceful no-op when no
provider is configured. Without this, adding a second provider would mean
duplicating provider-selection and fallback logic in both places.
"""
import asyncio
from typing import Protocol

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    from google import genai
    from google.genai import types as genai_types
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False


def _looks_like_real_key(key: str) -> bool:
    return bool(key) and not key.startswith("your_") and len(key) > 10


class LLMClient(Protocol):
    async def generate(
        self, system_prompt: str, user_content: str, max_tokens: int = 512, temperature: float | None = None
    ) -> str: ...


class AnthropicLLM:
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    async def generate(
        self, system_prompt: str, user_content: str, max_tokens: int = 512, temperature: float | None = None
    ) -> str:
        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user_content}],
        )
        if system_prompt:
            kwargs["system"] = system_prompt
        if temperature is not None:
            kwargs["temperature"] = temperature

        # anthropic's client is sync-only; run it off the event loop thread.
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: self._client.messages.create(**kwargs))
        return response.content[0].text


class GeminiLLM:
    def __init__(self, api_key: str, model: str):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def generate(
        self, system_prompt: str, user_content: str, max_tokens: int = 512, temperature: float | None = None
    ) -> str:
        config_kwargs = {"max_output_tokens": max_tokens}
        if system_prompt:
            config_kwargs["system_instruction"] = system_prompt
        if temperature is not None:
            config_kwargs["temperature"] = temperature

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=genai_types.GenerateContentConfig(**config_kwargs),
        )
        return response.text


def build_llm_client(
    provider: str,
    anthropic_api_key: str,
    gemini_api_key: str,
    anthropic_model: str,
    gemini_model: str,
) -> LLMClient | None:
    """
    provider: "anthropic" | "gemini" | "auto".
    "auto" prefers Anthropic (the long-standing default) and falls back to
    Gemini if only a Gemini key is configured. Returns None if neither
    provider is usable (missing key or SDK not installed) — callers already
    have their own heuristic/no-op fallback for that case.
    """
    provider = (provider or "auto").lower()

    def _anthropic() -> LLMClient | None:
        if _ANTHROPIC_AVAILABLE and _looks_like_real_key(anthropic_api_key):
            return AnthropicLLM(anthropic_api_key, anthropic_model)
        return None

    def _gemini() -> LLMClient | None:
        if _GEMINI_AVAILABLE and _looks_like_real_key(gemini_api_key):
            return GeminiLLM(gemini_api_key, gemini_model)
        return None

    if provider == "anthropic":
        return _anthropic()
    if provider == "gemini":
        return _gemini()
    return _anthropic() or _gemini()
