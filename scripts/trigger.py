import sqlite3
import uuid

conn = sqlite3.connect('bess_data.db')
c = conn.cursor()
job_id = str(uuid.uuid4())
c.execute("""
    INSERT INTO job_queue 
    (job_id, scenario_name, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, expected_lifespan_cycles, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty, status) 
    VALUES (?, 'Verification Test', 10.0, 20.0, 200, 15000, 0.08, 15, 6000, 15.0, 0.9, 0.9, 1.0, 50.0, 'PENDING')
""", (job_id,))
conn.commit()
conn.close()
print("Job inserted:", job_id)
