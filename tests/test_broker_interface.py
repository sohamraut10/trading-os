"""
Tests for AlpacaBroker's guard against a missing alpaca-trade-api install.

alpaca-trade-api is intentionally not a hard dependency (see requirements.txt
— it hard-conflicts with google-genai's websockets range), so this path is
expected to be hit in most environments. It must fail with a clear error
that api/main.py's _build_broker() can catch and fall back on, not a raw
ImportError several frames deep.
"""
from unittest.mock import patch

import pytest

from core.execution.broker_interface import AlpacaBroker


def test_alpaca_broker_raises_clear_error_when_sdk_missing():
    with patch("core.execution.broker_interface._ALPACA_AVAILABLE", False):
        with pytest.raises(RuntimeError, match="alpaca-trade-api is not installed"):
            AlpacaBroker(api_key="key", secret_key="secret", base_url="https://paper-api.alpaca.markets")
