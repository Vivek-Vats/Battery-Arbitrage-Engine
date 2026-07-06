import threading
import sqlite3
import time
import json
from streamlit.testing.v1 import AppTest

DB_PATH = "bess_data.db"

def fail_pending_jobs():
    time.sleep(1)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE job_queue SET status='FAILED' WHERE status='PENDING'")
    conn.commit()
    conn.close()

def test_sidebar_form_submission():
    at = AppTest.from_file("app.py").run()
    
    for ni in at.sidebar.number_input:
        if ni.label == "Power (MW)":
            ni.set_value(55.0)
        elif ni.label == "CAPEX (€/kWh)":
            ni.set_value(120.0)
            
    # Start thread to fail the job
    threading.Thread(target=fail_pending_jobs, daemon=True).start()
    
    # We will try to click the first button in sidebar
    try:
        at.sidebar.button[0].click().run(timeout=3)
    except RuntimeError:
        # Expected if timeout occurs due to polling loop
        pass
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT power_mw, capex_per_kwh FROM job_queue ORDER BY created_at DESC LIMIT 1")
    res = cursor.fetchone()
    conn.close()
    
    assert res is not None
    assert res[0] == 55.0
    assert res[1] == 120.0

def test_kpi_and_plotly_rendering():
    mock_job_id = "mock_job_id_test_kpi"
    
    metrics_data = {"Net_Annual_Profit_EUR": 5000000, "Total_CAPEX_EUR": 1000}
    dispatch_data = [{"datetime": "2023-01-01T00:00:00", "p_dispatch": 10, "p_store": 0, "state_of_charge": 50, "price": 100, "forecast_price": 100}]
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO job_queue (job_id, status, energy_mwh) 
        VALUES (?, 'COMPLETED', 200.0)
    ''', (mock_job_id,))
    cursor.execute('''
        INSERT OR REPLACE INTO job_results (job_id, metrics_json, dispatch_json)
        VALUES (?, ?, ?)
    ''', (mock_job_id, json.dumps(metrics_data), json.dumps(dispatch_data)))
    conn.commit()
    conn.close()
    
    at = AppTest.from_file("app.py").run()
    at.session_state['current_job_id'] = mock_job_id
    at.run()
    
    metric_labels = [m.label for m in at.metric]
    metric_values = [m.value for m in at.metric]
    
    assert "Net Annual Profit" in metric_labels
    profit_index = metric_labels.index("Net Annual Profit")
    assert "5,000,000" in metric_values[profit_index]
    
    # Check for plotly charts via attribute if it exists, otherwise via .get()
    plotly_charts = getattr(at, 'plotly_chart', None)
    if plotly_charts is None:
        plotly_charts = at.get('plotly_chart')
    
    assert len(plotly_charts) > 0
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM job_queue WHERE job_id = ?", (mock_job_id,))
    cursor.execute("DELETE FROM job_results WHERE job_id = ?", (mock_job_id,))
    conn.commit()
    conn.close()
