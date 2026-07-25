import os
import json
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime

# Assume a data fetcher exists in the real system
# from core.data.market_data import fetch_candles

DB_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/omega")

def fetch_historical_candles_mock(symbol: str, start_ts: datetime, end_ts: datetime):
    """
    Mock function representing the fetching of market data to verify the outcome.
    """
    # Mocking a sequence of prices where the asset went up slightly then hit the profit target
    dates = pd.date_range(start_ts, end_ts, freq='15min')
    df = pd.DataFrame({
        'ts': dates,
        'symbol': symbol,
        'high': [25000 + i*10 for i in range(len(dates))],
        'low': [24950 + i*5 for i in range(len(dates))],
        'close': [24980 + i*8 for i in range(len(dates))]
    })
    return df

def score_suggestion(suggestion: dict, candles: pd.DataFrame) -> dict:
    """
    Deterministically scores a suggestion against actual subsequent price action.
    suggestion must have: entry_price, target_price (T1), stop_loss.
    """
    side = 'long' if suggestion['target_price'] > suggestion['entry_price'] else 'short'
    
    tp = suggestion['target_price']
    sl = suggestion['stop_loss']
    
    outcome = 'EXPIRED_FLAT'
    realized_r = 0.0
    
    # Intended risk
    risk = abs(suggestion['entry_price'] - sl)
    if risk == 0: risk = 1.0 # avoid div/0
    
    for _, bar in candles.iterrows():
        if side == 'long':
            if bar['low'] <= sl:
                outcome = 'HIT_SL'
                realized_r = -1.0
                break
            if bar['high'] >= tp:
                outcome = 'HIT_T1'
                realized_r = abs(tp - suggestion['entry_price']) / risk
                break
        else:
            if bar['high'] >= sl:
                outcome = 'HIT_SL'
                realized_r = -1.0
                break
            if bar['low'] <= tp:
                outcome = 'HIT_T1'
                realized_r = abs(suggestion['entry_price'] - tp) / risk
                break
                
    return {
        'outcome': outcome,
        'realized_r': realized_r
    }

def run_nightly_scorer():
    """
    Nightly job: Scans the DB for /charts suggestions whose horizon has passed and scores them.
    """
    print(f"[{datetime.now()}] Starting Nightly Analyst Scorer...")
    engine = create_engine(DB_URL)
    
    scored_count = 0
    
    with engine.begin() as conn:
        # 1. Fetch pending suggestions (where outcome is not yet scored and horizon < NOW)
        # Using a mock query for the architecture demo
        results = conn.execute(text("""
            SELECT id, asset, created_at, payload, confidence 
            FROM signals 
            WHERE strategy = 'chart_analyst' 
              AND action IS NULL 
              -- AND horizon_ts < NOW() (simplified)
            LIMIT 100
        """)).mappings().all()
        
        for row in results:
            payload = row['payload'] # dict assuming JSONB
            if 'target_price' not in payload or 'stop_loss' not in payload:
                continue
                
            # 2. Fetch subsequent price action
            start_ts = row['created_at']
            # Assume a fixed 2-day horizon for the demo
            end_ts = start_ts + pd.Timedelta(days=2) 
            
            candles = fetch_historical_candles_mock(row['asset'], start_ts, end_ts)
            
            # 3. Deterministically score
            score = score_suggestion(payload, candles)
            
            # 4. Write outcome back
            conn.execute(text("""
                UPDATE signals 
                SET action = :outcome, reason = :realized_r
                WHERE id = :id
            """), {
                "outcome": score['outcome'], 
                "realized_r": str(score['realized_r']),
                "id": row['id']
            })
            
            scored_count += 1
            
        # 5. Generate Calibration Curve Report (Confidence vs Win Rate)
        stats = conn.execute(text("""
            SELECT 
                FLOOR(confidence / 10.0) * 10 AS conf_bucket,
                COUNT(*) as total,
                SUM(CASE WHEN action = 'HIT_T1' THEN 1 ELSE 0 END) as hits
            FROM signals
            WHERE strategy = 'chart_analyst' AND action IS NOT NULL
            GROUP BY conf_bucket
            ORDER BY conf_bucket
        """)).mappings().all()
        
        print("\n--- Analyst Calibration Curve ---")
        for st in stats:
            win_rate = (st['hits'] / st['total'] * 100) if st['total'] > 0 else 0
            print(f"Confidence {st['conf_bucket']}-{st['conf_bucket']+9}% : {win_rate:.1f}% Win Rate ({st['total']} samples)")
            
    print(f"[{datetime.now()}] Scorer complete. {scored_count} suggestions graded.")

if __name__ == "__main__":
    run_nightly_scorer()
