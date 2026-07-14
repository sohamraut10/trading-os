"""
Tests for the shared LLM client abstraction (core/llm/client.py) — provider
selection logic and the Anthropic/Gemini wrapper classes. No real network
calls: each wrapper's underlying SDK client is constructed for real (cheap,
no I/O) but its network-calling method is mocked.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.llm.client import AnthropicLLM, GeminiLLM, build_llm_client
from core.agents.base_agent import MarketContext, OHLCV, Signal
from core.agents.sentiment_agent import SentimentAgent

REAL_KEY = "sk-ant-a1b2c3d4e5f6g7h8"  # >10 chars, doesn't start with "your_"


# ── Provider selection ──────────────────────────────────────────────────

def test_auto_prefers_anthropic_when_both_configured():
    client = build_llm_client("auto", REAL_KEY, REAL_KEY, "claude-model", "gemini-model")
    assert isinstance(client, AnthropicLLM)


def test_auto_falls_back_to_gemini_when_only_gemini_configured():
    client = build_llm_client("auto", "", REAL_KEY, "claude-model", "gemini-model")
    assert isinstance(client, GeminiLLM)


def test_auto_returns_none_when_neither_configured():
    assert build_llm_client("auto", "", "", "claude-model", "gemini-model") is None


def test_explicit_anthropic_provider_ignores_gemini_key():
    client = build_llm_client("anthropic", REAL_KEY, REAL_KEY, "claude-model", "gemini-model")
    assert isinstance(client, AnthropicLLM)


def test_explicit_anthropic_provider_without_key_returns_none():
    assert build_llm_client("anthropic", "", REAL_KEY, "claude-model", "gemini-model") is None


def test_explicit_gemini_provider_ignores_anthropic_key():
    client = build_llm_client("gemini", REAL_KEY, REAL_KEY, "claude-model", "gemini-model")
    assert isinstance(client, GeminiLLM)


def test_explicit_gemini_provider_without_key_returns_none():
    assert build_llm_client("gemini", REAL_KEY, "", "claude-model", "gemini-model") is None


def test_placeholder_keys_are_not_treated_as_real():
    assert build_llm_client("auto", "your_anthropic_key_here", "your_gemini_key_here", "m", "m") is None


def test_short_keys_are_not_treated_as_real():
    assert build_llm_client("auto", "short", "short", "m", "m") is None


# ── AnthropicLLM.generate ────────────────────────────────────────────────

async def test_anthropic_generate_returns_text_and_forwards_params():
    llm = AnthropicLLM(REAL_KEY, "claude-model")
    fake_response = SimpleNamespace(content=[SimpleNamespace(text="hello from claude")])
    llm._client.messages = MagicMock()
    llm._client.messages.create = MagicMock(return_value=fake_response)

    result = await llm.generate("system prompt", "user content", max_tokens=123, temperature=0.5)

    assert result == "hello from claude"
    _, kwargs = llm._client.messages.create.call_args
    assert kwargs["model"] == "claude-model"
    assert kwargs["max_tokens"] == 123
    assert kwargs["system"] == "system prompt"
    assert kwargs["temperature"] == 0.5
    assert kwargs["messages"] == [{"role": "user", "content": "user content"}]


async def test_anthropic_generate_omits_system_and_temperature_when_unset():
    llm = AnthropicLLM(REAL_KEY, "claude-model")
    fake_response = SimpleNamespace(content=[SimpleNamespace(text="ok")])
    llm._client.messages = MagicMock()
    llm._client.messages.create = MagicMock(return_value=fake_response)

    await llm.generate("", "user content", max_tokens=50)

    _, kwargs = llm._client.messages.create.call_args
    assert "system" not in kwargs
    assert "temperature" not in kwargs


# ── GeminiLLM.generate ───────────────────────────────────────────────────

async def test_gemini_generate_returns_text_and_forwards_params():
    llm = GeminiLLM(REAL_KEY, "gemini-model")
    fake_response = SimpleNamespace(text="hello from gemini")
    llm._client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    result = await llm.generate("system prompt", "user content", max_tokens=200, temperature=0.2)

    assert result == "hello from gemini"
    _, kwargs = llm._client.aio.models.generate_content.call_args
    assert kwargs["model"] == "gemini-model"
    assert kwargs["contents"] == "user content"
    assert kwargs["config"].system_instruction == "system prompt"
    assert kwargs["config"].max_output_tokens == 200
    assert kwargs["config"].temperature == 0.2


async def test_gemini_generate_omits_system_instruction_when_unset():
    llm = GeminiLLM(REAL_KEY, "gemini-model")
    fake_response = SimpleNamespace(text="ok")
    llm._client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    await llm.generate("", "user content", max_tokens=50)

    _, kwargs = llm._client.aio.models.generate_content.call_args
    assert kwargs["config"].system_instruction is None


# ── SentimentAgent wiring ────────────────────────────────────────────────

async def test_sentiment_agent_uses_gemini_when_only_gemini_key_configured():
    agent = SentimentAgent(api_key="", gemini_api_key=REAL_KEY, provider="auto")
    assert isinstance(agent._client, GeminiLLM)

    fake_response = SimpleNamespace(text='{"signal": "BUY", "confidence": 77, "key_drivers": ["test"], "risk_flags": []}')
    agent._client._client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    ctx = MarketContext(
        asset="BTC/USDT", timeframe="1h",
        candles=[OHLCV(timestamp=0, open=1, high=1, low=1, close=1, volume=1)],
        current_price=1.0,
        news_headlines=["Some headline"],
        sentiment_raw={"reddit": {"mention_count": 1}},
    )
    decision = await agent.analyze(ctx)

    assert decision.signal == Signal.BUY
    assert decision.confidence == 77.0
    assert "llm_fallback" not in decision.warnings
    assert "heuristic_mode" not in decision.warnings
