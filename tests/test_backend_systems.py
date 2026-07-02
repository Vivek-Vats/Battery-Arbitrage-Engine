import pytest
from unittest.mock import patch
import pandas as pd
import sqlite3
import data_ingestion
import bess_orchestrator

class StopLoopException(BaseException):
    """Exception to break out of the orchestrator's infinite polling loop."""
    pass

@patch('data_ingestion.fetch_prices')
def test_data_ingestion_cleaning(mock_fetch):
    # Set up in-memory sqlite connection for data_ingestion
    conn = sqlite3.connect(':memory:')
    
    # 1. Provide fake 2-hour data to the mock API
    idx = pd.DatetimeIndex(['2024-01-01 00:00:00', '2024-01-01 01:00:00'], tz='Europe/Amsterdam')
    df_fake = pd.Series([10.0, 20.0], index=idx)
    
    # The script calls fetch_prices twice (H1 and H2), we mock both.
    mock_fetch.side_effect = [df_fake, pd.Series(dtype=float)] 

    # We need to intercept the write to prevent actual database modification 
    # and to easily verify the resulting dataframe shape.
    original_to_sql = pd.DataFrame.to_sql
    written_df = None
    
    def mock_to_sql(self, *args, **kwargs):
        nonlocal written_df
        written_df = self.copy()
        # Optionally, we could still write to our in-memory db here
        original_to_sql(self, args[0], conn, **{k:v for k,v in kwargs.items() if k != 'con'})
        return

    with patch('data_ingestion.pd.DataFrame.to_sql', new=mock_to_sql):
        # We patch the spine generation so it aligns with our 2-hour window.
        # The 2-hour window starting at 2024-01-01 00:00:00 (Amsterdam) 
        # is 2023-12-31 23:00:00 (UTC).
        # We create 8 periods of 15-minute intervals.
        spine = pd.date_range('2023-12-31 23:00:00', periods=8, freq='15min', tz='UTC')
        with patch('data_ingestion.pd.date_range', return_value=spine):
            data_ingestion.main()
    
    assert written_df is not None, "Data was not written to database"
    
    # Verify the dataframe is 8 rows long
    assert len(written_df) == 8, f"Expected 8 rows, got {len(written_df)}"
    
    # Verify the column exists and forward fill worked
    assert 'price' in written_df.columns
    
    # Check that forward filling happened.
    # The first 4 rows should correspond to the first hour (10.0)
    # The next 4 rows should correspond to the second hour (20.0)
    prices = written_df['price'].values
    assert prices[0] == 10.0
    assert prices[3] == 10.0
    assert prices[4] == 20.0
    assert prices[7] == 20.0

def test_orchestrator_job_state():
    # Set up in-memory sqlite connection for orchestrator
    conn = sqlite3.connect(':memory:')
    bess_orchestrator.init_db(conn)
    cursor = conn.cursor()
    
    # Inject a fake job row with status='PENDING'
    cursor.execute('''
        INSERT INTO job_queue (
            job_id, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, 
            lifespan, grid_fee_import, efficiency_store, efficiency_dispatch, 
            depth_of_discharge, degradation_penalty, expected_lifespan_cycles, status
        )
        VALUES ('job_1', 10, 20, 100, 1000, 0.05, 10, 0.01, 0.9, 0.9, 0.8, 0, 6000.0, 'PENDING')
    ''')
    conn.commit()

    # The orchestrator needs day_ahead_prices data for domain execution
    cursor.execute('''CREATE TABLE day_ahead_prices (datetime TEXT, price REAL)''')
    cursor.execute("INSERT INTO day_ahead_prices VALUES ('2024-01-01 00:00:00', 10.0)")
    cursor.execute("INSERT INTO day_ahead_prices VALUES ('2024-01-01 00:15:00', 12.0)")
    conn.commit()
    
    # Patch sqlite3.connect to return our in-memory connection
    # Patch time.sleep to break the infinite polling loop after one iteration
    with patch('bess_orchestrator.sqlite3.connect') as mock_connect:
        mock_connect.return_value = conn
        with patch('bess_orchestrator.time.sleep', side_effect=StopLoopException):
            try:
                bess_orchestrator.main()
            except StopLoopException:
                pass

    # Verify that the job's status correctly updates to 'COMPLETED'
    cursor.execute("SELECT status FROM job_queue WHERE job_id='job_1'")
    status_row = cursor.fetchone()
    assert status_row is not None
    assert status_row[0] == 'COMPLETED'
    
    # Verify that the data is successfully written to the job_results table
    cursor.execute("SELECT * FROM job_results WHERE job_id='job_1'")
    result_row = cursor.fetchone()
    assert result_row is not None, "Job result was not written to job_results table"

import requests

@patch('data_ingestion.client.query_day_ahead_prices')
def test_data_ingestion_api_429(mock_query):
    # Setup mock to raise HTTPError to trigger tenacity @retry, then return valid data on second try
    idx = pd.DatetimeIndex(['2024-01-01 00:00:00'], tz='Europe/Amsterdam')
    valid_data = pd.Series([10.0], index=idx)
    
    mock_query.side_effect = [requests.exceptions.HTTPError("429 Too Many Requests"), valid_data, valid_data]
    
    conn = sqlite3.connect(':memory:')
    original_to_sql = pd.DataFrame.to_sql
    written_df = None
    
    def mock_to_sql(self, *args, **kwargs):
        nonlocal written_df
        written_df = self.copy()
        original_to_sql(self, args[0], conn, **{k:v for k,v in kwargs.items() if k != 'con'})
    
    with patch('data_ingestion.pd.DataFrame.to_sql', new=mock_to_sql):
        spine = pd.date_range('2023-12-31 23:00:00', periods=4, freq='15min', tz='UTC')
        with patch('data_ingestion.pd.date_range', return_value=spine):
            data_ingestion.main()
            
    assert written_df is not None
    assert mock_query.call_count == 3  # Failed once, succeeded twice (H1 and H2)

@patch('data_ingestion.fetch_prices')
def test_data_ingestion_dst_boundary(mock_fetch):
    # Simulate a 25-hour day for Fall Back transition
    idx = pd.date_range('2024-10-27 00:00:00', periods=25, freq='h', tz='Europe/Amsterdam')
    df_fake = pd.Series(range(25), index=idx, dtype=float)
    
    mock_fetch.side_effect = [df_fake, pd.Series(dtype=float)] 
    conn = sqlite3.connect(':memory:')
    original_to_sql = pd.DataFrame.to_sql
    written_df = None
    
    def mock_to_sql(self, *args, **kwargs):
        nonlocal written_df
        written_df = self.copy()
    
    with patch('data_ingestion.pd.DataFrame.to_sql', new=mock_to_sql):
        # We don't patch date_range here, let it create the natural spine
        # Just limit the date window in the test
        start_utc = pd.Timestamp('2024-10-27', tz='Europe/Amsterdam').tz_convert('UTC')
        end_utc = pd.Timestamp('2024-10-28', tz='Europe/Amsterdam').tz_convert('UTC') - pd.Timedelta(minutes=15)
        spine = pd.date_range(start=start_utc, end=end_utc, freq='15min')
        
        with patch('data_ingestion.pd.date_range', return_value=spine):
            data_ingestion.main()
            
    assert written_df is not None
    assert len(written_df) == 100 # 25 hours * 4 quarters = 100 periods exactly

def test_orchestrator_poison_pill():
    conn = sqlite3.connect(':memory:')
    bess_orchestrator.init_db(conn)
    cursor = conn.cursor()
    
    # Inject a poison pill job with negative energy_mwh (impossible for battery)
    cursor.execute('''
        INSERT INTO job_queue (
            job_id, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, 
            lifespan, grid_fee_import, efficiency_store, efficiency_dispatch, 
            depth_of_discharge, degradation_penalty, status
        )
        VALUES ('job_poison', 10, -20, 100, 1000, 0.05, 10, 0.01, 0.9, 0.9, 0.8, 0, 'PENDING')
    ''')
    conn.commit()

    cursor.execute('''CREATE TABLE day_ahead_prices (datetime TEXT, price REAL)''')
    cursor.execute("INSERT INTO day_ahead_prices VALUES ('2024-01-01 00:00:00', 10.0)")
    conn.commit()
    
    with patch('bess_orchestrator.sqlite3.connect', return_value=conn):
        with patch('bess_orchestrator.time.sleep', side_effect=StopLoopException):
            try:
                bess_orchestrator.main()
            except StopLoopException:
                pass

    # Verify that the job's status correctly updates to 'FAILED' instead of crashing
    cursor.execute("SELECT status FROM job_queue WHERE job_id='job_poison'")
    status_row = cursor.fetchone()
    assert status_row is not None
    assert status_row[0] == 'FAILED'

def test_orchestrator_concurrency_lock():
    conn = sqlite3.connect(':memory:')
    bess_orchestrator.init_db(conn)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO job_queue (
            job_id, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, 
            lifespan, grid_fee_import, efficiency_store, efficiency_dispatch, 
            depth_of_discharge, degradation_penalty, status
        )
        VALUES ('job_concurrent', 10, 20, 100, 1000, 0.05, 10, 0.01, 0.9, 0.9, 0.8, 0, 'PENDING')
    ''')
    conn.commit()
    
    # Simulate first worker picking up the job
    cursor.execute('''
        UPDATE job_queue 
        SET status = 'RUNNING' 
        WHERE job_id = (SELECT job_id FROM job_queue WHERE status = 'PENDING' LIMIT 1)
        RETURNING job_id
    ''')
    row1 = cursor.fetchone()
    conn.commit()
    
    # Simulate second worker pulling at the exact same time (after first updated it to RUNNING)
    cursor.execute('''
        UPDATE job_queue 
        SET status = 'RUNNING' 
        WHERE job_id = (SELECT job_id FROM job_queue WHERE status = 'PENDING' LIMIT 1)
        RETURNING job_id
    ''')
    row2 = cursor.fetchone()
    
    assert row1 is not None, "First worker should get the job"
    assert row1[0] == 'job_concurrent'
    assert row2 is None, "Second worker should receive None (locked)"

from unittest.mock import MagicMock

def test_orchestrator_database_locked():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    
    # First call throws locked error, second call throws StopLoopException to break infinite loop
    mock_cursor.execute.side_effect = [sqlite3.OperationalError("database is locked"), StopLoopException]
    
    with patch('bess_orchestrator.sqlite3.connect', return_value=mock_conn):
        with patch('bess_orchestrator.init_db'): # Bypass init_db which would also use the mock
            try:
                bess_orchestrator.main()
            except StopLoopException:
                pass
            
            # The loop should have tried twice. The outer Exception block in main() caught the OperationalError.
            assert mock_cursor.execute.call_count == 2

def test_orchestrator_graceful_shutdown():
    bess_orchestrator.shutdown_requested = False
    bess_orchestrator.signal_handler(None, None)
    
    assert bess_orchestrator.shutdown_requested is True
    
    # Run main() with the flag already True. It should immediately exit gracefully without hanging.
    with patch('bess_orchestrator.sqlite3.connect') as mock_connect:
        bess_orchestrator.main()
