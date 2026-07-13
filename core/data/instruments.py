"""
Dhan scrip master — dynamic instrument lookup.

Downloads Dhan's api-scrip-master.csv once per day and provides O(1)
resolution of any NSE equity, index, or MCX commodity to its
(security_id, exchange_segment, instrument_type, lot_size).

For MCX commodities the near-month (earliest future expiry) contract
is selected automatically; no manual monthly maintenance required.

Usage:
    from core.data.instruments import scrip_master

    # At app startup:
    await scrip_master.ensure_loaded()

    # Resolve any symbol:
    inst = scrip_master.resolve("NATURALGAS")   # → MCX near-month
    inst = scrip_master.resolve("RELIANCE")     # → NSE equity
    inst = scrip_master.resolve("NIFTY")        # → IDX_I index
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
_REFRESH_INTERVAL = 86_400  # 24 h — refresh once per day

# Stable index security IDs — these never change so we don't need the CSV for them
_INDEX_META: dict[str, tuple[str, str]] = {
    "NIFTY":      ("13",  "Nifty 50"),
    "NIFTY50":    ("13",  "Nifty 50"),
    "BANKNIFTY":  ("25",  "Bank Nifty"),
    "FINNIFTY":   ("27",  "Fin Nifty"),
    "NIFTYNXT50": ("26",  "Nifty Next 50"),
    "MIDCPNIFTY": ("442", "Midcap Nifty"),
}


@dataclass
class Instrument:
    security_id: str
    exchange: str          # e.g. NSE_EQ, IDX_I, MCX_COMM
    instrument_type: str   # EQUITY, INDEX, FUTCOM, …
    trading_symbol: str    # full Dhan trading symbol (MCX: "NATURALGAS-16Jul2026-FUTCOM")
    display_name: str = ""
    expiry: str = ""       # ISO date string, empty for equities/indices
    lot_size: int = 1

    def to_pair_dict(self, user_symbol: str, asset_type: str) -> dict:
        return {
            "symbol":         user_symbol.upper(),
            "name":           self.display_name or user_symbol.upper(),
            "exchange":       self.exchange,
            "type":           asset_type,
            "security_id":    self.security_id,
            "trading_symbol": self.trading_symbol,
            "lot_size":       self.lot_size,
            "expiry":         self.expiry,
            "data_source":    "",
        }


class ScripMaster:
    """
    Thread-safe singleton that owns Dhan's scrip master data.
    All heavy I/O happens in ensure_loaded(); lookups are synchronous
    so they can be called from both async and sync contexts.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_fetch: float = 0.0

        # Pre-populate indices (stable — no CSV needed)
        self._indices: dict[str, Instrument] = {
            sym: Instrument(
                security_id=sid,
                exchange="IDX_I",
                instrument_type="INDEX",
                trading_symbol=sym,
                display_name=name,
            )
            for sym, (sid, name) in _INDEX_META.items()
        }
        self._equities: dict[str, Instrument] = {}
        # commodity.upper() → list of futures sorted by expiry (nearest first)
        self._mcx: dict[str, list[Instrument]] = {}

    # ── Loading ───────────────────────────────────────────────────────────────

    async def ensure_loaded(self) -> None:
        """Download and parse the scrip master if the cache is stale or empty."""
        if self._equities and time.time() - self._last_fetch < _REFRESH_INTERVAL:
            return
        async with self._lock:
            if self._equities and time.time() - self._last_fetch < _REFRESH_INTERVAL:
                return
            await self._fetch_and_parse()

    async def _fetch_and_parse(self) -> None:
        log.info("Downloading Dhan scrip master …")
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(_SCRIP_MASTER_URL)
                resp.raise_for_status()
                text = resp.text
        except Exception as exc:
            if self._equities:
                log.warning("Scrip master refresh failed — using cached data (%s)", exc)
                return
            log.error("Scrip master download failed and cache is empty: %s", exc)
            raise

        equities: dict[str, Instrument] = {}
        mcx: dict[str, list[Instrument]] = {}

        for raw in csv.DictReader(io.StringIO(text)):
            row: dict[str, str] = {k.strip(): (v.strip() if v else "") for k, v in raw.items()}

            exch  = row.get("SEM_EXM_EXCH_ID", "")
            itype = row.get("SEM_INSTRUMENT_NAME", "")
            sid   = row.get("SEM_SMST_SECURITY_ID", "")
            sym   = row.get("SEM_TRADING_SYMBOL", "")
            cname = row.get("SEM_CUSTOM_SYMBOL", "") or sym
            exp   = row.get("SEM_EXPIRY_DATE", "")
            try:
                lot = max(1, int(float(row.get("SEM_LOT_UNITS", "1") or 1)))
            except (ValueError, TypeError):
                lot = 1

            if not sid or not sym:
                continue

            if exch == "NSE" and itype == "EQUITY":
                equities[sym.upper()] = Instrument(
                    security_id=sid,
                    exchange="NSE_EQ",
                    instrument_type="EQUITY",
                    trading_symbol=sym,
                    display_name=cname,
                    lot_size=lot,
                )

            elif exch == "MCX" and itype == "FUTCOM":
                commodity = (row.get("SM_SYMBOL_NAME", "") or sym.split("-")[0]).upper()
                mcx.setdefault(commodity, []).append(Instrument(
                    security_id=sid,
                    exchange="MCX_COMM",
                    instrument_type="FUTCOM",
                    trading_symbol=sym,
                    display_name=cname or sym,
                    expiry=exp,
                    lot_size=lot,
                ))

        # Sort MCX lists by expiry (nearest first)
        for lst in mcx.values():
            lst.sort(key=lambda i: i.expiry or "9999-12-31")

        self._equities = equities
        self._mcx = mcx
        self._last_fetch = time.time()
        log.info(
            "Scrip master ready: %d NSE equities, %d MCX commodities",
            len(equities), len(mcx),
        )

    # ── Resolution API ────────────────────────────────────────────────────────

    def resolve(self, symbol: str) -> Optional[Instrument]:
        """
        Resolve a symbol to its Instrument.
        Priority: index → MCX near-month → NSE equity.
        Returns None if unknown.
        """
        upper = symbol.upper()
        if upper in self._indices:
            return self._indices[upper]
        if upper in self._mcx:
            return self._near_month(upper)
        return self._equities.get(upper)

    def resolve_mcx(self, commodity: str) -> Optional[Instrument]:
        """Return the near-month MCX futures contract for a commodity name."""
        return self._near_month(commodity.upper())

    def _near_month(self, commodity: str) -> Optional[Instrument]:
        futures = self._mcx.get(commodity, [])
        if not futures:
            return None
        today = date.today().isoformat()
        for f in futures:
            if f.expiry >= today:
                return f
        return futures[-1]  # all expired — return latest anyway

    def is_mcx(self, symbol: str) -> bool:
        return symbol.upper() in self._mcx

    def is_index(self, symbol: str) -> bool:
        return symbol.upper() in self._indices

    def mcx_commodities(self) -> list[str]:
        return list(self._mcx.keys())

    # ── UI / API helpers ──────────────────────────────────────────────────────

    def watchlist_pairs(self, symbols: list[str]) -> list[dict]:
        """
        Convert a list of user-facing symbols (from LIVE_SUGGESTIONS_ASSETS)
        into pair dicts suitable for /pairs/suggest.
        """
        out = []
        for sym in symbols:
            upper = sym.upper()
            inst = self.resolve(upper)
            if inst:
                ptype = ("index" if self.is_index(upper) else
                         "commodity" if self.is_mcx(upper) else "equity")
                out.append(inst.to_pair_dict(upper, ptype))
            else:
                out.append({
                    "symbol": upper, "name": upper,
                    "exchange": "", "type": "unknown",
                    "security_id": "", "trading_symbol": upper,
                    "lot_size": 1, "expiry": "", "data_source": "",
                })
        return out

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across indices, MCX, and NSE equities."""
        q = query.upper().strip()
        if not q:
            return []
        results: list[dict] = []
        for sym, inst in self._indices.items():
            if q in sym or q in inst.display_name.upper():
                results.append(inst.to_pair_dict(sym, "index"))
        for commodity in sorted(self._mcx):
            if q in commodity:
                inst = self._near_month(commodity)
                if inst:
                    results.append(inst.to_pair_dict(commodity, "commodity"))
        for sym, inst in self._equities.items():
            if q in sym or q in inst.display_name.upper():
                results.append(inst.to_pair_dict(sym, "equity"))
        return results[:limit]


# Module-level singleton — shared by broker_interface and market_data
scrip_master = ScripMaster()
