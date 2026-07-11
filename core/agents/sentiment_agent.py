"""
Sentiment & News Agent
Uses Claude to NLP-analyze headlines and social sentiment into a structured signal.
Falls back to heuristic keyword scoring if LLM unavailable.
"""
import re
import asyncio
from .base_agent import BaseAgent, AgentDecision, AgentName, MarketContext, Signal

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


SYSTEM_PROMPT = """You are a financial sentiment analyst. Analyze the provided news headlines and
social sentiment data for {asset}.

Return ONLY valid JSON in this exact format:
{{
  "signal": "BUY" | "SELL" | "HOLD",
  "confidence": <integer 0-100>,
  "sentiment_breakdown": {{
    "news": <-1.0 to 1.0>,
    "social": <-1.0 to 1.0>,
    "macro": <-1.0 to 1.0>
  }},
  "key_drivers": ["<driver1>", "<driver2>"],
  "risk_flags": ["<flag1>"]
}}

Rules:
- confidence > 80 only when multiple strong concordant signals exist
- BUY when net sentiment > 0.3 with volume/conviction
- SELL when net sentiment < -0.3 with volume/conviction
- HOLD for ambiguous, contradictory, or low-signal environments
- Always flag regulatory news, black swan events, or earnings surprises as risk_flags
"""


# Heuristic keyword sets used when LLM is unavailable
BULLISH_KEYWORDS = {
    "surge", "rally", "breakout", "bull", "buy", "upgrade", "record", "beat",
    "earnings beat", "positive", "recovery", "grow", "gain", "partnership",
    "launch", "adoption", "etf approval", "institutional", "accumulation"
}

BEARISH_KEYWORDS = {
    "crash", "plunge", "dump", "bear", "sell", "downgrade", "miss", "fraud",
    "hack", "ban", "regulation", "lawsuit", "recession", "inflation",
    "default", "panic", "fear", "investigation", "warning"
}


def _heuristic_score(headlines: list[str], asset: str) -> tuple[Signal, float, str]:
    """Fallback: simple keyword counting when LLM is not available."""
    text = " ".join(headlines + [asset]).lower()
    bullish_hits = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
    bearish_hits = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
    total = bullish_hits + bearish_hits

    if total == 0:
        return Signal.HOLD, 50.0, "No significant sentiment keywords found in headlines"

    if bullish_hits > bearish_hits:
        score = (bullish_hits / total) * 100
        confidence = min(50 + (score - 50) * 0.7, 80)  # cap heuristic at 80
        return Signal.BUY, round(confidence, 1), f"Bullish keywords: {bullish_hits}, bearish: {bearish_hits}"
    elif bearish_hits > bullish_hits:
        score = (bearish_hits / total) * 100
        confidence = min(50 + (score - 50) * 0.7, 80)
        return Signal.SELL, round(confidence, 1), f"Bearish keywords: {bearish_hits}, bullish: {bullish_hits}"
    else:
        return Signal.HOLD, 50.0, "Mixed sentiment — equal bullish/bearish signals"


def _parse_llm_response(raw: str) -> dict:
    """Extract JSON from potentially noisy LLM output."""
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in LLM response")
    import json
    return json.loads(match.group())


class SentimentAgent(BaseAgent):
    name = AgentName.SENTIMENT

    def __init__(self, api_key: str = "", model: str = "claude-haiku-4-5-20251001"):
        self._model = model
        has_real_key = api_key and not api_key.startswith("your_") and len(api_key) > 10
        self._client = anthropic.Anthropic(api_key=api_key) if _ANTHROPIC_AVAILABLE and has_real_key else None

    async def _analyze(self, ctx: MarketContext) -> AgentDecision:
        headlines = ctx.news_headlines
        sentiment_raw = ctx.sentiment_raw

        if not headlines and not sentiment_raw:
            return AgentDecision(
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=50.0,
                reasoning="No news or sentiment data available",
                warnings=["no_data"],
            )

        if self._client:
            return await self._llm_analyze(ctx, headlines, sentiment_raw)
        else:
            return self._heuristic_analyze(ctx, headlines)

    async def _llm_analyze(self, ctx: MarketContext, headlines: list[str], sentiment_raw: dict) -> AgentDecision:
        user_content = f"""
Asset: {ctx.asset}
Regime: {ctx.regime}
Macro context: {ctx.macro_context}

News Headlines:
{chr(10).join(f"- {h}" for h in headlines[:20])}

Social Sentiment Data:
{sentiment_raw}
"""
        try:
            # Run sync client in thread to avoid blocking event loop
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=self._model,
                    max_tokens=512,
                    system=SYSTEM_PROMPT.format(asset=ctx.asset),
                    messages=[{"role": "user", "content": user_content}],
                    temperature=0.1,
                )
            )
            parsed = _parse_llm_response(response.content[0].text)

            signal = Signal(parsed["signal"])
            confidence = float(parsed["confidence"])
            drivers = parsed.get("key_drivers", [])
            risk_flags = parsed.get("risk_flags", [])
            breakdown = parsed.get("sentiment_breakdown", {})

            reasoning = f"LLM sentiment: {', '.join(drivers)}" if drivers else "LLM analysis complete"

            return AgentDecision(
                agent_name=self.name,
                signal=signal,
                confidence=confidence,
                reasoning=reasoning,
                indicators={
                    "sentiment_breakdown": breakdown,
                    "key_drivers": drivers,
                    "headline_count": len(headlines),
                },
                warnings=risk_flags,
            )

        except Exception as e:
            # Graceful fallback on LLM error
            signal, confidence, reasoning = _heuristic_score(headlines, ctx.asset)
            return AgentDecision(
                agent_name=self.name,
                signal=signal,
                confidence=confidence * 0.8,  # penalize fallback confidence
                reasoning=f"LLM failed ({e}), heuristic fallback: {reasoning}",
                warnings=["llm_fallback"],
            )

    def _heuristic_analyze(self, ctx: MarketContext, headlines: list[str]) -> AgentDecision:
        signal, confidence, reasoning = _heuristic_score(headlines, ctx.asset)
        return AgentDecision(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            indicators={"headline_count": len(headlines)},
            warnings=["heuristic_mode"],
        )
