"""
core.options — Indian Options Strategy Module.

Public API surface for external consumers (Orchestrator, ConsensusEngine, etc.).
"""

from core.options.chain_analyzer import ChainSummary, OIActivity, OptionChainAnalyzer, StrikeData
from core.options.entry_engine import ConfirmationResult, EntryDecision, EntryEngine
from core.options.exit_engine import ExitEngine, ExitReason, ExitSignal, OptionPosition
from core.options.expiry_engine import ExpiryEngine, ExpiryInfo
from core.options.greeks_engine import Greeks, GreeksEngine, PortfolioGreeks
from core.options.position_manager import PositionManager, PositionSizeResult
from core.options.regime_classifier import OptionsRegime, OptionsRegimeClassifier
from core.options.signal_generator import OptionsAnalysisAgent
from core.options.strategy_manager import StrategyManager, StrategySpec, StrategyType
from core.options.volatility_engine import VolatilityEngine, VolatilitySnapshot

__all__ = [
    # Chain
    "ChainSummary", "OIActivity", "OptionChainAnalyzer", "StrikeData",
    # Entry
    "ConfirmationResult", "EntryDecision", "EntryEngine",
    # Exit
    "ExitEngine", "ExitReason", "ExitSignal", "OptionPosition",
    # Expiry
    "ExpiryEngine", "ExpiryInfo",
    # Greeks
    "Greeks", "GreeksEngine", "PortfolioGreeks",
    # Position
    "PositionManager", "PositionSizeResult",
    # Regime
    "OptionsRegime", "OptionsRegimeClassifier",
    # Signal (main agent)
    "OptionsAnalysisAgent",
    # Strategy
    "StrategyManager", "StrategySpec", "StrategyType",
    # Volatility
    "VolatilityEngine", "VolatilitySnapshot",
]
