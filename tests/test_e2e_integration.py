import sqlite3
import pandas as pd
import json
import pytest
import os
import threading
import time
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import bess_orchestrator

TEST_DB_PATH = 'test_bess_data.db'
original_connect = sqlite3.connect

def setup_test_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
        
    conn = original_connect(TEST_DB_PATH)
    bess_orchestrator.init_db(conn)
    
    dates = pd.date_range(start="2024-01-01", periods=24, freq='H', tz='Europe/Amsterdam')
    df = pd.DataFrame({
        'datetime': dates.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'price': [50.0] * 24,
        'forecast_price': [50.0] * 24
    })
    df.to_sql('forecasted_market_data', conn, if_exists='replace', index=False)
    conn.commit()
    conn.close()

def teardown_test_db():
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except PermissionError:
            pass

def patched_connect(*args, **kwargs):
    if args and args[0] == 'bess_data.db':
        return original_connect(TEST_DB_PATH)
    return original_connect(*args, **kwargs)

def background_orchestrator_job():
    time.sleep(2)
    conn = original_connect(TEST_DB_PATH)
    bess_orchestrator.process_one_job(conn)
    conn.close()

@patch('sqlite3.connect', side_effect=patched_connect)
def test_e2e_architecture(mock_connect):
    setup_test_db()
    try:
        at = AppTest.from_file("app.py").run(timeout=10)
        
        for ni in at.sidebar.number_input:
            if ni.label == "Power (MW)":
                ni.set_value(50.0)
            elif ni.label == "CAPEX (€/kWh)":
                ni.set_value(120.0)
                
        # Start background thread to process job while Streamlit spins in while loop
        t = threading.Thread(target=background_orchestrator_job, daemon=True)
        t.start()
        
        # Simulate the form submission (will block in polling loop until bg thread finishes)
        at.sidebar.button[0].click().run(timeout=15)
        
        t.join(timeout=10)
        
        conn = original_connect(TEST_DB_PATH)
        cursor = conn.cursor()
        
        # Streamlit AppTest run has finished meaning the loop broke because of COMPLETED status!
        cursor.execute("SELECT job_id, status FROM job_queue WHERE power_mw = 50.0 AND capex_per_kwh = 120.0")
        rows = cursor.fetchall()
        assert len(rows) == 1
        job_id, status = rows[0]
        assert status == 'COMPLETED'
        
        cursor.execute("SELECT metrics_json, dispatch_json FROM job_results WHERE job_id = ?", (job_id,))
        results = cursor.fetchone()
        assert results is not None
        metrics_json_str, dispatch_json_str = results
        
        metrics = json.loads(metrics_json_str)
        dispatch = json.loads(dispatch_json_str)
        assert isinstance(metrics, dict)
        assert isinstance(dispatch, list)
        conn.close()
        
        # UI Rendering Verification
        at.session_state['current_job_id'] = job_id
        at.run()
        
        assert len(at.tabs) > 0
        assert len(at.metric) > 0
        metrics_with_euro = [m for m in at.metric if '€' in str(m.value) or '€' in str(m.label)]
        assert len(metrics_with_euro) > 0
        
    finally:
        teardown_test_db()
