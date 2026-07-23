import os
import sqlite3
import requests
import time
import pandas as pd
import numpy as np
import yfinance as yf
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
from entsoe import EntsoePandasClient
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, retry_if_exception_type

# Initialize API client
api_key = os.environ.get("ENTSOE_API_KEY", "")
client = EntsoePandasClient(api_key=api_key) if api_key else None

# Rate-Limiting: Exponential backoff with random jitter to handle HTTP 429 / RemoteDisconnected
@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_da_prices(start, end, country_code):
    if not client:
        raise ValueError("ENTSOE_API_KEY environment variable is not set. Please set it to a valid ENTSO-E API key.")
    return client.query_day_ahead_prices(country_code, start=start, end=end)

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_imb_prices(start, end, country_code):
    if not client:
        raise ValueError("ENTSOE_API_KEY environment variable is not set. Please set it to a valid ENTSO-E API key.")
    return client.query_imbalance_prices(country_code, start=start, end=end)

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_load_forecast(start, end, country_code):
    if not client:
        raise ValueError("ENTSOE_API_KEY environment variable is not set. Please set it to a valid ENTSO-E API key.")
    return client.query_load_forecast(country_code, start=start, end=end)

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_wind_solar_forecast(start, end, country_code):
    if not client:
        raise ValueError("ENTSOE_API_KEY environment variable is not set. Please set it to a valid ENTSO-E API key.")
    return client.query_wind_and_solar_forecast(country_code, start=start, end=end)

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_generation(start, end, country_code):
    if not client:
        raise ValueError("ENTSOE_API_KEY environment variable is not set. Please set it to a valid ENTSO-E API key.")
    return client.query_generation(country_code, start=start, end=end)


@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_afrr_capacity(start, end, country_code):
    if not client:
        raise ValueError("ENTSOE_API_KEY environment variable is not set. Please set it to a valid ENTSO-E API key.")
    return client.query_contracted_reserve_prices_procured_capacity(
        country_code=country_code, process_type='A51', type_marketagreement_type='A01', start=start, end=end
    )

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_afrr_activation(start, end, country_code):
    if not client:
        raise ValueError("ENTSOE_API_KEY environment variable is not set. Please set it to a valid ENTSO-E API key.")
    return client.query_activated_balancing_energy_prices(
        country_code=country_code, start=start, end=end
    )

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_fcr_prices(start, end, country_code):
    if not client:
        raise ValueError("ENTSOE_API_KEY environment variable is not set. Please set it to a valid ENTSO-E API key.")
    return client.query_contracted_reserve_prices_procured_capacity(
        country_code=country_code, process_type='A52', type_marketagreement_type='A01', start=start, end=end
    )

@retry(
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def _fetch_tennet_mol_daily(url, headers, d_start, d_end):
    params = {
        'date_from': d_start,
        'date_to': d_end
    }
    r = requests.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_tennet_mol_elasticity(start, end):
    url = "https://api.tennet.eu/publications/v1/merit-order-list"
    headers = {
        'Ocp-Apim-Subscription-Key': os.environ.get("TENNET_API_KEY", ""),
        'apikey': os.environ.get("TENNET_API_KEY", ""),
        'Accept': 'application/json'
    }
    
    start_date = start.tz_localize(None).floor('D')
    end_date = end.tz_localize(None).floor('D')
    
    all_records = []
    
    # Loop over every single day to bypass strict TenneT out-of-bounds limits
    for d in pd.date_range(start_date, end_date - pd.Timedelta(days=1), freq='D'):
        date_str = d.strftime('%Y-%m-%d')
        next_date_str = (d + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        try:
            data = _fetch_tennet_mol_daily(url, headers, date_str, next_date_str)
            def find_records(obj):
                if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
                    return obj
                if isinstance(obj, dict):
                    for v in obj.values():
                        res = find_records(v)
                        if res: return res
                return []
            records = find_records(data)
            all_records.extend(records)
        except Exception as e:
            print(f"Error fetching MOL elasticity for {date_str}: {e}")
            
    if not all_records:
        return pd.DataFrame(columns=['historical_safe_volume_mw', 'historical_saturation_volume_mw'])
        
    df = pd.DataFrame(all_records)
    
    time_col = next((c for c in df.columns if c.lower() in ['time', 'date', 'datetime', 'periodfrom', 'periodstart', 'timeinterval_start', 'valid_from', 'documentdate']), None)
    price_col = next((c for c in df.columns if 'price' in c.lower()), None)
    vol_col = next((c for c in df.columns if 'volume' in c.lower() or 'quantity' in c.lower() or 'mw' in c.lower()), None)
    
    if not time_col or not price_col or not vol_col:
        return pd.DataFrame(columns=['historical_safe_volume_mw', 'historical_saturation_volume_mw'])
        
    df[time_col] = pd.to_datetime(df[time_col], utc=True)
    df[price_col] = pd.to_numeric(df[price_col], errors='coerce')
    df[vol_col] = pd.to_numeric(df[vol_col], errors='coerce')
    
    df = df.dropna(subset=[price_col, vol_col])
    
    results = []
    # Group by 15-minute time block
    for t_block, group in df.groupby(pd.Grouper(key=time_col, freq='15min')):
        if group.empty:
            continue
            
        # sort the anonymous bids
        group = group.sort_values(by=price_col, ascending=False)
        max_price = group[price_col].max()
        
        if pd.isna(max_price) or max_price <= 0:
            results.append({
                'datetime': t_block,
                'historical_safe_volume_mw': 0.0,
                'historical_saturation_volume_mw': 0.0
            })
            continue
            
        target_5 = max_price * 0.95
        target_50 = max_price * 0.50
        
        # Calculate the cumulative volume required to push clearing price down
        vol_5 = group.loc[group[price_col] > target_5, vol_col].sum()
        vol_50 = group.loc[group[price_col] > target_50, vol_col].sum()
        
        results.append({
            'datetime': t_block,
            'historical_safe_volume_mw': vol_5,
            'historical_saturation_volume_mw': vol_50
        })
        
    out_df = pd.DataFrame(results)
    if not out_df.empty:
        out_df.set_index('datetime', inplace=True)
        if out_df.index.tz is None:
            out_df.index = out_df.index.tz_localize('UTC')
        else:
            out_df.index = out_df.index.tz_convert('UTC')
    else:
        out_df = pd.DataFrame(columns=['historical_safe_volume_mw', 'historical_saturation_volume_mw'])
        out_df.index.name = 'datetime'
        
    return out_df

def main():
    parser = argparse.ArgumentParser(description="Fetch and ingest ENTSO-E/TenneT data incrementally.")
    parser.add_argument('--start', type=str, help="Start date (YYYY-MM-DD). If omitted, fetches from latest DB timestamp.")
    parser.add_argument('--end', type=str, help="End date (YYYY-MM-DD). If omitted, fetches up to today.")
    args = parser.parse_args()

    db_path = "bess_data.db"
    conn = sqlite3.connect(db_path)

    if args.start:
        start_date = pd.Timestamp(args.start)
    else:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(datetime) FROM historical_market_data")
            max_dt = cursor.fetchone()[0]
            if max_dt:
                # Start 1 day before the max_dt to ensure overlap and gap filling
                start_date = pd.Timestamp(max_dt) - pd.Timedelta(days=1)
            else:
                start_date = pd.Timestamp('2023-01-01')
        except sqlite3.OperationalError:
            start_date = pd.Timestamp('2023-01-01')
            
    if args.end:
        end_date = pd.Timestamp(args.end)
    else:
        end_date = pd.Timestamp(datetime.now(timezone.utc)).floor('D')
        
    start_date = start_date.tz_localize(None)
    end_date = end_date.tz_localize(None)
    
    if start_date >= end_date:
        print("Data is already up-to-date.")
        conn.close()
        return

    print(f"Ingesting data from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...")

    country_code = 'NL'
    
    # Generate strict 1-month chunks to prevent ENTSO-E payload crashes
    date_range = pd.date_range(start=start_date, end=end_date, freq='MS')
    # If the range is smaller than a month, or end date doesn't land exactly on MS, append end_date
    if len(date_range) == 0 or date_range[-1] < end_date:
        date_range = date_range.append(pd.DatetimeIndex([end_date]))
    if date_range[0] > start_date:
        date_range = date_range.insert(0, start_date)
        
    dates = []
    for i in range(len(date_range) - 1):
        dates.append((date_range[i].strftime('%Y-%m-%d'), date_range[i+1].strftime('%Y-%m-%d')))
        
    if not dates:
        dates = [(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))]
        
    da_dfs = []
    imb_dfs = []
    load_dfs = []
    ws_dfs = []
    gen_dfs = []
    afrr_cap_dfs = []
    afrr_act_dfs = []
    mol_dfs = []
    fcr_dfs = []
    
    for start_str, end_str in dates:
        start = pd.Timestamp(start_str, tz='Europe/Amsterdam')
        end = pd.Timestamp(end_str, tz='Europe/Amsterdam')
        print(f"Fetching data from {start_str} to {end_str}...")
        try:
            da = fetch_da_prices(start, end, country_code)
            da_dfs.append(da)
            
            imb = fetch_imb_prices(start, end, country_code)
            imb_dfs.append(imb)
        except Exception as e:
            print(f"Error fetching price data for {start_str}-{end_str}: {e}")
            
        try:
            load = fetch_load_forecast(start, end, country_code)
            load_dfs.append(load)
        except Exception as e:
            print(f"Error fetching load forecast for {start_str}-{end_str}: {e}")
            
        try:
            ws = fetch_wind_solar_forecast(start, end, country_code)
            ws_dfs.append(ws)
        except Exception as e:
            print(f"Error fetching wind/solar forecast for {start_str}-{end_str}: {e}")
            
        try:
            gen = fetch_generation(start, end, country_code)
            gen_dfs.append(gen)
        except Exception as e:
            print(f"Error fetching actual generation for {start_str}-{end_str}: {e}")
            

        try:
            afrr_cap = fetch_afrr_capacity(start, end, country_code)
            if afrr_cap is not None and not afrr_cap.empty:
                afrr_cap_dfs.append(afrr_cap)
        except Exception as e:
            print(f"Error fetching aFRR capacity prices for {start_str}-{end_str}: {e}")
            
        try:
            afrr_act = fetch_afrr_activation(start, end, country_code)
                
            if afrr_act is not None and not afrr_act.empty:
                afrr_act_dfs.append(afrr_act)
        except Exception as e:
            print(f"Error fetching aFRR activation prices for {start_str}-{end_str}: {e}")

        try:
            fcr = fetch_fcr_prices(start, end, country_code)
            if fcr is not None and not fcr.empty:
                fcr_dfs.append(fcr)
        except Exception as e:
            print(f"Error fetching FCR capacity prices for {start_str}-{end_str}: {e}")

        try:
            mol = fetch_tennet_mol_elasticity(start, end)
            if mol is not None and not mol.empty:
                mol_dfs.append(mol)
        except Exception as e:
            print(f"Error fetching MOL elasticity for {start_str}-{end_str}: {e}")

        # Sleep to respect rate limits
        time.sleep(1.0)
            
    # Combine
    da_df = pd.concat(da_dfs)
    imb_df = pd.concat(imb_dfs)
    load_df = pd.concat(load_dfs)
    ws_df = pd.concat(ws_dfs)
    gen_df = pd.concat(gen_dfs)
    
    # Cleaning DA
    da_df = da_df.tz_convert('UTC')
    da_df = da_df[~da_df.index.duplicated(keep='first')]
    if isinstance(da_df, pd.Series):
        da_df = da_df.to_frame(name='da_price_actual')
    else:
        da_df.columns = ['da_price_actual']
        
    # Cleaning Imbalance
    imb_df = imb_df.tz_convert('UTC')
    imb_df = imb_df[~imb_df.index.duplicated(keep='first')]
    if isinstance(imb_df, pd.DataFrame):
        if 'Short' in imb_df.columns and 'Long' in imb_df.columns:
            imb_df = imb_df[['Short', 'Long']].rename(columns={'Short': 'imbalance_price_short_15m', 'Long': 'imbalance_price_long_15m'})
        else:
            imb_df = imb_df.iloc[:, 0].to_frame(name='imbalance_price_15m')
    else:
        imb_df = imb_df.to_frame(name='imbalance_price_15m')
    
    # Cleaning Load Forecast
    load_df = load_df.tz_convert('UTC')
    load_df = load_df[~load_df.index.duplicated(keep='first')]
    if isinstance(load_df, pd.Series):
        load_df = load_df.to_frame(name='Load Forecast')
    else:
        load_df.columns = ['Load Forecast']
        
    # Cleaning Wind/Solar Forecast
    ws_df = ws_df.tz_convert('UTC')
    ws_df = ws_df[~ws_df.index.duplicated(keep='first')]
    
    # Cleaning Actual Generation
    gen_df = gen_df.tz_convert('UTC')
    gen_df = gen_df[~gen_df.index.duplicated(keep='first')]
    
    gen_solar = gen_df.get(('Solar', 'Actual Aggregated'), pd.Series(index=gen_df.index, dtype=float))
    gen_wind_on = gen_df.get(('Wind Onshore', 'Actual Aggregated'), pd.Series(index=gen_df.index, dtype=float))
    gen_wind_off = gen_df.get(('Wind Offshore', 'Actual Aggregated'), pd.Series(index=gen_df.index, dtype=float))
    
    actual_gen = pd.DataFrame({
        'Solar_Actual': gen_solar,
        'Wind_Onshore_Actual': gen_wind_on,
        'Wind_Offshore_Actual': gen_wind_off
    }, index=gen_df.index)
    

    # Cleaning aFRR Capacity
    if afrr_cap_dfs:
        afrr_cap_df = pd.concat(afrr_cap_dfs)
        afrr_cap_df = afrr_cap_df.tz_convert('UTC')
        afrr_cap_df = afrr_cap_df[~afrr_cap_df.index.duplicated(keep='first')]
        
        cols_to_keep = {}
        if 'Up Prices' in afrr_cap_df.columns:
            cols_to_keep['Up Prices'] = 'afrr_up_price_mw'
        if 'Down Prices' in afrr_cap_df.columns:
            cols_to_keep['Down Prices'] = 'afrr_down_price_mw'
            
        afrr_cap_df = afrr_cap_df[list(cols_to_keep.keys())].rename(columns=cols_to_keep)
    else:
        afrr_cap_df = pd.DataFrame(columns=['afrr_up_price_mw', 'afrr_down_price_mw'])
        
    # Cleaning aFRR Activation
    if afrr_act_dfs:
        afrr_act_df = pd.concat(afrr_act_dfs)
        afrr_act_df = afrr_act_df.tz_convert('UTC')
        if 'ReserveType' in afrr_act_df.columns:
            afrr_act_df = afrr_act_df[afrr_act_df['ReserveType'] == 'aFRR']
            
        afrr_act_df = afrr_act_df.reset_index().drop_duplicates(subset=['index', 'Direction'], keep='first')
        afrr_act_df = afrr_act_df.pivot(index='index', columns='Direction', values='Price')
        
        cols_to_keep = {}
        if 'Up' in afrr_act_df.columns:
            cols_to_keep['Up'] = 'afrr_up_activation_price_mwh'
        if 'Down' in afrr_act_df.columns:
            cols_to_keep['Down'] = 'afrr_down_activation_price_mwh'
            
        afrr_act_df = afrr_act_df[list(cols_to_keep.keys())].rename(columns=cols_to_keep)
        afrr_act_df.index.name = None
    else:
            afrr_act_df = pd.DataFrame(columns=['afrr_up_activation_price_mwh', 'afrr_down_activation_price_mwh'])

    # Cleaning FCR Capacity
    if fcr_dfs:
        fcr_df = pd.concat(fcr_dfs)
        fcr_df = fcr_df.tz_convert('UTC')
        fcr_df = fcr_df[~fcr_df.index.duplicated(keep='first')]
        
        if 'Symmetric Prices' in fcr_df.columns:
            fcr_df = fcr_df[['Symmetric Prices']].rename(columns={'Symmetric Prices': 'fcr_price_eur_mw'})
        else:
            fcr_col = fcr_df.columns[0]
            fcr_df = fcr_df[[fcr_col]].rename(columns={fcr_col: 'fcr_price_eur_mw'})
    else:
        fcr_df = pd.DataFrame(columns=['fcr_price_eur_mw'])

    print(f"Fetching TTF Gas Prices via yfinance from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...")
    try:
        ttf_df = yf.download("TTF=F", start=start_date.strftime('%Y-%m-%d'), end=(end_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        if not ttf_df.empty and 'Close' in ttf_df.columns:
            gas_series = ttf_df['Close'].shift(1)
        else:
            gas_series = pd.Series(0.0, index=[pd.to_datetime(start_date)])
    except Exception as e:
        print(f"Error fetching TTF Gas Prices: {e}")
        gas_series = pd.Series(0.0, index=[pd.to_datetime(start_date)])
        
    if isinstance(gas_series, pd.DataFrame):
        gas_series = gas_series.iloc[:, 0]
    gas_series.index = pd.to_datetime(gas_series.index).tz_localize('UTC')
    
    # Create continuous 15-min spine
    start_utc = start_date.tz_localize('Europe/Amsterdam', ambiguous='NaT', nonexistent='shift_forward').tz_convert('UTC')
    end_utc = end_date.tz_localize('Europe/Amsterdam', ambiguous='NaT', nonexistent='shift_forward').tz_convert('UTC') - pd.Timedelta(minutes=15)
    
    # Spine generation with 15min freq
    spine = pd.date_range(start=start_utc, end=end_utc, freq='15min')
    
    # Reindex and forward fill DA prices to 15-minute resolution
    # Da price might come at hourly start. Reindex drops missing values unless filled.
    da_df = da_df.reindex(spine)
    da_df = da_df.ffill().bfill()
    
    # Reindex Imbalance prices
    imb_df = imb_df.reindex(spine)
    imb_df = imb_df.ffill().bfill()
    
    # Reindex Load Forecast
    load_df = load_df.reindex(spine)
    load_df = load_df.ffill().bfill()
    
    # Reindex Wind/Solar Forecast
    ws_df = ws_df.reindex(spine)
    ws_df = ws_df.ffill().bfill()
    
    # Reindex Actual Generation (DO NOT ffill() here, we want NaNs so we can safely handle them)
    actual_gen = actual_gen.reindex(spine)
    
    # Reindex Gas prices
    gas_df = gas_series.reindex(spine).ffill().bfill()
    
    # Reindex aFRR
    afrr_cap_df = afrr_cap_df.reindex(spine).fillna(0.0)
    afrr_act_df = afrr_act_df.reindex(spine)
    
    # Reindex FCR
    fcr_df = fcr_df.reindex(spine)
    fcr_df = fcr_df.ffill().bfill().fillna(0.0)
    
    # Concatenate and reindex MOL elasticity
    if mol_dfs:
        mol_df = pd.concat(mol_dfs)
        mol_df = mol_df[~mol_df.index.duplicated(keep='first')]
    else:
        mol_df = pd.DataFrame(columns=['historical_safe_volume_mw', 'historical_saturation_volume_mw'])
        
    mol_df = mol_df.reindex(spine)
    mol_df = mol_df.ffill().bfill()
    
    # Join into unified DataFrame
    df = pd.DataFrame(index=spine)
    df['da_price_actual'] = da_df['da_price_actual']
    
    if 'imbalance_price_short_15m' in imb_df.columns:
        df['imbalance_price_short_15m'] = imb_df['imbalance_price_short_15m']
        df['imbalance_price_long_15m'] = imb_df['imbalance_price_long_15m']
    else:
        df['imbalance_price_15m'] = imb_df['imbalance_price_15m']
    df['Load Forecast'] = load_df['Load Forecast']
    for col in ['Solar', 'Wind Onshore', 'Wind Offshore']:
        if col in ws_df.columns:
            df[col] = ws_df[col]
            actual_col_name = f"{col.replace(' ', '_')}_Actual"
            # Fallback for API failures: If ENTSO-E dropped the chunk, assume Actual = Forecast (Error = 0)
            safe_actuals = actual_gen[actual_col_name].fillna(df[col])
            df[f"{col}_Error"] = df[col] - safe_actuals
            
    df['Gas Price'] = gas_df
    

    if 'afrr_up_price_mw' in afrr_cap_df.columns:
        df['afrr_up_price_mw'] = afrr_cap_df['afrr_up_price_mw']
    if 'afrr_down_price_mw' in afrr_cap_df.columns:
        df['afrr_down_price_mw'] = afrr_cap_df['afrr_down_price_mw']
    if 'afrr_up_activation_price_mwh' in afrr_act_df.columns:
        df['afrr_up_activation_price_mwh'] = afrr_act_df['afrr_up_activation_price_mwh']
    else:
        df['afrr_up_activation_price_mwh'] = np.nan
        
    if 'afrr_down_activation_price_mwh' in afrr_act_df.columns:
        df['afrr_down_activation_price_mwh'] = afrr_act_df['afrr_down_activation_price_mwh']
    else:
        df['afrr_down_activation_price_mwh'] = np.nan
        
    # aFRR Activation Prices are preserved. 0.0 is legitimate. Non-activated periods remain NaN.
    
    # Add MOL elasticity columns
    df['historical_safe_volume_mw'] = mol_df['historical_safe_volume_mw']
    df['historical_saturation_volume_mw'] = mol_df['historical_saturation_volume_mw']
    
    df['fcr_price_eur_mw'] = fcr_df['fcr_price_eur_mw']
    
    # Strip timezone for sqlite
    df.index = df.index.tz_localize(None)
    
    print("Writing delta to staging table...")
    df.to_sql('temp_market_data', conn, if_exists='replace', index=True, index_label='datetime')
    
    print("Executing atomic UPSERT into historical_market_data...")
    conn.execute('''
        CREATE TABLE IF NOT EXISTS historical_market_data (
            datetime TEXT PRIMARY KEY
        )
    ''')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_datetime_unique ON historical_market_data (datetime)')
    
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(temp_market_data)")
    temp_cols = [info[1] for info in cursor.fetchall()]
    cursor.execute("PRAGMA table_info(historical_market_data)")
    hist_cols = [info[1] for info in cursor.fetchall()]
    
    # Dynamic schema evolution
    for col in temp_cols:
        if col not in hist_cols and col != 'datetime':
            print(f"Adding new column '{col}' to historical_market_data schema.")
            conn.execute(f'ALTER TABLE historical_market_data ADD COLUMN "{col}" REAL')
            
    cols_str = ", ".join([f'"{c}"' for c in temp_cols])
    
    if len(temp_cols) > 1:
        update_str = ", ".join([f'"{c}" = excluded."{c}"' for c in temp_cols if c != 'datetime'])
        upsert_sql = f'''
            INSERT INTO historical_market_data ({cols_str}) 
            SELECT {cols_str} FROM temp_market_data
            WHERE true
            ON CONFLICT(datetime) DO UPDATE SET {update_str}
        '''
    else:
        upsert_sql = f'INSERT OR IGNORE INTO historical_market_data ({cols_str}) SELECT {cols_str} FROM temp_market_data'
        
    conn.execute(upsert_sql)
    conn.execute('DROP TABLE temp_market_data')
    conn.commit()
    conn.close()
    
    print(f"Incremental update successfully UPSERTed into 'historical_market_data'.")

if __name__ == "__main__":
    main()