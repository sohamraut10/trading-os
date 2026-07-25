import pandas as pd
from datetime import datetime

class FutureDataLeakageError(Exception):
    pass

class HistoricalDataProvider:
    """
    Deterministically replaces live data feeds (WebSocket/REST).
    Guarantees no look-ahead bias by enforcing the current_replay_timestamp.
    """
    def __init__(self, historical_candles: pd.DataFrame):
        # Assumes dataframe has a datetime index or 'ts' column sorted chronologically
        self.df = historical_candles
        if 'ts' not in self.df.columns:
            self.df['ts'] = self.df.index
            
        self.current_replay_timestamp = None
        
    def set_time(self, ts: datetime):
        self.current_replay_timestamp = ts
        
    def get_latest_bar(self, symbol: str):
        """
        Returns the most recent bar strictly <= current_replay_timestamp.
        """
        if not self.current_replay_timestamp:
            raise RuntimeError("Provider time not set. Call set_time() before querying.")
            
        valid_bars = self.df[(self.df['symbol'] == symbol) & (self.df['ts'] <= self.current_replay_timestamp)]
        if valid_bars.empty:
            return None
        return valid_bars.iloc[-1].to_dict()
        
    def get_history(self, symbol: str, limit: int = 100):
        """
        Returns N past bars strictly <= current_replay_timestamp.
        """
        if not self.current_replay_timestamp:
            raise RuntimeError("Provider time not set. Call set_time() before querying.")
            
        valid_bars = self.df[(self.df['symbol'] == symbol) & (self.df['ts'] <= self.current_replay_timestamp)]
        
        # Enforce anti-leakage check
        if not valid_bars.empty and valid_bars['ts'].max() > self.current_replay_timestamp:
            raise FutureDataLeakageError(f"Leakage detected! Returned timestamp {valid_bars['ts'].max()} > current {self.current_replay_timestamp}")
            
        return valid_bars.tail(limit).to_dict(orient='records')
