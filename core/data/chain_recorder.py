import os
import time
import datetime
from sqlalchemy import create_engine, text
# In a real system, you'd import the DhanHQ python client:
# from dhanhq import dhanhq

DB_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/omega")

def fetch_live_chain(symbol: str):
    """
    Simulates fetching the live options chain from Dhan HQ API.
    In production, this translates to dhan.get_option_chain(...)
    """
    # Mocking for the architecture slice
    ts = datetime.datetime.now(datetime.timezone.utc)
    
    # Fake spot
    spot = 24500.00
    
    # Fake chain entries
    chain_data = []
    for strike in range(24000, 25000, 100):
        chain_data.append({
            'ts': ts,
            'symbol': symbol,
            'expiry': datetime.date(2026, 7, 30),
            'strike': strike,
            'opt_type': 'CE',
            'ltp': max(0.05, spot - strike + 50),
            'bid': max(0.05, spot - strike + 49),
            'ask': max(0.05, spot - strike + 51),
            'iv': 0.15,
            'oi': 100000,
            'volume': 50000,
            'spot': spot
        })
        chain_data.append({
            'ts': ts,
            'symbol': symbol,
            'expiry': datetime.date(2026, 7, 30),
            'strike': strike,
            'opt_type': 'PE',
            'ltp': max(0.05, strike - spot + 50),
            'bid': max(0.05, strike - spot + 49),
            'ask': max(0.05, strike - spot + 51),
            'iv': 0.16,
            'oi': 120000,
            'volume': 60000,
            'spot': spot
        })
    return chain_data

def run_recorder(interval_seconds=60):
    """
    Main loop to snapshot the live chain every X seconds.
    """
    print(f"Starting Chain Recorder. Interval: {interval_seconds}s")
    engine = create_engine(DB_URL)
    
    symbols_to_track = ["NIFTY", "BANKNIFTY"]
    
    while True:
        try:
            for symbol in symbols_to_track:
                # 1. Fetch live chain
                chain_data = fetch_live_chain(symbol)
                
                # 2. Bulk insert into Postgres
                with engine.begin() as conn:
                    for row in chain_data:
                        conn.execute(text("""
                            INSERT INTO options_intraday 
                            (ts, symbol, expiry, strike, opt_type, ltp, bid, ask, iv, oi, volume, spot)
                            VALUES (:ts, :symbol, :expiry, :strike, :opt_type, :ltp, :bid, :ask, :iv, :oi, :volume, :spot)
                        """), row)
                
            print(f"[{datetime.datetime.now()}] Snapshotted {len(chain_data) * len(symbols_to_track)} options.")
            
        except Exception as e:
            print(f"Error during snapshot: {e}")
            
        time.sleep(interval_seconds)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60, help="Snapshot interval in seconds")
    args = parser.parse_args()
    
    # In a real setup, we'd check if market is open (09:15 to 15:30 IST)
    # before running the loop.
    run_recorder(args.interval)
