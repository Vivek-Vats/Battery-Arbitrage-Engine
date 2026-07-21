import pandas as pd
import numpy as np
import requests
import time
import sqlite3
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
    print("Connecting to bess_data.db...")
    con = sqlite3.connect('bess_data.db')
    
    # Load the existing historical_market_data table
    print("Loading historical_market_data from database...")
    hist_df = pd.read_sql("SELECT * FROM historical_market_data", con)
    hist_df['datetime'] = pd.to_datetime(hist_df['datetime'])
    hist_df.set_index('datetime', inplace=True)
    
    # Define period
    start = pd.Timestamp('2025-10-01', tz='Europe/Amsterdam')
    end = pd.Timestamp('2026-07-01', tz='Europe/Amsterdam')
    
    print(f"Fetching TenneT data from {start} to {end}...")
    tennet_df = fetch_tennet_afrr_activations(start, end)
    
    if tennet_df.empty:
        print("No TenneT data to update. Exiting.")
        return
        
    print(f"Retrieved {len(tennet_df)} records from TenneT.")
    
    # Update the historical dataframe
    # Find overlapping indexes
    overlap_idx = hist_df.index.intersection(tennet_df.index)
    
    if len(overlap_idx) > 0:
        print(f"Updating {len(overlap_idx)} records in historical_market_data...")
        if 'afrr_up_activation_price_mwh' not in hist_df.columns:
            hist_df['afrr_up_activation_price_mwh'] = np.nan
        if 'afrr_down_activation_price_mwh' not in hist_df.columns:
            hist_df['afrr_down_activation_price_mwh'] = np.nan
            
        hist_df.loc[overlap_idx, 'afrr_up_activation_price_mwh'] = tennet_df.loc[overlap_idx, 'afrr_up_activation_price_mwh'].combine_first(hist_df.loc[overlap_idx, 'afrr_up_activation_price_mwh'])
        hist_df.loc[overlap_idx, 'afrr_down_activation_price_mwh'] = tennet_df.loc[overlap_idx, 'afrr_down_activation_price_mwh'].combine_first(hist_df.loc[overlap_idx, 'afrr_down_activation_price_mwh'])
        
        # We don't overwrite with 0.0 here for everything, let's just keep the NaNs or whatever they were.
        # But earlier we said fill missing with 0.0. Let's do that for the whole column again just in case.
        hist_df['afrr_up_activation_price_mwh'] = hist_df['afrr_up_activation_price_mwh'].fillna(0.0)
        hist_df['afrr_down_activation_price_mwh'] = hist_df['afrr_down_activation_price_mwh'].fillna(0.0)

        # Write back to SQLite preserving table schema and indexes
        print("Saving updated historical_market_data back to SQLite...")
        cursor = con.cursor()
        cursor.execute("DELETE FROM historical_market_data")
        con.commit()
        hist_df.to_sql("historical_market_data", con, if_exists="append", index=True, index_label="datetime")
        print("Done!")
    else:
        print("No overlapping dates found in the database. Nothing updated.")
        
    con.close()

if __name__ == "__main__":
    main()
