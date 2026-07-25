import os
import io
import zipfile
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import create_engine, text
from core.persistence.database import get_engine  # Assumed helper, fallback below

DB_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/omega")

def download_bhavcopy(date: datetime.date) -> Optional[pd.DataFrame]:
    """
    Downloads NSE F&O bhavcopy for a given date.
    URL format: https://archives.nseindia.com/content/historical/DERIVATIVES/YYYY/MMM/foDDMMMYYYYbhav.csv.zip
    """
    month_str = date.strftime('%b').upper()
    year_str = date.strftime('%Y')
    day_str = date.strftime('%d')
    
    url = f"https://archives.nseindia.com/content/historical/DERIVATIVES/{year_str}/{month_str}/fo{day_str}{month_str}{year_str}bhav.csv.zip"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    print(f"Fetching {url}")
    response = requests.get(url, headers=headers, timeout=10)
    
    if response.status_code != 200:
        print(f"Failed to download bhavcopy for {date}: HTTP {response.status_code}")
        return None
        
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            filename = z.namelist()[0]
            with z.open(filename) as f:
                df = pd.read_csv(f)
                return df
    except Exception as e:
        print(f"Failed to extract/parse ZIP for {date}: {e}")
        return None

def process_and_load(df: pd.DataFrame, date: datetime.date):
    """
    Parses the raw CSV and upserts into Postgres.
    """
    engine = create_engine(DB_URL)
    
    # Strip whitespace from column names
    df.columns = df.columns.str.strip()
    
    # Parse expiry dates
    df['EXPIRY_DT'] = pd.to_datetime(df['EXPIRY_DT'], format='%d-%b-%Y').dt.date
    
    # 1. Options (OPTSTK, OPTIDX)
    options_df = df[df['INSTRUMENT'].isin(['OPTSTK', 'OPTIDX'])].copy()
    if not options_df.empty:
        opts_to_insert = pd.DataFrame({
            'symbol': options_df['SYMBOL'],
            'expiry': options_df['EXPIRY_DT'],
            'strike': options_df['STRIKE_PR'],
            'opt_type': options_df['OPTION_TYP'],
            'date': date,
            'o': options_df['OPEN'],
            'h': options_df['HIGH'],
            'l': options_df['LOW'],
            'c': options_df['CLOSE'],
            'settle': options_df['SETTLE_PR'],
            'oi': options_df['OPEN_INT'],
            'volume': options_df['CONTRACTS']
        })
        opts_to_insert.to_sql('options_eod_temp', engine, if_exists='replace', index=False)
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO options_eod (symbol, expiry, strike, opt_type, date, o, h, l, c, settle, oi, volume)
                SELECT symbol, expiry, strike, opt_type, date, o, h, l, c, settle, oi, volume FROM options_eod_temp
                ON CONFLICT (symbol, expiry, strike, opt_type, date) DO UPDATE SET
                    o=EXCLUDED.o, h=EXCLUDED.h, l=EXCLUDED.l, c=EXCLUDED.c, 
                    settle=EXCLUDED.settle, oi=EXCLUDED.oi, volume=EXCLUDED.volume;
            """))

    # 2. Futures (FUTSTK, FUTIDX)
    futures_df = df[df['INSTRUMENT'].isin(['FUTSTK', 'FUTIDX'])].copy()
    if not futures_df.empty:
        futs_to_insert = pd.DataFrame({
            'symbol': futures_df['SYMBOL'],
            'expiry': futures_df['EXPIRY_DT'],
            'date': date,
            'o': futures_df['OPEN'],
            'h': futures_df['HIGH'],
            'l': futures_df['LOW'],
            'c': futures_df['CLOSE'],
            'settle': futures_df['SETTLE_PR'],
            'oi': futures_df['OPEN_INT'],
            'volume': futures_df['CONTRACTS']
        })
        futs_to_insert.to_sql('futures_eod_temp', engine, if_exists='replace', index=False)
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO futures_eod (symbol, expiry, date, o, h, l, c, settle, oi, volume)
                SELECT symbol, expiry, date, o, h, l, c, settle, oi, volume FROM futures_eod_temp
                ON CONFLICT (symbol, expiry, date) DO UPDATE SET
                    o=EXCLUDED.o, h=EXCLUDED.h, l=EXCLUDED.l, c=EXCLUDED.c, 
                    settle=EXCLUDED.settle, oi=EXCLUDED.oi, volume=EXCLUDED.volume;
            """))
            
    print(f"[{date}] Processed {len(options_df)} options and {len(futures_df)} futures.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYY-MM-DD", default=datetime.today().strftime('%Y-%m-%d'))
    parser.add_argument("--backfill-days", type=int, default=0)
    args = parser.parse_args()

    end_date = datetime.strptime(args.date, '%Y-%m-%d').date()
    
    for i in range(args.backfill_days + 1):
        target_date = end_date - timedelta(days=i)
        if target_date.weekday() >= 5: # Skip weekends
            continue
            
        df = download_bhavcopy(target_date)
        if df is not None:
            process_and_load(df, target_date)
