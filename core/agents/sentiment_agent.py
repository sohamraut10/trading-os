"""
Sentiment & News Agent
Uses an LLM (Claude or Gemini) to NLP-analyze headlines and social sentiment
into a structured signal. Falls back to heuristic keyword scoring if no LLM
is configured or the call fails.

Index mode: uses macro/sectoral keyword sets and an index-aware system prompt
instead of generic stock/crypto heuristics. Indian indices are driven by RBI
policy, FII flows, GDP/PMI data, and crude oil — not company-level news.
"""
import re
from .base_agent import BaseAgent, AgentDecision, AgentName, MarketContext, Signal
from core.llm import build_llm_client


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

INDEX_SYSTEM_PROMPT = """You are a macro and sectoral analyst for Indian equity indices.
Analyze the provided news headlines and market data for {asset}.

Index profile:
{index_context}

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

Indian macro signal rules (apply with highest weight):
- RBI rate cut / dovish MPC / accommodative stance → BUY (especially BANKNIFTY)
- RBI rate hike / hawkish / tightening / liquidity withdrawal → SELL
- Strong FII/FPI inflows / foreign institutional buying → BUY
- FII/FPI selling / outflows / foreign exit → SELL
- GDP above expectation / PMI expansion / IIP growth → BUY
- Crude oil spike >5% / INR depreciation / rupee weakness → SELL
- India VIX above 20 / VIX spike → reduce confidence, add "elevated_vix" to risk_flags
- US Fed hawkish pivot / rate hike → SELL (triggers FII exit from EMs)
- US Fed pause / rate cut → BUY (EM inflow trigger)
- Strong Union Budget / fiscal stimulus / capex boost → BUY
- Fiscal deficit widening / tax hike / surcharge → SELL
- Global risk-off / credit events / geopolitical escalation → SELL
- confidence > 80 only when 2+ concordant macro signals exist
- HOLD when headlines are company-specific (not macro or sectoral for this index)
- HOLD when PCR is in neutral range (0.9–1.1) and no strong directional news
"""

_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50", "MIDCPNIFTY", "SENSEX"}

_INDEX_CONTEXT = {
    "NIFTY": (
        "NIFTY 50 is India's broad large-cap index. Sector weights: IT ~25%, financials ~35%, "
        "energy, pharma, auto. Key macro drivers: FII/FPI flows, US Fed policy, crude oil prices, "
        "INR/USD exchange rate, IT sector earnings, India GDP/PMI prints. "
        "Bullish catalysts: FII inflows, rate cuts, strong GDP, INR stability, global risk-on. "
        "Bearish catalysts: FII outflows, rate hikes, crude spike >$90, INR weakness, US recession fears."
    ),
    "BANKNIFTY": (
        "BANK NIFTY tracks India's top banking stocks (HDFC Bank, ICICI Bank, SBI, Kotak, Axis). "
        "Highly sensitive to RBI monetary policy — rate cuts boost NIM and loan growth. "
        "Key drivers: RBI MPC decisions, repo rate, CRR, NPA trends, credit growth, NBFC health, "
        "government bank recapitalization, liquidity conditions. "
        "Bullish: RBI rate cut, dovish MPC, falling NPAs, strong loan growth, NBFC recovery. "
        "Bearish: rate hike, hawkish RBI, NPA spike, NBFC default, IL&FS-style stress, PMC-type events."
    ),
    "FINNIFTY": (
        "FINNIFTY tracks financial services: banks, insurance, NBFCs, AMCs, wealth management. "
        "Broader than BANKNIFTY — includes HDFC Life, SBI Life, Bajaj Finance, Shriram Finance. "
        "Key drivers: RBI/SEBI regulations, interest rate cycle, mutual fund SIP flows, "
        "insurance premium growth, NBFC credit quality, gold loan demand. "
        "Bullish: rate cuts, SEBI pro-market reforms, MF SIP records, insurance sector tailwinds. "
        "Bearish: rate hikes, SEBI crackdowns, NBFC defaults, mutual fund redemption pressure."
    ),
    "NIFTYNXT50": (
        "NIFTY Next 50 — large-cap companies ranked 51–100 by market cap. Domestic consumption "
        "and infrastructure tilt. Less FII-driven, more domestic institutional (DII) driven. "
        "Key drivers: domestic demand cycle, government capex, GST revenue, rural recovery, "
        "consumption themes, PLI schemes, infrastructure spending. "
        "Bullish: domestic demand uptick, government capex push, GST record collections. "
        "Bearish: domestic consumption slowdown, fiscal tightening, high inflation."
    ),
    "MIDCPNIFTY": (
        "MIDCAP NIFTY — India's mid-cap index, proxy for domestic economic growth. "
        "Higher beta than NIFTY, more volatile, less influenced by FII (mostly DII/retail). "
        "Key drivers: rural economy, SME credit availability, domestic consumption, "
        "government infra schemes, MSME support, retail credit growth. "
        "Bullish: rural demand boost, lower rates for SMEs, government MSME schemes, consumption. "
        "Bearish: inflation squeezing margins, credit tightening, global risk-off reducing risk appetite."
    ),
    "SENSEX": (
        "BSE SENSEX — 30 blue-chip stocks, closely correlated to NIFTY 50. "
        "Key drivers: FII flows, IT/financials sector news, RBI policy, crude, INR/USD. "
        "Treat as equivalent to NIFTY for macro signal interpretation."
    ),
}

# Generic stock/crypto keywords (non-index fallback)
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

# Index macro keyword sets (multi-word phrases for precision)
_INDEX_BULLISH = {
    # RBI / monetary easing
    "rate cut", "repo cut", "rbi cut", "rbi easing", "rbi dovish", "dovish",
    "accommodative", "liquidity infusion", "crr cut", "open market operations",
    "rbi support", "mpc dovish", "reverse repo",
    # FII / institutional flows
    "fii buying", "fpi inflow", "fii inflow", "foreign buying", "fpi buying",
    "net buyers", "institutional buying", "dii buying", "foreign inflow",
    "fpi net buyer", "fii net buyer", "em inflow",
    # macro growth
    "gdp beat", "gdp growth", "gdp above", "pmi expansion", "manufacturing pmi",
    "services pmi", "iip growth", "gst record", "gst collection record",
    "fiscal surplus", "trade surplus", "export growth", "india growth",
    "growth recovery", "economic expansion", "strong earnings",
    # policy positive
    "budget boost", "capex push", "fiscal stimulus", "infrastructure push",
    "pli scheme", "production linked", "fdi inflow", "reform push",
    "divestment proceeds", "privatisation",
    # global tailwinds
    "fed pause", "fed cut", "rate pause", "federal reserve pause", "us rate cut",
    "global risk on", "em rally", "emerging market inflow", "dollar weakens",
    "crude falls", "oil falls", "brent falls", "inr strengthens", "rupee gains",
}

_INDEX_BEARISH = {
    # RBI / monetary tightening
    "rate hike", "repo hike", "rbi hike", "rbi hawkish", "tightening",
    "liquidity withdrawal", "crr hike", "rbi caution", "inflation concern",
    "mpc hawkish", "policy tightening", "rbi alert",
    # FII / institutional outflows
    "fii selling", "fpi outflow", "fii outflow", "foreign selling", "fpi selling",
    "net sellers", "institutional selling", "dii selling", "foreign exit",
    "fpi net seller", "fii net seller", "em outflow", "capital outflow",
    # macro negative
    "gdp miss", "gdp slowdown", "gdp below", "pmi contraction", "iip decline",
    "fiscal deficit", "trade deficit", "current account deficit", "rupee falls",
    "inr weakens", "inr depreciation", "currency pressure", "rupee pressure",
    # policy negative
    "budget disappointment", "tax hike", "cess hike", "surcharge",
    "capital gains tax", "stt hike", "divestment miss",
    # global headwinds
    "fed hike", "fed hawkish", "us recession", "global risk off", "risk aversion",
    "emerging market outflow", "dollar surge", "dollar rally", "crude spike",
    "oil spike", "brent spike", "china slowdown", "global slowdown",
    "geopolitical", "war escalation", "sanctions",
    # volatility / systemic
    "india vix", "vix spike", "circuit breaker", "market crash",
    "black swan", "systemic risk", "credit event", "debt default",
}

# Per-index sectoral boosters (weighted double in scoring)
_SECTOR_BULLISH = {
    "BANKNIFTY": {
        "credit growth", "npa falls", "npa decline", "gross npa", "net npa falls",
        "bank profit", "loan growth", "net interest margin", "nim expansion",
        "banking sector recovery", "bank earnings beat", "psb profit",
        "recapitalisation", "bad bank", "nbfc recovery",
    },
    "FINNIFTY": {
        "sip growth", "sip record", "mutual fund inflow", "mf inflow",
        "sebi reform", "insurance growth", "amc profit", "nbfc recovery",
        "gold loan growth", "microfinance recovery", "wealth management",
    },
    "NIFTYNXT50": {
        "domestic demand", "rural recovery", "consumption boost",
        "infrastructure spend", "government scheme",
    },
    "MIDCPNIFTY": {
        "sme growth", "msme support", "rural demand", "agri growth",
        "consumption recovery", "retail credit",
    },
}

_SECTOR_BEARISH = {
    "BANKNIFTY": {
        "npa spike", "npa rise", "gross npa rises", "bank fraud", "nbfc stress",
        "nbfc default", "yes bank", "pmla action", "rbi penalty on bank",
        "asset quality concern", "il&fs", "shadow banking stress",
    },
    "FINNIFTY": {
        "sebi penalty", "sebi ban", "amfi outflow", "mf redemption",
        "insurance claim surge", "nbfc default", "shadow banking",
        "p2p ban", "loan app ban",
    },
    "NIFTYNXT50": {
        "domestic slowdown", "rural stress", "consumption fall", "urban slowdown",
    },
    "MIDCPNIFTY": {
        "sme stress", "msme npa", "rural distress", "agri crisis",
        "farm loan waiver cost",
    },
}


def _index_heuristic_score(headlines: list[str], asset: str) -> tuple[Signal, float, str]:
    """
    Index-specific heuristic: macro keyword scoring with sectoral boosts.
    Uses phrase matching (multi-word) for higher precision than single-word.
    """
    text = " ".join(headlines).lower()
    asset_upper = asset.upper()

    bull_hits: list[str] = []
    bear_hits: list[str] = []

    for kw in _INDEX_BULLISH:
        if kw in text:
            bull_hits.append(kw)
    for kw in _INDEX_BEARISH:
        if kw in text:
            bear_hits.append(kw)

    # Sectoral hits count double (more specific = higher conviction)
    for kw in _SECTOR_BULLISH.get(asset_upper, set()):
        if kw in text:
            bull_hits.extend([kw, kw])
    for kw in _SECTOR_BEARISH.get(asset_upper, set()):
        if kw in text:
            bear_hits.extend([kw, kw])

    total = len(bull_hits) + len(bear_hits)
    if total == 0:
        return Signal.HOLD, 50.0, "No macro/sectoral signals found in headlines"

    if len(bull_hits) > len(bear_hits):
        score = (len(bull_hits) / total) * 100
        confidence = min(50 + (score - 50) * 0.7, 78)
        top = list(dict.fromkeys(bull_hits))[:3]  # deduplicated, order-preserved top hits
        return Signal.BUY, round(confidence, 1), f"Macro bullish: {', '.join(top)}"
    elif len(bear_hits) > len(bull_hits):
        score = (len(bear_hits) / total) * 100
        confidence = min(50 + (score - 50) * 0.7, 78)
        top = list(dict.fromkeys(bear_hits))[:3]
        return Signal.SELL, round(confidence, 1), f"Macro bearish: {', '.join(top)}"
    return Signal.HOLD, 50.0, "Mixed macro signals — no directional edge"


def _heuristic_score(headlines: list[str], asset: str) -> tuple[Signal, float, str]:
    """Generic fallback for non-index assets: simple keyword counting."""
    text = " ".join(headlines + [asset]).lower()
    bullish_hits = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
    bearish_hits = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
    total = bullish_hits + bearish_hits

    if total == 0:
        return Signal.HOLD, 50.0, "No significant sentiment keywords found in headlines"

    if bullish_hits > bearish_hits:
        score = (bullish_hits / total) * 100
        confidence = min(50 + (score - 50) * 0.7, 80)
        return Signal.BUY, round(confidence, 1), f"Bullish keywords: {bullish_hits}, bearish: {bearish_hits}"
    elif bearish_hits > bullish_hits:
        score = (bearish_hits / total) * 100
        confidence = min(50 + (score - 50) * 0.7, 80)
        return Signal.SELL, round(confidence, 1), f"Bearish keywords: {bearish_hits}, bullish: {bullish_hits}"
    return Signal.HOLD, 50.0, "Mixed sentiment — equal bullish/bearish signals"


def _parse_llm_response(raw: str) -> dict:
    """Extract JSON from potentially noisy LLM output (handles markdown fences)."""
    import json
    if not raw:
        raise ValueError("Empty LLM response")
    stripped = re.sub(r"```(?:json)?\s*", "", raw).strip()
    match = re.search(r'\{.*\}', stripped, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in LLM response")
    return json.loads(match.group())


class SentimentAgent(BaseAgent):
    name = AgentName.SENTIMENT

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-haiku-4-5-20251001",
        gemini_api_key: str = "",
        gemini_model: str = "gemini-2.5-flash",
        provider: str = "auto",
    ):
        self._client = build_llm_client(
            provider=provider,
            anthropic_api_key=api_key,
            gemini_api_key=gemini_api_key,
            anthropic_model=model,
            gemini_model=gemini_model,
        )

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
        is_index = ctx.asset.upper() in _INDEX_SYMBOLS

        if is_index:
            index_ctx = _INDEX_CONTEXT.get(ctx.asset.upper(), "")
            system_prompt = INDEX_SYSTEM_PROMPT.format(
                asset=ctx.asset, index_context=index_ctx
            )
            # Include options market data in user content for indices
            macro = ctx.macro_context or {}
            macro_lines = []
            if macro.get("pcr", -1) >= 0:
                macro_lines.append(f"PCR (Put-Call Ratio): {macro['pcr']:.2f}")
            if macro.get("atm_iv", 0) > 0:
                macro_lines.append(f"ATM IV: {macro['atm_iv']:.1f}%")
            if macro.get("iv_skew", 0) != 0:
                macro_lines.append(f"IV Skew (put-call): {macro['iv_skew']:+.1f}%")
            if macro.get("max_pain", 0) > 0:
                macro_lines.append(f"Max Pain: ₹{macro['max_pain']:.0f}")
            vix = macro.get("vix", 0)
            if vix > 0:
                macro_lines.append(f"India VIX: {vix:.1f}")

            user_content = f"""Asset: {ctx.asset}
Regime: {ctx.regime}
Options Market Data:
{chr(10).join(macro_lines) if macro_lines else "Not available"}

News Headlines:
{chr(10).join(f"- {h}" for h in headlines[:20])}

Social Sentiment: {sentiment_raw}
"""
        else:
            system_prompt = SYSTEM_PROMPT.format(asset=ctx.asset)
            user_content = f"""Asset: {ctx.asset}
Regime: {ctx.regime}
Macro context: {ctx.macro_context}

News Headlines:
{chr(10).join(f"- {h}" for h in headlines[:20])}

Social Sentiment Data: {sentiment_raw}
"""

        try:
            raw_text = await self._client.generate(
                system_prompt=system_prompt,
                user_content=user_content,
                max_tokens=512,
                temperature=0.1,
            )
            parsed = _parse_llm_response(raw_text)

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
                    "index_mode": is_index,
                },
                warnings=risk_flags,
            )

        except Exception as e:
            # Graceful fallback on LLM error
            if is_index:
                signal, confidence, reasoning = _index_heuristic_score(headlines, ctx.asset)
            else:
                signal, confidence, reasoning = _heuristic_score(headlines, ctx.asset)
            return AgentDecision(
                agent_name=self.name,
                signal=signal,
                confidence=confidence * 0.8,
                reasoning=f"LLM failed ({e}), heuristic fallback: {reasoning}",
                warnings=["llm_fallback"],
            )

    def _heuristic_analyze(self, ctx: MarketContext, headlines: list[str]) -> AgentDecision:
        is_index = ctx.asset.upper() in _INDEX_SYMBOLS
        if is_index:
            signal, confidence, reasoning = _index_heuristic_score(headlines, ctx.asset)
        else:
            signal, confidence, reasoning = _heuristic_score(headlines, ctx.asset)
        return AgentDecision(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            indicators={"headline_count": len(headlines), "index_mode": is_index},
            warnings=["heuristic_mode"],
        )
