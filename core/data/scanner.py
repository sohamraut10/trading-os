"""
Market-wide rotating scanner.

Builds a full tradeable universe from the scrip master (F&O equities + MCX
commodities) and hands out batches in round-robin order so every instrument
gets analysed over time without any hardcoded symbol list.
"""
from __future__ import annotations

import logging

from core.data.instruments import scrip_master

log = logging.getLogger(__name__)


class MarketScanner:
    def __init__(self) -> None:
        self._universe: list[str] = []
        self._pointer: int = 0

    def refresh(self) -> None:
        """Rebuild universe from the loaded scrip master. Call after ensure_loaded()."""
        self._universe = scrip_master.tradeable_universe()
        self._pointer = 0
        log.info(
            "MarketScanner universe: %d instruments (%d MCX + %d F&O equities)",
            len(self._universe),
            len(scrip_master.mcx_commodities()),
            len(scrip_master.fno_symbols()),
        )

    def next_batch(self, size: int) -> list[str]:
        """Return the next `size` symbols in rotation, wrapping around."""
        if not self._universe:
            return []
        n = len(self._universe)
        batch = [self._universe[(self._pointer + i) % n] for i in range(size)]
        self._pointer = (self._pointer + size) % n
        return batch

    @property
    def universe_size(self) -> int:
        return len(self._universe)

    @property
    def pointer(self) -> int:
        return self._pointer


market_scanner = MarketScanner()
