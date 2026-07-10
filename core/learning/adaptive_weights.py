"""
Learning Loop — Adaptive Agent Weight System
Adjusts agent weights over time based on their historical prediction accuracy.
Implements exponential moving average of per-agent accuracy so recent performance
weighs more than historical.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentPerformanceRecord:
    agent_name: str
    predictions: list[dict] = field(default_factory=list)
    # Each prediction: {"signal": "BUY", "confidence": 82, "outcome": 1 or -1, "timestamp": ...}

    @property
    def accuracy(self) -> float:
        if not self.predictions:
            return 0.5
        correct = sum(1 for p in self.predictions if _was_correct(p))
        return correct / len(self.predictions)

    @property
    def weighted_accuracy(self) -> float:
        """EMA of accuracy — recent predictions weigh more."""
        if not self.predictions:
            return 0.5
        alpha = 0.1
        ema = 0.5
        for p in sorted(self.predictions, key=lambda x: x["timestamp"]):
            correct = 1.0 if _was_correct(p) else 0.0
            ema = alpha * correct + (1 - alpha) * ema
        return ema

    @property
    def confidence_calibration(self) -> float:
        """How well-calibrated is the agent's confidence vs actual accuracy?"""
        if len(self.predictions) < 10:
            return 1.0
        bins = {}
        for p in self.predictions:
            bucket = int(p["confidence"] // 10) * 10  # 50, 60, 70, 80, 90
            if bucket not in bins:
                bins[bucket] = []
            bins[bucket].append(1 if _was_correct(p) else 0)
        calibration_error = 0.0
        n = 0
        for bucket, outcomes in bins.items():
            expected_acc = bucket / 100
            actual_acc = sum(outcomes) / len(outcomes)
            calibration_error += abs(expected_acc - actual_acc) * len(outcomes)
            n += len(outcomes)
        # 0 = perfectly calibrated, 1 = worst case
        return 1.0 - (calibration_error / n if n > 0 else 0.5)


def _was_correct(prediction: dict) -> bool:
    signal = prediction.get("signal")
    outcome = prediction.get("outcome", 0)  # 1 = price went up, -1 = went down
    if signal == "BUY":
        return outcome > 0
    elif signal == "SELL":
        return outcome < 0
    return False  # HOLD predictions not scored


class AdaptiveWeightManager:
    """
    Manages per-agent performance tracking and dynamic weight computation.
    Weights are recomputed after every N resolved trades.
    """

    BASE_WEIGHTS = {
        "Technical": 0.30,
        "Sentiment": 0.20,
        "Quant": 0.25,
        "OrderFlow": 0.25,
    }
    RECALIBRATION_INTERVAL = 20    # recalibrate after every 20 new outcomes
    MIN_WEIGHT = 0.10
    MAX_WEIGHT = 0.45

    def __init__(self, persistence_path: str = "/tmp/agent_performance.json"):
        self._path = Path(persistence_path)
        self._records: dict[str, AgentPerformanceRecord] = {
            name: AgentPerformanceRecord(agent_name=name)
            for name in self.BASE_WEIGHTS
        }
        self._current_weights = dict(self.BASE_WEIGHTS)
        self._trade_count = 0
        self._load()

    def record_prediction(
        self,
        agent_name: str,
        signal: str,
        confidence: float,
        trade_id: str,
    ) -> None:
        if agent_name not in self._records:
            return
        self._records[agent_name].predictions.append({
            "signal": signal,
            "confidence": confidence,
            "trade_id": trade_id,
            "timestamp": time.time(),
            "outcome": None,   # filled in by resolve_trade
        })

    def resolve_trade(self, trade_id: str, price_return_pct: float) -> None:
        """
        Called after a trade closes. Updates all agents that predicted this trade.
        price_return_pct > 0 = price went up, < 0 = went down.
        """
        outcome = 1 if price_return_pct > 0 else -1
        for record in self._records.values():
            for pred in record.predictions:
                if pred.get("trade_id") == trade_id:
                    pred["outcome"] = outcome
                    pred["price_return"] = price_return_pct

        self._trade_count += 1
        if self._trade_count % self.RECALIBRATION_INTERVAL == 0:
            self._recalibrate_weights()
            self._save()

    def get_weights(self) -> dict[str, float]:
        return dict(self._current_weights)

    def get_performance_report(self) -> dict:
        return {
            name: {
                "accuracy": round(rec.weighted_accuracy, 3),
                "calibration": round(rec.confidence_calibration, 3),
                "predictions": len(rec.predictions),
                "weight": round(self._current_weights.get(name, 0), 3),
            }
            for name, rec in self._records.items()
        }

    def _recalibrate_weights(self) -> None:
        """
        New weight ∝ weighted_accuracy × calibration_score.
        Normalized to sum to 1. Clamped to [MIN_WEIGHT, MAX_WEIGHT].
        """
        scores = {}
        for name, rec in self._records.items():
            if len(rec.predictions) < 5:
                scores[name] = self.BASE_WEIGHTS.get(name, 0.25)
            else:
                scores[name] = rec.weighted_accuracy * rec.confidence_calibration

        total = sum(scores.values())
        if total == 0:
            return

        for name in self._current_weights:
            raw = scores.get(name, 0.25) / total
            self._current_weights[name] = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, raw))

        # Re-normalize after clamping
        total_after = sum(self._current_weights.values())
        for name in self._current_weights:
            self._current_weights[name] /= total_after

    def _save(self) -> None:
        try:
            data = {
                "weights": self._current_weights,
                "records": {
                    name: rec.predictions[-500:]   # keep last 500 predictions
                    for name, rec in self._records.items()
                },
                "trade_count": self._trade_count,
            }
            self._path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._current_weights = data.get("weights", self.BASE_WEIGHTS)
            self._trade_count = data.get("trade_count", 0)
            for name, preds in data.get("records", {}).items():
                if name in self._records:
                    self._records[name].predictions = preds
        except Exception:
            pass
