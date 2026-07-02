import sys
sys.path.append('.')
from unittest.mock import patch, MagicMock
import sqlite3
import bess_orchestrator

class StopLoopException(Exception):
    pass

def test_orchestrator_job_state():
    conn = sqlite3.connect(':memory:')
    
    bess_orchestrator.init_db(conn)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO job_queue (job_id, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty, status)
        VALUES ('job_1', 10, 20, 100, 1000, 0.05, 10, 0.01, 0.9, 0.9, 0.8, 0, 'PENDING')
    ''')
    conn.commit()

    cursor.execute('''CREATE TABLE day_ahead_prices (datetime TEXT, price REAL)''')
    cursor.execute("INSERT INTO day_ahead_prices VALUES ('2024-01-01 00:00:00', 10.0)")
    cursor.execute("INSERT INTO day_ahead_prices VALUES ('2024-01-01 00:15:00', 12.0)")
    conn.commit()
    
    with patch('bess_orchestrator.sqlite3.connect') as mock_connect:
        mock_connect.return_value = conn
        with patch('bess_orchestrator.time.sleep', side_effect=StopLoopException):
            try:
                bess_orchestrator.main()
            except StopLoopException:
                pass

    cursor.execute("SELECT status FROM job_queue WHERE job_id='job_1'")
    row = cursor.fetchone()
    print("Row:", row)

test_orchestrator_job_state()
