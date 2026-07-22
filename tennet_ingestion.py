import pandas as pd
import numpy as np
import requests
import time
import sqlite3
import argparse
from datetime import datetime, timezone
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, retry_if_exception_type

TENNET_API_KEY = "085bf0ad-a062-4f84-ad4b-d265ee5a6e98"

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def _fetch_single_day(url, headers, params):
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r

def fetch_tennet_afrr_activations(start, end):
    url = "https://api.tennet.eu/publications/v1/frequency-restoration-reserve-activations"
    headers = {
        'Ocp-Apim-Subscription-Key': TENNET_API_KEY,
        'apikey': TENNET_API_KEY,
        'Accept': 'application/json'
    }
    
    daily_dfs = []
    
    # Use timezone-naive dates for iteration to avoid DST timedelta bugs (e.g., 24h jump landing on the same day)
    start_date = start.tz_localize(None).floor('D')
    end_date = end.tz_localize(None).floor('D')
    
    # Loop over every single day to bypass the strict 2-day Period Out of Bounds limit
    for d in pd.date_range(start_date, end_date - pd.Timedelta(days=1), freq='D'):
        date_str = d.strftime('%Y-%m-%d')
        next_date_str = (d + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        params = {
            'date_from': date_str,
            'date_to': next_date_str
        }
        
        print(f"Fetching TenneT aFRR data for {date_str}...", flush=True)
        try:
            r = _fetch_single_day(url, headers, params)
        except Exception as e:
            print(f"Failed to fetch data for {date_str} after retries: {e}", flush=True)
            continue
        time.sleep(1.05) # strictly respect the 60 requests/min rate limit
        
        data = r.json()
        if not data:
            continue
            
        def find_records(obj):
            if isinstance(obj, list):
                if len(obj) > 0 and isinstance(obj[0], dict) and ('timeInterval_start' in obj[0] or 'aFRR_up' in obj[0]):
                    return obj
                for item in obj:
                    res = find_records(item)
                    if res: return res
            elif isinstance(obj, dict):
                # Also check if the dict itself is a record but usually it's a list of dicts
                for v in obj.values():
                    res = find_records(v)
                    if res: return res
            return None
            
        records = find_records(data)
        
        if not records:
            print(f"Skipping {date_str} due to unexpected JSON structure.")
            continue
            
        df = pd.DataFrame(records)
        
        time_col = next((c for c in df.columns if c.lower() in ['time', 'date', 'datetime', 'periodfrom', 'periodstart', 'timeinterval_start']), df.columns[0])
        try:
            df.set_index(time_col, inplace=True)
            df.index = pd.to_datetime(df.index).tz_localize('Europe/Amsterdam', ambiguous='NaT').tz_convert('UTC')
        except Exception as e:
            print(f"Skipping {date_str} due to index parsing error: {e}")
            continue
        
        out_df = pd.DataFrame(index=df.index)
        out_df['afrr_up_activation_price_mwh'] = np.nan
        out_df['afrr_down_activation_price_mwh'] = np.nan

        up_col = next((c for c in df.columns if 'afrr_up' in c.lower()), None)
        down_col = next((c for c in df.columns if 'afrr_down' in c.lower()), None)
        
        if up_col:
            out_df['afrr_up_activation_price_mwh'] = pd.to_numeric(df[up_col], errors='coerce')
        if down_col:
            out_df['afrr_down_activation_price_mwh'] = pd.to_numeric(df[down_col], errors='coerce')
                
        # Drop rows where everything is NaN
        out_df = out_df.dropna(how='all')
        if not out_df.empty:
            daily_dfs.append(out_df)

    if not daily_dfs:
        print("No TenneT data retrieved for the given period.")
        return pd.DataFrame()
        
    final_df = pd.concat(daily_dfs)
    
    # Ensure there are no duplicate indexes
    final_df = final_df[~final_df.index.duplicated(keep='last')]
    
    # Strip timezone for SQLite
    final_df.index = final_df.index.tz_localize(None)
    
    return final_df

def main():
    parser = argparse.ArgumentParser(description="Fetch and ingest TenneT data incrementally.")
    parser.add_argument('--start', type=str, help="Start date (YYYY-MM-DD). If omitted, fetches from latest DB timestamp.")
    parser.add_argument('--end', type=str, help="End date (YYYY-MM-DD). If omitted, fetches up to today.")
    args = parser.parse_args()

    print("Connecting to bess_data.db...")
    con = sqlite3.connect('bess_data.db')
    
    if args.start:
        start = pd.Timestamp(args.start, tz='Europe/Amsterdam')
    else:
        try:
            cursor = con.cursor()
            # Find the latest date we actually have TenneT aFRR data for (avoiding NaNs/0s)
            cursor.execute("SELECT MAX(datetime) FROM historical_market_data WHERE afrr_up_activation_price_mwh IS NOT NULL AND afrr_up_activation_price_mwh != 0.0")
            max_dt = cursor.fetchone()[0]
            if max_dt:
                start = pd.Timestamp(max_dt).tz_localize('UTC').tz_convert('Europe/Amsterdam') - pd.Timedelta(days=1)
            else:
                start = pd.Timestamp('2023-01-01', tz='Europe/Amsterdam')
        except sqlite3.OperationalError:
            start = pd.Timestamp('2023-01-01', tz='Europe/Amsterdam')

    if args.end:
        end = pd.Timestamp(args.end, tz='Europe/Amsterdam')
    else:
        end = pd.Timestamp(datetime.now(timezone.utc)).tz_convert('Europe/Amsterdam').floor('D')
        
    if start >= end:
        print("Data is already up-to-date.")
        con.close()
        return
        
    print(f"Fetching TenneT data from {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}...")
    tennet_df = fetch_tennet_afrr_activations(start, end)
    
    if tennet_df.empty:
        print("No TenneT data to update. Exiting.")
        con.close()
        return
        
    print(f"Retrieved {len(tennet_df)} records from TenneT.")
    
    print("Writing delta to staging table...")
    tennet_df.to_sql('temp_tennet_data', con, if_exists='replace', index=True, index_label='datetime')
    
    print("Executing atomic UPSERT into historical_market_data...")
    con.execute('''
        CREATE TABLE IF NOT EXISTS historical_market_data (
            datetime TEXT PRIMARY KEY
        )
    ''')
    con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_datetime_unique ON historical_market_data (datetime)')
    
    cursor = con.cursor()
    cursor.execute("PRAGMA table_info(temp_tennet_data)")
    temp_cols = [info[1] for info in cursor.fetchall()]
    cursor.execute("PRAGMA table_info(historical_market_data)")
    hist_cols = [info[1] for info in cursor.fetchall()]
    
    for col in temp_cols:
        if col not in hist_cols and col != 'datetime':
            print(f"Adding new column '{col}' to historical_market_data schema.")
            con.execute(f'ALTER TABLE historical_market_data ADD COLUMN "{col}" REAL')
            
    cols_str = ", ".join([f'"{c}"' for c in temp_cols])
    
    if len(temp_cols) > 1:
        update_str = ", ".join([f'"{c}" = excluded."{c}"' for c in temp_cols if c != 'datetime'])
        upsert_sql = f'''
            INSERT INTO historical_market_data ({cols_str}) 
            SELECT {cols_str} FROM temp_tennet_data
            WHERE true
            ON CONFLICT(datetime) DO UPDATE SET {update_str}
        '''
    else:
        upsert_sql = f'INSERT OR IGNORE INTO historical_market_data ({cols_str}) SELECT {cols_str} FROM temp_tennet_data'
        
    con.execute(upsert_sql)
    con.execute('DROP TABLE temp_tennet_data')
    con.commit()
    con.close()
    print("Done!")

if __name__ == "__main__":
    main()
