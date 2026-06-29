import os
import sqlite3
import pandas as pd
from entsoe import EntsoePandasClient
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, retry_if_exception_type

# Initialize API client
# Set your ENTSOE_API_KEY environment variable before running, or replace the default value here.
api_key = os.environ.get("ENTSOE_API_KEY", "bf976337-ff7e-4900-8226-14a2ed469a27")
client = EntsoePandasClient(api_key=api_key)

# Rate-Limiting: Exponential backoff with random jitter to handle HTTP 429 / RemoteDisconnected
@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(Exception)
)
def fetch_prices(start, end, country_code):
    return client.query_day_ahead_prices(country_code, start=start, end=end)

def main():
    country_code = 'NL'
    
    # Query Splitting: bypass Bug #531
    # 2024 query into two half-year calls
    start_1 = pd.Timestamp('2024-01-01', tz='Europe/Amsterdam')
    end_1 = pd.Timestamp('2024-07-01', tz='Europe/Amsterdam')
    
    start_2 = pd.Timestamp('2024-07-01', tz='Europe/Amsterdam')
    end_2 = pd.Timestamp('2025-01-01', tz='Europe/Amsterdam')
    
    print("Fetching H1 2024...")
    try:
        df1 = fetch_prices(start_1, end_1, country_code)
        print("Fetching H2 2024...")
        df2 = fetch_prices(start_2, end_2, country_code)
    except Exception as e:
        print(f"Error fetching data: {e}")
        print("Please ensure you have a valid ENTSO-E API key set in the ENTSOE_API_KEY environment variable.")
        return
    
    # Concatenate results
    df = pd.concat([df1, df2])
    
    # Data Cleaning Strategy
    # 1. Normalize the fetched DataFrame's index to UTC
    df = df.tz_convert('UTC')
    
    # 2. Create a continuous pd.date_range reference index (UTC)
    start_utc = pd.Timestamp('2024-01-01', tz='Europe/Amsterdam').tz_convert('UTC')
    end_utc = pd.Timestamp('2025-01-01', tz='Europe/Amsterdam').tz_convert('UTC') - pd.Timedelta(minutes=15)
    
    # with a 15-minute frequency
    spine = pd.date_range(start=start_utc, end=end_utc, freq='15min')
    
    # Remove any duplicates before reindexing
    df = df[~df.index.duplicated(keep='first')]
    
    # 3. Reindex against this continuous spine
    df = df.reindex(spine)
    
    # 4. Impute missing values: ffill first, then bfill
    df = df.ffill().bfill()
    
    # 5. Re-localize to Europe/Amsterdam
    df = df.tz_convert('Europe/Amsterdam')
    
    # Format as DataFrame if it's a Series
    if isinstance(df, pd.Series):
        df = df.to_frame(name='price')
    else:
        df.columns = ['price']
        
    # Database Handoff: Convert the timezone-aware DataFrame index back to strict UTC
    df = df.tz_convert('UTC')
    
    # --- ARCHITECTURAL FIX ---
    # SQLite via the standard sqlite3 driver does not natively support pandas timezone-aware 
    # objects and will often throw a ValueError. 
    # We must strip the timezone information (making it naive) after converting to UTC.
    df.index = df.index.tz_localize(None)
        
    # Storage
    db_path = "bess_data.db"
    conn = sqlite3.connect(db_path)
    
    # Save to sqlite
    df.to_sql('day_ahead_prices', conn, if_exists='replace', index=True, index_label='datetime')
    conn.close()
    
    print(f"Data successfully saved to {db_path} in the table 'day_ahead_prices'.")

if __name__ == "__main__":
    main()