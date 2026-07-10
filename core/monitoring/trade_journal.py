"""
AI Trade Journal
Generates human-readable post-trade analysis using Claude.
Logs every signal decision for full auditability.
"""
import json
import time
from pathlib import Path
from dataclasses import dataclass, field

try:
    import anthropic
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

from core.agents.meta_agent import TradeSignal


@dataclass
class TradeJournalEntry:
    trade_id: str
    asset: str
    action: str | None
    confidence: float
    regime: str
    signal: dict
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    hold_duration_min: float = 0.0
    ai_analysis: str = ""
    timestamp: float = field(default_factory=time.time)


JOURNAL_PROMPT = """You are an elite trading journal AI. Write a concise post-trade analysis.

Trade Details:
{trade_json}

Write a 3-paragraph structured analysis:
1. SIGNAL QUALITY: What drove the consensus? Were agents aligned or conflicted?
2. EXECUTION: Was this the right time? Any regime or risk concerns?
3. LESSON: One key insight for future trades of this type.

Be direct, data-driven, and under 200 words total."""


class TradeJournal:
    def __init__(
        self,
        api_key: str = "",
        journal_path: str = "trade_journal.jsonl",
        model: str = "claude-haiku-4-5-20251001",
    ):
        self._client = anthropic.Anthropic(api_key=api_key) if _AVAILABLE and api_key else None
        self._model = model
        self._path = Path(journal_path)

    async def log_signal(
        self, signal: TradeSignal, entry_price: float = 0.0
    ) -> TradeJournalEntry:
        entry = TradeJournalEntry(
            trade_id=signal.request_id,
            asset=signal.asset,
            action=signal.action.value if signal.action else None,
            confidence=signal.confidence,
            regime=signal.regime,
            signal=signal.to_dict(),
            entry_price=entry_price,
        )

        if self._client and signal.final_decision:
            entry.ai_analysis = await self._generate_analysis(entry)

        self._persist(entry)
        return entry

    async def update_outcome(
        self, trade_id: str, exit_price: float, pnl_pct: float, hold_min: float
    ) -> None:
        # Load and update the entry
        entries = self._load_all()
        for e in entries:
            if e.get("trade_id") == trade_id:
                e["exit_price"] = exit_price
                e["pnl_pct"] = pnl_pct
                e["hold_duration_min"] = hold_min
        self._overwrite_all(entries)

    async def _generate_analysis(self, entry: TradeJournalEntry) -> str:
        if not self._client:
            return ""
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=self._model,
                    max_tokens=400,
                    messages=[{
                        "role": "user",
                        "content": JOURNAL_PROMPT.format(
                            trade_json=json.dumps(entry.signal, indent=2)
                        ),
                    }],
                )
            )
            return response.content[0].text
        except Exception as e:
            return f"Analysis unavailable: {e}"

    def _persist(self, entry: TradeJournalEntry) -> None:
        with open(self._path, "a") as f:
            f.write(json.dumps(entry.__dict__) + "\n")

    def _load_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        entries = []
        with open(self._path) as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries

    def _overwrite_all(self, entries: list[dict]) -> None:
        with open(self._path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
