import sqlite3
import time
import json
import logging
import pandas as pd
import quant_engine
import finance_engine

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def init_db(conn):
    """Ensure required tables exist."""
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_queue (
            job_id TEXT PRIMARY KEY,
            power_mw REAL,
            energy_mwh REAL,
            capex_per_kwh REAL,
            opex_per_mw REAL,
            wacc REAL,
            lifespan REAL,
            grid_fee_import REAL,
            efficiency_store REAL,
            efficiency_dispatch REAL,
            depth_of_discharge REAL,
            degradation_penalty REAL,
            status TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_results (
            job_id TEXT PRIMARY KEY,
            metrics_json TEXT,
            dispatch_json TEXT
        )
    ''')
    conn.commit()

def main():
    db_path = 'bess_data.db'
    conn = sqlite3.connect(db_path)
    init_db(conn)

    logger.info("Starting BESS Orchestrator. Polling for jobs...")

    while True:
        try:
            cursor = conn.cursor()
            
            # Poll for a pending job
            cursor.execute('''
                SELECT job_id, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty
                FROM job_queue 
                WHERE status = 'PENDING' 
                LIMIT 1
            ''')
            row = cursor.fetchone()
            
            if row:
                job_id, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty = row
                
                # Transaction Locking: immediately update status to 'RUNNING'
                cursor.execute("UPDATE job_queue SET status = 'RUNNING' WHERE job_id = ?", (job_id,))
                conn.commit()
                
                logger.info(f"Picked up job {job_id}. Status set to RUNNING.")
                
                try:
                    # Data Loading
                    df = pd.read_sql_query("SELECT * FROM day_ahead_prices", conn)
                    
                    # Lead Architect Addendum 1: Strip timezone to prevent PyPSA crash
                    df['datetime'] = pd.to_datetime(df['datetime']).dt.tz_localize(None)
                    df.set_index('datetime', inplace=True)
                    
                    # Domain Execution
                    # 1. Optimize dispatch
                    dispatch_df = quant_engine.optimize_dispatch(df, power_mw, energy_mwh, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty)
                    
                    # 2. Calculate financials
                    metrics = finance_engine.calculate_financials(dispatch_df, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, grid_fee_import, efficiency_dispatch)
                    
                    # Serialization & Output
                    metrics_json = json.dumps(metrics)
                    
                    # Lead Architect Addendum 2: Explicitly name the index before resetting
                    dispatch_df.index.name = 'datetime'
                    dispatch_df_reset = dispatch_df.reset_index()
                    
                    # Timezone Shift Fix (Leak 3): Re-localize to Europe/Amsterdam for frontend UI
                    dispatch_df_reset['datetime'] = pd.to_datetime(dispatch_df_reset['datetime']).dt.tz_localize('UTC').dt.tz_convert('Europe/Amsterdam')
                    
                    # Format datetime as ISO string to prevent JSON serialization issues
                    dispatch_df_reset['datetime'] = dispatch_df_reset['datetime'].dt.strftime('%Y-%m-%dT%H:%M:%S')
                    dispatch_json = dispatch_df_reset.to_json(orient='records')
                    
                    # Insert into job_results
                    cursor.execute('''
                        INSERT OR REPLACE INTO job_results (job_id, metrics_json, dispatch_json) 
                        VALUES (?, ?, ?)
                    ''', (job_id, metrics_json, dispatch_json))
                    
                    # Update job status to 'COMPLETED'
                    cursor.execute("UPDATE job_queue SET status = 'COMPLETED' WHERE job_id = ?", (job_id,))
                    conn.commit()
                    
                    logger.info(f"Job {job_id} processed successfully.")
                    
                except Exception as e:
                    logger.error(f"Job {job_id} failed during execution: {e}", exc_info=True)
                    cursor.execute("UPDATE job_queue SET status = 'FAILED' WHERE job_id = ?", (job_id,))
                    conn.commit()
            
        except Exception as e:
            logger.error(f"Error in orchestration polling loop: {e}", exc_info=True)
            
        time.sleep(2)

if __name__ == "__main__":
    main()
