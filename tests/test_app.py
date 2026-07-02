import pytest
from streamlit.testing.v1 import AppTest
import sqlite3
import os

# --- HOTFIX FOR STREAMLIT APPTEST BUG ---
# Streamlit AppTest leaks thread-local form context stacks across reruns (like button clicks)
# causing an erroneous "Forms cannot be nested in other forms" exception. 
# We monkey-patch the internal validation to bypass this framework bug.
import streamlit.elements.form
streamlit.elements.form.is_in_form = lambda *args, **kwargs: False
# ----------------------------------------

def test_sidebar_defaults():
    # Initialize the app
    at = AppTest.from_file("app.py").run()
    
    # Assert that the Power (MW) input correctly matches the 100.0 default
    assert at.sidebar.number_input[0].value == 100.0
    
    # Assert that the CAPEX input correctly matches the 180.0 default
    # Looking at app.py, CAPEX is the 3rd number_input (index 2)
    assert at.sidebar.number_input[2].value == 180.0


from unittest.mock import patch, MagicMock
import json

def test_empty_state_rendering():
    at = AppTest.from_file("app.py").run()
    
    # Assert info banner is present and metrics/charts are absent
    assert len(at.info) > 0
    assert "Submit the form to run the dispatch optimization." in at.info[0].value
    assert len(at.metric) == 0

@patch('app.sqlite3.connect')
@patch('app.uuid.uuid4', return_value='test_failed_job')
def test_failed_job_rendering(mock_uuid, mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    
    # The first execute is the INSERT. The second is the SELECT polling.
    # We mock fetchone() to return 'FAILED' immediately
    mock_cursor.fetchone.return_value = ('FAILED',)
    mock_connect.return_value = mock_conn

    at = AppTest.from_file("app.py").run()
    
    # Simulate a button click
    at.sidebar.button[0].click().run(timeout=3)
    
    # Assert that the error banner appears
    assert len(at.error) > 0
    assert "Optimization Failed." in at.error[0].value

@patch('app.sqlite3.connect')
@patch('app.uuid.uuid4', return_value='test_completed_job')
def test_end_to_end_kpi_rendering(mock_uuid, mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    
    mock_metrics = {
        "Annual_Gross_Revenue_EUR": 1000000.0,
        "LCOS_EUR_per_MWh": 50.0,
        "Simple_Payback_Years": 4.5,
        "Annual_ROI_Percentage": 15.0,
        "Equivalent_Full_Cycles": 300,
        "Annual_Degradation_Cost_EUR": 1500.0,
        "Expected_Lifespan_Years": 20.0
    }
    
    mock_dispatch = [
        {"datetime": "2024-01-01T00:00:00", "p_dispatch": 10, "p_store": 0, "state_of_charge": 5, "price": 100.0},
        {"datetime": "2024-01-01T01:00:00", "p_dispatch": 0, "p_store": 10, "state_of_charge": 15, "price": -10.0}
    ]
    
    # side_effect for fetchone():
    # 1. First fetchone inside the polling loop: return ('COMPLETED',)
    # 2. Second fetchone to fetch results: return (metrics_json, dispatch_json, energy_capacity)
    mock_cursor.fetchone.side_effect = [
        ('COMPLETED',),
        (json.dumps(mock_metrics), json.dumps(mock_dispatch), 20.0)
    ]
    mock_connect.return_value = mock_conn

    at = AppTest.from_file("app.py").run()
    
    # Simulate a button click
    at.sidebar.button[0].click().run(timeout=3)
    
    # Verify the UI renders exactly 8 metrics
    assert len(at.metric) == 8
    
    # Verify metric labels and values (Expected Lifespan is now index 1)
    assert at.metric[1].label == "Expected Lifespan"
    assert at.metric[1].value == "20.0 Years"
    
    assert at.metric[3].label == "Annual ROI"
    assert at.metric[3].value == "15.00%"
