import pytest
import bess_orchestrator
import sqlite3
import unittest.mock
import json
import pandas as pd
import re

def test_job_lifecycle():
    # 1. Use sqlite3.connect(':memory:') to create a temporary database.
    conn = sqlite3.connect(':memory:')
    
    # 2. Initialize the tables using bess_orchestrator.init_db and manually insert a mock forecasted_market_data table.
    bess_orchestrator.init_db(conn)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS forecasted_market_data (
            datetime TEXT PRIMARY KEY,
            price REAL,
            forecast_price REAL
        )
    ''')
    cursor.execute("INSERT INTO forecasted_market_data (datetime, price, forecast_price) VALUES ('2023-01-01T00:00:00Z', 50.0, 50.0)")
    cursor.execute("INSERT INTO forecasted_market_data (datetime, price, forecast_price) VALUES ('2023-01-01T01:00:00Z', 40.0, 40.0)")
    conn.commit()

    # 3. Insert a mock job into job_queue with status = 'PENDING', ensuring all current schema columns are included
    cursor.execute('''
        INSERT INTO job_queue (
            job_id, scenario_name, power_mw, energy_mwh, capex_per_kwh, opex_per_mw,
            wacc, lifespan, grid_fee_import, efficiency_store, efficiency_dispatch,
            depth_of_discharge, degradation_penalty, expected_lifespan_cycles, status
        ) VALUES (
            'test_job_1', 'test_scenario', 10.0, 20.0, 150.0, 5000.0,
            0.05, 15.0, 0.0, 0.9, 0.9,
            0.95, 0.0, 5000.0, 'PENDING'
        )
    ''')
    conn.commit()

    mock_dispatch_df = pd.DataFrame({
        'dispatch': [10.0, -10.0]
    }, index=pd.to_datetime(['2023-01-01 00:00:00', '2023-01-01 01:00:00']))
    
    mock_metrics = {'npv': 1000, 'irr': 0.1}

    # 4. Call a refactored, single-iteration version of the orchestrator loop.
    with unittest.mock.patch('bess_orchestrator.quant_engine.optimize_dispatch', return_value=mock_dispatch_df), \
         unittest.mock.patch('bess_orchestrator.finance_engine.calculate_financials', return_value=mock_metrics):
        success = bess_orchestrator.process_one_job(conn)
        assert success is True

    # 5. Assert that the job_queue status successfully updates to 'COMPLETED'.
    cursor.execute("SELECT status FROM job_queue WHERE job_id = 'test_job_1'")
    status = cursor.fetchone()[0]
    assert status == 'COMPLETED'

    # 6. Assert that job_results contains valid, parsable JSON for both the metrics and dispatch data.
    cursor.execute("SELECT metrics_json, dispatch_json FROM job_results WHERE job_id = 'test_job_1'")
    row = cursor.fetchone()
    assert row is not None
    metrics_json_str, dispatch_json_str = row
    
    metrics_data = json.loads(metrics_json_str)
    dispatch_data = json.loads(dispatch_json_str)
    
    assert metrics_data['npv'] == 1000
    assert len(dispatch_data) == 2


def test_timezone_serialization():
    conn = sqlite3.connect(':memory:')
    bess_orchestrator.init_db(conn)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS day_ahead_prices (
            datetime TEXT PRIMARY KEY,
            price_eur_per_mwh REAL
        )
    ''')
    cursor.execute("INSERT INTO day_ahead_prices (datetime, price_eur_per_mwh) VALUES ('2023-01-01T00:00:00Z', 50.0)")
    conn.commit()

    cursor.execute('''
        INSERT INTO job_queue (
            job_id, scenario_name, power_mw, energy_mwh, capex_per_kwh, opex_per_mw,
            wacc, lifespan, grid_fee_import, efficiency_store, efficiency_dispatch,
            depth_of_discharge, degradation_penalty, expected_lifespan_cycles, status
        ) VALUES (
            'test_job_2', 'test_scenario', 10.0, 20.0, 150.0, 5000.0,
            0.05, 15.0, 0.0, 0.9, 0.9,
            0.95, 0.0, 5000.0, 'PENDING'
        )
    ''')
    conn.commit()

    # Create mock dispatch dataframe with a UTC timestamp
    mock_dispatch_df = pd.DataFrame({
        'dispatch': [10.0]
    }, index=pd.to_datetime(['2023-01-01 00:00:00']))
    
    mock_metrics = {'npv': 1000}

    with unittest.mock.patch('bess_orchestrator.quant_engine.optimize_dispatch', return_value=mock_dispatch_df), \
         unittest.mock.patch('bess_orchestrator.finance_engine.calculate_financials', return_value=mock_metrics):
        bess_orchestrator.process_one_job(conn)

    cursor.execute("SELECT dispatch_json FROM job_results WHERE job_id = 'test_job_2'")
    dispatch_json_str = cursor.fetchone()[0]
    dispatch_data = json.loads(dispatch_json_str)
    
    # Assert output dispatch_json contains timestamps explicitly formatted in standard ISO strings (e.g., %Y-%m-%dT%H:%M:%S)
    # and that they are correctly localized to Europe/Amsterdam
    timestamp = dispatch_data[0]['datetime']
    
    # Assert the format is ISO string
    assert re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$', timestamp), f"Timestamp {timestamp} is not in correct ISO format"
    
    # Assert localization is correct (2023-01-01 00:00:00 UTC -> 2023-01-01 01:00:00 CET/Europe/Amsterdam)
    assert timestamp == '2023-01-01T01:00:00'
