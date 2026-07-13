"""
Prometheus metrics for Trading OS.
Exposes /metrics in OpenMetrics text format.
Tracks: cycle latency, signal rate, agent confidence, circuit breaker state, P&L.
"""
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Gauge:
    name: str
    help: str
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)

    def set(self, v: float) -> None:
        self.value = v

    def render(self) -> str:
        label_str = self._label_str()
        return (
            f"# HELP {self.name} {self.help}\n"
            f"# TYPE {self.name} gauge\n"
            f"{self.name}{label_str} {self.value}\n"
        )

    def _label_str(self) -> str:
        if not self.labels:
            return ""
        parts = ",".join(f'{k}="{v}"' for k, v in self.labels.items())
        return "{" + parts + "}"


@dataclass
class Counter:
    name: str
    help: str
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)

    def inc(self, v: float = 1.0) -> None:
        self.value += v

    def render(self) -> str:
        label_str = ""
        if self.labels:
            parts = ",".join(f'{k}="{v}"' for k, v in self.labels.items())
            label_str = "{" + parts + "}"
        return (
            f"# HELP {self.name} {self.help}\n"
            f"# TYPE {self.name} counter\n"
            f"{self.name}_total{label_str} {self.value}\n"
        )


@dataclass
class Histogram:
    """Simplified histogram with fixed buckets."""
    name: str
    help: str
    buckets: list[float] = field(default_factory=lambda: [5, 10, 25, 50, 100, 250, 500, 1000])
    _counts: list[int] = field(default_factory=list, init=False, repr=False)
    _sum: float = field(default=0.0, init=False)
    _total: int = field(default=0, init=False)

    def __post_init__(self):
        self._counts = [0] * len(self.buckets)

    def observe(self, v: float) -> None:
        self._sum += v
        self._total += 1
        for i, b in enumerate(self.buckets):
            if v <= b:
                self._counts[i] += 1

    def render(self) -> str:
        lines = [
            f"# HELP {self.name} {self.help}",
            f"# TYPE {self.name} histogram",
        ]
        cumulative = 0
        for b, c in zip(self.buckets, self._counts):
            cumulative += c
            lines.append(f'{self.name}_bucket{{le="{b}"}} {cumulative}')
        lines.append(f'{self.name}_bucket{{le="+Inf"}} {self._total}')
        lines.append(f"{self.name}_sum {self._sum}")
        lines.append(f"{self.name}_count {self._total}")
        return "\n".join(lines) + "\n"


class TradingMetrics:
    """
    Central registry for all Trading OS Prometheus metrics.
    Call update_* methods from the orchestrator after each cycle.
    """

    def __init__(self):
        # Cycle latency histogram (ms)
        self.cycle_latency = Histogram(
            "trading_cycle_latency_ms",
            "End-to-end analysis cycle duration in milliseconds",
            buckets=[10, 25, 50, 100, 200, 500, 1000, 2000],
        )

        # Signal counters
        self.signals_total = Counter("trading_signals", "Total signals generated")
        self.true_signals  = Counter("trading_true_signals", "TRUE (execute) signals")
        self.false_signals = Counter("trading_false_signals", "FALSE (reject) signals")
        self.da_vetoes     = Counter("trading_da_vetoes", "Devil's Advocate vetoes")

        # Per-agent confidence gauges (one per agent)
        self._agent_confidence: dict[str, Gauge] = {}
        for agent in ("Technical", "Sentiment", "Quant", "OrderFlow", "DevilsAdvocate"):
            self._agent_confidence[agent] = Gauge(
                "trading_agent_confidence",
                "Last agent confidence score 0-100",
                labels={"agent": agent},
            )

        # Per-asset gauges
        self._asset_price: dict[str, Gauge] = {}
        self._asset_confidence: dict[str, Gauge] = {}

        # Risk / portfolio gauges
        self.portfolio_equity    = Gauge("trading_portfolio_equity_usd", "Current portfolio equity in USD")
        self.portfolio_exposure  = Gauge("trading_portfolio_exposure_pct", "Portfolio exposure as fraction 0-1")
        self.daily_pnl_pct       = Gauge("trading_daily_pnl_pct", "Today's P&L percentage")
        self.open_trades         = Gauge("trading_open_trades", "Number of currently open trades")
        self.circuit_breaker     = Gauge("trading_circuit_breaker_active", "1 if circuit breaker is active")
        self.consecutive_losses  = Gauge("trading_consecutive_losses", "Consecutive losing trades")

        # Regime gauge (mapped to int: bull=1, bear=-1, sideways=0, volatile=2)
        self.regime = Gauge("trading_regime", "Current market regime encoded as integer")

        # Host system resource gauges (populated via update_system)
        self.cpu_pct   = Gauge("trading_host_cpu_pct",      "Host CPU usage percent")
        self.ram_pct   = Gauge("trading_host_ram_pct",      "Host RAM usage percent")
        self.disk_pct  = Gauge("trading_host_disk_pct",     "Host disk usage percent")
        self.ram_avail = Gauge("trading_host_ram_avail_gb", "Host RAM available in GB")
        self.ram_total = Gauge("trading_host_ram_total_gb", "Host RAM total in GB")

        self._start_time = time.time()

    def update_cycle(self, asset: str, cycle_ms: float, signal_dict: dict) -> None:
        self.cycle_latency.observe(cycle_ms)
        self.signals_total.inc()

        if signal_dict.get("final_decision") == "TRUE SIGNAL":
            self.true_signals.inc()
        else:
            self.false_signals.inc()

        if signal_dict.get("override_reason") and "DA veto" in str(signal_dict.get("override_reason", "")):
            self.da_vetoes.inc()

        conf = signal_dict.get("confidence", 0)
        self._asset_confidence.setdefault(
            asset, Gauge("trading_asset_confidence", "Latest consensus confidence", labels={"asset": asset})
        ).set(conf)

        regime_map = {"bull": 1, "bear": -1, "sideways": 0, "volatile": 2}
        self.regime.set(regime_map.get(signal_dict.get("regime", ""), 0))

        for agent in signal_dict.get("agents", []):
            name = agent.get("name", "")
            if name in self._agent_confidence:
                self._agent_confidence[name].set(agent.get("confidence", 0))

    def update_system(self) -> None:
        try:
            import psutil
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=None)
            disk = psutil.disk_usage("/")
            self.cpu_pct.set(cpu)
            self.ram_pct.set(mem.percent)
            self.disk_pct.set(disk.percent)
            self.ram_avail.set((mem.total - mem.used) / 1e9)
            self.ram_total.set(mem.total / 1e9)
        except Exception:
            pass

    def update_portfolio(self, equity: float, exposure: float, daily_pnl: float,
                         open_trades: int, consecutive_losses: int, circuit_breaker: bool) -> None:
        self.portfolio_equity.set(equity)
        self.portfolio_exposure.set(exposure)
        self.daily_pnl_pct.set(daily_pnl)
        self.open_trades.set(open_trades)
        self.consecutive_losses.set(consecutive_losses)
        self.circuit_breaker.set(1.0 if circuit_breaker else 0.0)

    def render(self) -> str:
        """Returns the full /metrics payload in Prometheus text format."""
        parts = [
            f"# Trading OS metrics — uptime {time.time() - self._start_time:.0f}s\n",
            self.cycle_latency.render(),
            self.signals_total.render(),
            self.true_signals.render(),
            self.false_signals.render(),
            self.da_vetoes.render(),
            self.portfolio_equity.render(),
            self.portfolio_exposure.render(),
            self.daily_pnl_pct.render(),
            self.open_trades.render(),
            self.consecutive_losses.render(),
            self.circuit_breaker.render(),
            self.regime.render(),
        ]
        for g in self._agent_confidence.values():
            parts.append(g.render())
        for g in self._asset_confidence.values():
            parts.append(g.render())
        for g in (self.cpu_pct, self.ram_pct, self.disk_pct, self.ram_avail, self.ram_total):
            parts.append(g.render())
        return "".join(parts)


# Singleton — imported by API and orchestrator
metrics = TradingMetrics()
