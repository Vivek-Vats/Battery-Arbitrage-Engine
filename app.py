# Execution Comments:
# Local Launch: streamlit run app.py
# Public Tunnel Exposure: cloudflared tunnel --url http://localhost:8501
# Alternatively: ngrok http 8501

import streamlit as st
import pandas as pd
import sqlite3
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import uuid
import time

# Page Configuration
st.set_page_config(page_title="BESS Arbitrage MVP", layout="wide")
st.title("Phase 6.0 BESS Arbitrage MVP Dashboard")

DB_PATH = 'bess_data.db'

# Sidebar Form
st.sidebar.header("Job Configuration")
with st.sidebar.form("bess_config_form"):
    power_mw = st.number_input("Power (MW)", min_value=1.0, value=100.0, step=1.0)
    energy_mwh = st.number_input("Energy (MWh)", min_value=1.0, value=200.0, step=1.0)
    capex_per_kwh = st.number_input("CAPEX (€/kWh)", min_value=0.0, value=180.0, step=10.0)
    opex_per_mw = st.number_input("OPEX (€/MW)", min_value=0.0, value=15000.0, step=1000.0)
    wacc = st.number_input("WACC (%)", min_value=0.0, value=7.0, step=0.1)
    lifespan = st.number_input("Lifespan (Years)", min_value=1, value=15, step=1)
    expected_lifespan_cycles = st.number_input("Expected Lifespan (Cycles)", min_value=1000, value=6000, step=500)
    grid_fee_import = st.number_input("Grid Fee Import (€/MWh)", min_value=0.0, value=15.0, step=1.0)
    efficiency_store = st.number_input("Charge Efficiency (%)", value=93.0, max_value=100.0)
    efficiency_dispatch = st.number_input("Discharge Efficiency (%)", value=93.0, max_value=100.0)
    depth_of_discharge = st.number_input("Depth of Discharge (%)", value=90.0, max_value=100.0)
    degradation_penalty = st.number_input("Degradation Penalty (€/MWh)", min_value=0.0, value=5.0)
    submit_button = st.form_submit_button("Run Dispatch Optimization")

if submit_button:
    job_id = str(uuid.uuid4())
    
    # Insert pending job
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO job_queue (job_id, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, expected_lifespan_cycles, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (job_id, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, expected_lifespan_cycles, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty, 'PENDING'))
    conn.commit()
    conn.close()
    
    # Polling loop
    job_failed = False
    with st.spinner("Solver is running..."):
        while True:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM job_queue WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                status = row[0]
                if status == 'COMPLETED':
                    break
                elif status == 'FAILED':
                    job_failed = True
                    break
            time.sleep(2)

    if job_failed:
        st.error("Optimization Failed.")
        st.stop()
    else:
        st.success("Optimization Completed!")

    # Fetch results
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT jr.metrics_json, jr.dispatch_json, jq.energy_mwh 
        FROM job_results jr 
        JOIN job_queue jq ON jr.job_id = jq.job_id 
        WHERE jr.job_id = ?
    """, (job_id,))
    res = cursor.fetchone()
    conn.close()
    
    if res:
        metrics_json, dispatch_json, energy_capacity = res
        metrics = json.loads(metrics_json)
        dispatch_data = json.loads(dispatch_json)
        dispatch_df = pd.DataFrame(dispatch_data)
        
        # Parse datetime
        dispatch_df['datetime'] = pd.to_datetime(dispatch_df['datetime'])
        
        # Calculate Power State: discharging is positive, charging is negative
        dispatch_df['power_state'] = dispatch_df['p_dispatch'] - dispatch_df['p_store']
        
        # Calculate State of Charge as a percentage
        dispatch_df['soc_percentage'] = (dispatch_df['state_of_charge'] / energy_capacity) * 100
        

        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(label="Total CAPEX", value=f"€{metrics.get('Total_CAPEX_EUR', 0):,.2f}")
        with col2:
            st.metric(label="Expected Lifespan", value=f"{metrics.get('Expected_Lifespan_Years', 0):.1f} Years")
        with col3:
            st.metric(label="Net Annual Profit", value=f"€{metrics.get('Net_Annual_Profit_EUR', 0):,.2f}")
        with col4:
            st.metric(label="Annual ROI", value=f"{metrics.get('Annual_ROI_Percentage', 0):.2f}%")
            
        st.write("")
        col5, col6, col7, col8 = st.columns(4)
        
        with col5:
            st.metric(label="LCOS", value=f"€{metrics.get('LCOS_EUR_per_MWh', 0):.2f} / MWh")
        with col6:
            st.metric(label="Average Spread", value=f"€{metrics.get('Average_Spread_EUR_per_MWh', 0):.2f} / MWh")
        with col7:
            st.metric(label="Equivalent Full Cycles", value=f"{metrics.get('Equivalent_Full_Cycles', 0):.1f} Cycles")
        with col8:
            st.metric(label="Annual Degradation Cost", value=f"€{metrics.get('Annual_Degradation_Cost_EUR', 0):,.2f}")
            

        
        # Create figure with 2 rows, 1 column, shared X-axes
        # Top row: Dispatch Power only
        # Bottom row: Electricity Price and State of Charge (dual axis)
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            specs=[[{"secondary_y": False}], [{"secondary_y": True}]],
            vertical_spacing=0.1
        )
        
        # Row 1: p_dispatch (selling) as Green bars
        fig.add_trace(
            go.Bar(
                x=dispatch_df['datetime'], 
                y=dispatch_df['p_dispatch'], 
                name="Discharging (Sell)",
                marker_color="green",
                opacity=0.8
            ),
            row=1, col=1
        )

        # Row 1: p_store (buying) as Red bars (negative)
        fig.add_trace(
            go.Bar(
                x=dispatch_df['datetime'], 
                y=-dispatch_df['p_store'], 
                name="Charging (Buy)",
                marker_color="red",
                opacity=0.8
            ),
            row=1, col=1
        )
        
        # Row 2: Electricity Price as blue line on primary Y-axis
        fig.add_trace(
            go.Scatter(
                x=dispatch_df['datetime'], 
                y=dispatch_df['price'], 
                name="Electricity Price",
                mode="lines",
                line=dict(color="rgba(0, 0, 255, 0.7)")
            ),
            row=2, col=1, secondary_y=False
        )

        # Row 2: Battery State of Charge (%) as filled area chart (purple) on secondary Y-axis
        fig.add_trace(
            go.Scatter(
                x=dispatch_df['datetime'], 
                y=dispatch_df['soc_percentage'], 
                name="State of Charge",
                mode="lines",
                line=dict(color="purple"),
                fill='tozeroy'
            ),
            row=2, col=1, secondary_y=True
        )
        
        # Set layout properties
        fig.update_layout(
            hovermode="x unified",
            barmode="relative",
            height=750,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        # Configure X-axes and rangeslider
        fig.update_xaxes(title_text="", showticklabels=False, row=1, col=1)
        fig.update_xaxes(
            title_text="", 
            rangeslider=dict(visible=True, thickness=0.05),
            tickformat="Week %V",
            tickformatstops=[
                dict(dtickrange=[604800000, None], value=""),
                dict(dtickrange=[None, 604800000], value="%b %d, %H:%M")
            ],
            row=2, col=1
        )
        
        # Configure Y-axes
        fig.update_yaxes(title_text="BESS Power (MW)", row=1, col=1)
        fig.update_yaxes(title_text="Electricity Price (€ / MWh)", row=2, col=1, secondary_y=False)
        fig.update_yaxes(title_text="State of Charge (%)", range=[0, 100], row=2, col=1, secondary_y=True)
        
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error("No results found for this job.")
else:
    st.info("Submit the form to run the dispatch optimization.")
