import pytest
from unittest.mock import patch
import pandas as pd
import sqlite3
import data_ingestion
import bess_orchestrator

class StopLoopException(BaseException):
    """Exception to break out of the orchestrator's infinite polling loop."""
    pass



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

    # The orchestrator needs forecasted_market_data data for domain execution
    cursor.execute('''CREATE TABLE forecasted_market_data (datetime TEXT, price REAL, forecast_price REAL)''')
    cursor.execute("INSERT INTO forecasted_market_data VALUES ('2024-01-01 00:00:00', 10.0, 10.0)")
    cursor.execute("INSERT INTO forecasted_market_data VALUES ('2024-01-01 00:15:00', 12.0, 12.0)")
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

    cursor.execute('''CREATE TABLE forecasted_market_data (datetime TEXT, price REAL, forecast_price REAL)''')
    cursor.execute("INSERT INTO forecasted_market_data VALUES ('2024-01-01 00:00:00', 10.0, 10.0)")
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
