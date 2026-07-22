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

import plotly.io as pio
pio.templates.default = "plotly_dark"

if 'current_job_id' not in st.session_state:
    st.session_state.current_job_id = None

# Page Configuration
st.set_page_config(page_title="BESS Arbitrage MVP", layout="wide")

# Inject Custom CSS for Premium Glassmorphism Dark Mode
st.markdown("""
    <style>
    /* Dark Theme Background */
    .stApp {
        background-color: #0E1117;
        color: #FAFAFA;
        font-family: 'Inter', sans-serif;
    }
    /* Glassmorphism Metrics */
    div[data-testid="metric-container"] {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        backdrop-filter: blur(10px);
        border-radius: 10px;
        padding: 15px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        transition: transform 0.2s ease-in-out;
    }
    div[data-testid="metric-container"]:hover {
        transform: translateY(-2px);
    }
    /* Headers */
    h1, h2, h3 {
        color: #FFFFFF !important;
        font-weight: 600 !important;
    }
    </style>
""", unsafe_allow_html=True)

DB_PATH = 'bess_data.db'

# Sidebar Form
st.sidebar.header("Job Configuration")
with st.sidebar.form("bess_config_form"):
    power_mw = st.number_input("Power (MW)", min_value=1.0, value=50.0, step=1.0)
    energy_mwh = st.number_input("Energy (MWh)", min_value=1.0, value=200.0, step=1.0)
    capex_per_kwh = st.number_input("CAPEX (€/kWh)", min_value=0.0, value=130.0, step=10.0)
    opex_per_mw = st.number_input("OPEX (€/MW)", min_value=0.0, value=15000.0, step=1000.0)
    wacc = st.number_input("WACC (%)", min_value=0.0, value=4.0, step=0.1)
    lifespan = st.number_input("Lifespan (Years)", min_value=1, value=15, step=1)
    expected_lifespan_cycles = st.number_input("Expected Lifespan (Cycles)", min_value=1000, value=6000, step=500)
    grid_fee_import = st.number_input("Grid Fee Import (€/MWh)", min_value=0.0, value=0.0, step=1.0)
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
        INSERT INTO job_queue (job_id, scenario_name, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, expected_lifespan_cycles, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (job_id, None, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan, expected_lifespan_cycles, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty, 'PENDING'))
    conn.commit()
    conn.close()
    
    # Polling loop
    job_failed = False
    with st.spinner("Solver is running..."):
        max_attempts = 900 # Allow up to 30 minutes for the optimizer to finish
        attempts = 0
        while attempts < max_attempts:
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
            attempts += 1
        else:
            job_failed = True

    if job_failed:
        st.error("Optimization Failed.")
        st.stop()
    else:
        st.success("Optimization Completed!")
        st.session_state.current_job_id = job_id

if st.session_state.current_job_id is not None:


    tab1, tab2 = st.tabs(["Single Scenario Deep-Dive", "Scenario Comparison Matrix"])
    
    with tab1:
        if True:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT jr.metrics_json, jr.dispatch_json, jq.energy_mwh, jq.grid_fee_import, jq.power_mw
                FROM job_results jr 
                JOIN job_queue jq ON jr.job_id = jq.job_id 
                WHERE jr.job_id = ?
            """, (st.session_state.current_job_id,))
            res = cursor.fetchone()
            conn.close()
            
            if res:
                metrics_json, dispatch_json, energy_capacity, grid_fee_import, power_capacity = res
                metrics = json.loads(metrics_json)
                dispatch_data = json.loads(dispatch_json)
                dispatch_df = pd.DataFrame(dispatch_data)
                
                # Parse datetime and drop DST Fall-Back duplicates so Plotly doesn't double-stack them
                dispatch_df['datetime'] = pd.to_datetime(dispatch_df['datetime'])
                dispatch_df = dispatch_df.drop_duplicates(subset=['datetime'], keep='first')
                
                # Backward compatibility for older jobs
                if 'p_dispatch' not in dispatch_df.columns:
                    dispatch_df['p_dispatch'] = dispatch_df.get('DA_Discharge', 0)
                if 'p_store' not in dispatch_df.columns:
                    dispatch_df['p_store'] = dispatch_df.get('DA_Charge', 0)
                
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
                col5, col6, col7, col8, col9, col10 = st.columns(6)
                
                # Calculate annualization factor to scale raw revenues to annual metrics
                ann_factor = (365 * 24) / (len(dispatch_df) * 0.25) if len(dispatch_df) > 0 else 1.0
                
                with col5:
                    st.metric(label="LCOS", value=f"€{metrics.get('LCOS_EUR_per_MWh', 0):.2f} / MWh")
                with col6:
                    st.metric(label="Average Spread", value=f"€{metrics.get('Average_Spread_EUR_per_MWh', 0):.2f} / MWh")
                with col7:
                    st.metric(label="Equivalent Full Cycles", value=f"{metrics.get('Equivalent_Full_Cycles', 0):.1f} Cycles")
                with col8:
                    st.metric(label="Annual Degradation Cost", value=f"€{metrics.get('Annual_Degradation_Cost_EUR', 0):,.2f}")
                with col9:
                    st.metric(label="Annual Market Slippage", value=f"-€{metrics.get('Market_Slippage_Loss_EUR', 0) * ann_factor:,.2f}")
                with col10:
                    st.metric(label="Annual Net Imbalance Alpha", value=f"€{metrics.get('Net_Imbalance_Revenue_EUR', 0) * ann_factor:,.2f}")
                    
                st.write("")
                col_waterfall, col_empty = st.columns([2, 1])
                with col_waterfall:
                    fig = go.Figure(go.Waterfall(
                        name="Net Financial Waterfall",
                        orientation="v",
                        measure=["relative", "relative", "relative", "relative", "relative", "relative", "relative", "total"],
                        x=["Net DA Arbitrage", "Net Imbalance Alpha", "FCR Capacity", "aFRR Capacity", "aFRR Activation", "OPEX", "Degradation", "Net Profit"],
                        y=[
                            metrics.get('Net_DA_Revenue_EUR', 0) * ann_factor, 
                            metrics.get('Net_Imbalance_Revenue_EUR', 0) * ann_factor, 
                            metrics.get('FCR_Capacity_Revenue_EUR', 0) * ann_factor,
                            (metrics.get('Ancillary_Capacity_Revenue_EUR', 0) + metrics.get('Ancillary_Down_Capacity_Revenue_EUR', 0)) * ann_factor,
                            (metrics.get('Net_aFRR_Activation_Revenue_EUR', 0) + metrics.get('Ancillary_Down_Activation_Revenue_EUR', 0)) * ann_factor,
                            -metrics.get('Total_OPEX_EUR', 0), 
                            -metrics.get('Annual_Degradation_Cost_EUR', 0), 
                            metrics.get('Net_Annual_Profit_EUR', 0)
                        ],
                        textposition="outside",
                        connector={"line": {"color": "rgb(63, 63, 63)"}}
                    ))
                    fig.update_layout(title="Annual Net Financial Waterfall", showlegend=False, margin=dict(l=20, r=20, t=60, b=20), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(fig, use_container_width=True)
                    
                st.header("Dispatch Timeseries Visualization")
                
                min_date = dispatch_df['datetime'].min().date()
                max_date = dispatch_df['datetime'].max().date()
                
                date_col1, date_col2, min_col, max_col = st.columns(4)
                with date_col1:
                    start_date = st.date_input("Start Date", value=min_date)
                with date_col2:
                    end_date = st.date_input("End Date", value=max_date)
                with min_col:
                    min_price = st.number_input("Min Price (€/MWh)", value=-100, step=50)
                with max_col:
                    max_price = st.number_input("Max Price (€/MWh)", value=500, step=50)
                
                mask = (dispatch_df['datetime'].dt.date >= start_date) & (dispatch_df['datetime'].dt.date <= end_date)
                filtered_df = dispatch_df.loc[mask]
    
                # Create figure with 3 rows, 1 column, shared X-axes
                fig = make_subplots(
                    rows=3, cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.05,
                    row_heights=[0.35, 0.35, 0.30]
                )
                
                # Row 1: BESS Power Stacked Plot
                if 'DA_Discharge' in filtered_df.columns:
                    da_discharge = filtered_df['DA_Discharge']
                    da_charge = filtered_df['DA_Charge']
                    afrr_up_act = filtered_df.get('aFRR_Up_Activation', 0)
                    afrr_up_res = filtered_df.get('aFRR_Up_Reserve', 0)
                    afrr_up_unact = (afrr_up_res - afrr_up_act).clip(lower=0)
                    
                    afrr_down_act = filtered_df.get('aFRR_Down_Activation', 0)
                    afrr_down_res = filtered_df.get('aFRR_Down_Reserve', 0)
                    afrr_down_unact = (afrr_down_res - afrr_down_act).clip(lower=0)
                    imbalance_discharge = filtered_df.get('Imbalance_Discharge', pd.Series(0.0, index=filtered_df.index))
                    imbalance_charge = filtered_df.get('Imbalance_Charge', pd.Series(0.0, index=filtered_df.index))
                    fcr_res = filtered_df.get('FCR_Reserve', pd.Series(0.0, index=filtered_df.index))

                    # Positive Stack
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=da_discharge, name="DA Arbitrage (Discharge)", marker_color="green", opacity=0.9), row=1, col=1)
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=afrr_up_act, name="aFRR Up (Activated)", marker_color="lightgreen", opacity=0.9), row=1, col=1)
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=imbalance_discharge, name="Imbalance Arbitrage (Discharge)", marker_color="#00F5FF", opacity=0.9), row=1, col=1)
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=afrr_up_unact, name="aFRR Up (Reserved)", marker_color="rgba(144, 238, 144, 0.1)", marker_line_color="lightgreen", marker_line_width=1), row=1, col=1)
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=fcr_res, name="FCR (Reserved)", marker_color="rgba(192, 192, 192, 0.3)", marker_line_color="silver", marker_line_width=1), row=1, col=1)
                    
                    # Negative Stack
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=-da_charge, name="DA Arbitrage (Charge)", marker_color="red", opacity=0.9), row=1, col=1)
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=-afrr_down_act, name="aFRR Down (Activated)", marker_color="lightcoral", opacity=0.9), row=1, col=1)
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=-imbalance_charge, name="Imbalance Arbitrage (Charge)", marker_color="#FF8C00", opacity=0.9), row=1, col=1)
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=-afrr_down_unact, name="aFRR Down (Reserved)", marker_color="rgba(240, 128, 128, 0.1)", marker_line_color="lightcoral", marker_line_width=1), row=1, col=1)
                else:
                    # Legacy Format
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=filtered_df['p_dispatch'], name="Discharging (Sell)", marker_color="green", opacity=0.8), row=1, col=1)
                    fig.add_trace(go.Bar(x=filtered_df['datetime'], y=-filtered_df['p_store'], name="Charging (Buy)", marker_color="red", opacity=0.8), row=1, col=1)

                # Add Max Power Bounds
                fig.add_hline(y=power_capacity, line_dash="dash", line_color="white", annotation_text="Max Power (Discharge)", annotation_position="top right", row=1, col=1)
                fig.add_hline(y=-power_capacity, line_dash="dash", line_color="white", annotation_text="Max Power (Charge)", annotation_position="bottom right", row=1, col=1)
                
                # Row 2: ML Forecast Price as orange dotted line
                fig.add_trace(
                    go.Scatter(
                        x=filtered_df['datetime'], 
                        y=filtered_df['forecast_price'], 
                        name="ML Forecast Price",
                        mode="lines",
                        line=dict(color="orange", dash="dot", width=1)
                    ),
                    row=2, col=1
                )
    
                # Row 2: Electricity Price as blue line
                fig.add_trace(
                    go.Scatter(
                        x=filtered_df['datetime'], 
                        y=filtered_df['price'], 
                        name="Electricity Price",
                        mode="lines",
                        line=dict(color="rgba(0, 0, 255, 0.7)")
                    ),
                    row=2, col=1
                )

                # Row 3: Battery State of Charge (%) as filled area chart (purple)
                fig.add_trace(
                    go.Scatter(
                        x=filtered_df['datetime'], 
                        y=filtered_df['soc_percentage'], 
                        name="State of Charge",
                        mode="lines",
                        line=dict(color="purple"),
                        fill='tozeroy'
                    ),
                    row=3, col=1
                )
                
                # Set layout properties
                fig.update_layout(
                    hovermode="x unified",
                    barmode="relative",
                    height=750,
                    dragmode='pan',
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                
                # Configure X-axes and rangeslider
                fig.update_xaxes(title_text="", showticklabels=False, rangeselector=dict(buttons=list([dict(count=7, label="1W", step="day", stepmode="backward"), dict(count=1, label="1M", step="month", stepmode="backward"), dict(count=3, label="3M", step="month", stepmode="backward"), dict(count=6, label="6M", step="month", stepmode="backward"), dict(step="all", label="1Y")]), bgcolor="#333333", activecolor="#555555"), row=1, col=1)
                fig.update_xaxes(title_text="", showticklabels=False, row=2, col=1)
                fig.update_xaxes(title_text="", row=3, col=1)
                
                # Configure Y-axes
                fig.update_yaxes(title_text="BESS Power (MW)", fixedrange=True, row=1, col=1)
                fig.update_yaxes(title_text="Price (€/MWh)", range=[min_price, max_price], fixedrange=True, row=2, col=1)
                fig.update_yaxes(title_text="State of Charge (%)", range=[0, 100], fixedrange=True, row=3, col=1)
                
                st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'displayModeBar': True, 'modeBarButtonsToRemove': ['zoom2d', 'zoom']})
            
                st.subheader("Macro Analytics: Financial & Physical Performance")
                
                # DA Arbitrage: Discharge Revenue - Tank Cost of Goods Sold (for that discharge)
                dispatch_df['DA_Profit_EUR'] = dispatch_df.get('DA_Discharge', 0) * dispatch_df.get('da_price_actual', 0) * 0.25 - dispatch_df.get('DA_COGS_EUR', 0)
                
                # Imbalance Alpha: Discharge Revenue - Tank Cost of Goods Sold
                dispatch_df['Imbalance_Profit_EUR'] = dispatch_df.get('Imbalance_Discharge', 0) * dispatch_df.get('price', 0) * 0.25 - dispatch_df.get('Imbalance_COGS_EUR', 0)
                
                # aFRR Activation Profit: Revenue - Tank COGS + Down Activation Revenue (which has 0 COGS)
                afrr_up_act_revenue = dispatch_df.get('aFRR_Up_Activation', 0) * dispatch_df.get('afrr_up_activation_price_mwh', 0) * 0.25
                afrr_down_act_revenue = (-dispatch_df.get('aFRR_Down_Activation', 0) * (dispatch_df.get('afrr_down_activation_price_mwh', 0) + grid_fee_import)) * 0.25
                dispatch_df['aFRR_Profit_EUR'] = afrr_up_act_revenue - dispatch_df.get('aFRR_COGS_EUR', 0) + afrr_down_act_revenue
                
                # Capacity revenues (already correctly multiplied by 0.25 in previous iterations but let's be careful)
                tier1_rev = dispatch_df.get('aFRR_Up_Reserve_Tier1', 0) * dispatch_df.get('afrr_up_price_mw', 0) * 1.0 * 0.25
                tier2_rev = dispatch_df.get('aFRR_Up_Reserve_Tier2', 0) * dispatch_df.get('afrr_up_price_mw', 0) * 0.6 * 0.25
                tier3_rev = dispatch_df.get('aFRR_Up_Reserve_Tier3', 0) * dispatch_df.get('afrr_up_price_mw', 0) * 0.1 * 0.25
                dispatch_df['aFRR_Capacity_Revenue_EUR'] = tier1_rev + tier2_rev + tier3_rev + (dispatch_df.get('aFRR_Down_Reserve', 0) * dispatch_df.get('afrr_down_price_mw', 0) * 0.25)
                
                dispatch_df['FCR_Capacity_Revenue_EUR'] = dispatch_df.get('FCR_Reserve', 0) * dispatch_df.get('fcr_price_eur_mw', 0) * 0.25
                
                macro_df = dispatch_df.copy()
                macro_df['year_month'] = macro_df['datetime'].dt.to_period('M').astype(str)
                macro_df['date'] = macro_df['datetime'].dt.date
                
                # Plot 1: Monthly Revenue Stream Breakdown
                monthly_profit = macro_df.groupby('year_month').agg({
                    'DA_Profit_EUR': 'sum',
                    'Imbalance_Profit_EUR': 'sum',
                    'aFRR_Capacity_Revenue_EUR': 'sum',
                    'FCR_Capacity_Revenue_EUR': 'sum',
                    'aFRR_Profit_EUR': 'sum'
                }).reset_index()
                
                fig_rev = go.Figure()
                fig_rev.add_trace(go.Bar(x=monthly_profit['year_month'], y=monthly_profit['DA_Profit_EUR'], name='DA Arbitrage (Net)', marker_color='green'))
                fig_rev.add_trace(go.Bar(x=monthly_profit['year_month'], y=monthly_profit['Imbalance_Profit_EUR'], name='Imbalance Alpha (Net)', marker_color='#00F5FF'))
                fig_rev.add_trace(go.Bar(x=monthly_profit['year_month'], y=monthly_profit['aFRR_Capacity_Revenue_EUR'], name='aFRR Capacity', marker_color='gold'))
                fig_rev.add_trace(go.Bar(x=monthly_profit['year_month'], y=monthly_profit['FCR_Capacity_Revenue_EUR'], name='FCR Capacity', marker_color='silver'))
                fig_rev.add_trace(go.Bar(x=monthly_profit['year_month'], y=monthly_profit['aFRR_Profit_EUR'], name='aFRR Activation', marker_color='lightblue'))
                fig_rev.update_layout(title="Monthly Net Revenue Breakdown", barmode='relative', xaxis_title="Month", yaxis_title="Net Profit (€)", height=400, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig_rev, use_container_width=True)
                
                # Plot 2: Cumulative Battery Wear (EFC) Tracker
                daily_stats = macro_df.groupby('date').agg({
                    'p_dispatch': 'sum'
                }).reset_index()
                daily_stats['total_discharge_mwh'] = daily_stats['p_dispatch'] * 0.25
                daily_stats['efc'] = daily_stats['total_discharge_mwh'] / energy_capacity
                daily_stats['cumulative_efc'] = daily_stats['efc'].cumsum()
                
                # Calculate expected daily wear budget
                expected_cycles = metrics.get('Expected_Lifespan_Cycles', 6000)
                expected_years = metrics.get('Expected_Lifespan_Years', 15)
                daily_budget = expected_cycles / (expected_years * 365) if expected_years > 0 else 1.0
                daily_stats['budget_efc'] = [i * daily_budget for i in range(1, len(daily_stats) + 1)]
                
                fig_cycle = go.Figure()
                fig_cycle.add_trace(go.Scatter(
                    x=daily_stats['date'], y=daily_stats['cumulative_efc'], 
                    name='Actual Accumulated Wear', 
                    mode='lines', fill='tozeroy', 
                    line=dict(color='crimson', width=3)
                ))
                fig_cycle.add_trace(go.Scatter(
                    x=daily_stats['date'], y=daily_stats['budget_efc'], 
                    name='Lifespan Budget Trajectory', 
                    mode='lines', 
                    line=dict(color='white', width=2, dash='dash')
                ))
                
                fig_cycle.update_layout(
                    title="Cumulative Battery Wear (EFC) vs Hardware Budget", 
                    xaxis_title="Date", 
                    yaxis_title="Cumulative Equivalent Full Cycles (EFC)", 
                    height=400, 
                    paper_bgcolor='rgba(0,0,0,0)', 
                    plot_bgcolor='rgba(0,0,0,0)',
                    hovermode='x unified'
                )
                st.plotly_chart(fig_cycle, use_container_width=True)
                
                # Plot 3: Captured Price Distribution
                captured_mask = macro_df['p_dispatch'] > 0
                captured_prices = macro_df[captured_mask]['price']
                all_prices = macro_df['price']
                
                fig_dist = go.Figure()
                fig_dist.add_trace(go.Histogram(x=all_prices, name='All Market Prices', opacity=0.5, marker_color='gray', nbinsx=100))
                fig_dist.add_trace(go.Histogram(x=captured_prices, name='Captured Prices (Discharge)', opacity=0.75, marker_color='orange', nbinsx=100))
                fig_dist.update_layout(barmode='overlay', title="Captured Market Price Distribution", xaxis_title="Price (€/MWh)", yaxis_title="Frequency", height=400, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                p1 = all_prices.quantile(0.01)
                p99 = all_prices.quantile(0.99)
                fig_dist.update_xaxes(range=[p1, p99])
                st.plotly_chart(fig_dist, use_container_width=True)

                st.divider()
                st.subheader("Save this Scenario for Comparison")
                col_name, col_btn = st.columns([3, 1])
                with col_name:
                    save_name = st.text_input("Scenario Name", key="save_scenario_input")
                with col_btn:
                    if st.button("Save Scenario"):
                        if save_name:
                            conn = sqlite3.connect(DB_PATH)
                            cursor = conn.cursor()
                            cursor.execute("UPDATE job_queue SET scenario_name = ? WHERE job_id = ?", (save_name, st.session_state.current_job_id))
                            conn.commit()
                            conn.close()
                            st.success("Scenario saved successfully!")
                        else:
                            st.error("Please enter a scenario name.")
            else:
                st.error("No results found for this job.")


    with tab2:
        if True:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT job_id, scenario_name FROM job_queue WHERE status='COMPLETED' AND scenario_name IS NOT NULL AND scenario_name != '' ORDER BY created_at DESC")
            saved_scenarios = cursor.fetchall()
            conn.close()
            
            if saved_scenarios:
                scenario_options = {row[1]: row[0] for row in saved_scenarios}
                selected_compare_labels = st.multiselect("Select Scenarios to Compare", options=list(scenario_options.keys()))
                
                if len(selected_compare_labels) >= 2:
                    compare_job_ids = [scenario_options[label] for label in selected_compare_labels]
                    
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    placeholders = ','.join('?' * len(compare_job_ids))
                    cursor.execute(f"""
                        SELECT jq.job_id, jq.scenario_name, jr.metrics_json 
                        FROM job_results jr 
                        JOIN job_queue jq ON jr.job_id = jq.job_id 
                        WHERE jq.job_id IN ({placeholders})
                    """, tuple(compare_job_ids))
                    compare_res = cursor.fetchall()
                    conn.close()
                    
                    metrics_dict = {}
                    profit_data = []
                    deg_cost_data = []
                    scenario_names_ordered = []
                    
                    for label in selected_compare_labels:
                        job_id_target = scenario_options[label]
                        row = next((r for r in compare_res if r[0] == job_id_target), None)
                        if row:
                            c_job_id, c_scen_name, c_metrics_json = row
                            c_metrics = json.loads(c_metrics_json)
                            # Instead of assigning full c_metrics which makes keys rows, let's use c_metrics directly
                            metrics_dict[label] = c_metrics
                            profit_data.append(c_metrics.get('Net_Annual_Profit_EUR', 0))
                            deg_cost_data.append(c_metrics.get('Annual_Degradation_Cost_EUR', 0))
                            scenario_names_ordered.append(label)
                    
                    compare_df = pd.DataFrame(metrics_dict)
                    st.dataframe(compare_df, use_container_width=True)
                    
                    fig2 = go.Figure(data=[
                        go.Bar(name='Net Annual Profit (€)', x=scenario_names_ordered, y=profit_data),
                        go.Bar(name='Annual Degradation Cost (€)', x=scenario_names_ordered, y=deg_cost_data)
                    ])
                    fig2.update_layout(barmode='group', title="Financial Comparison")
                    st.plotly_chart(fig2, use_container_width=True)
                elif len(selected_compare_labels) == 1:
                    st.info("Select at least one more scenario to compare.")
                else:
                    st.info("Select 2 or more scenarios to view the comparison matrix.")
            else:
                st.info("No saved scenarios available for comparison. Please save a scenario in Tab 1.")
                
            st.divider()
            st.subheader("Manage Saved Scenarios")
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT scenario_name FROM job_queue WHERE status='COMPLETED' AND scenario_name IS NOT NULL")
            
            col_del_name, col_del_btn = st.columns([3, 1])
            with col_del_name:
                scenario_to_delete = st.selectbox("Select scenario to remove", options=[row[0] for row in cursor.fetchall()])
            with col_del_btn:
                if st.button("Remove Scenario"):
                    cursor.execute("UPDATE job_queue SET scenario_name = NULL WHERE scenario_name = ?", (scenario_to_delete,))
                    conn.commit()
                    conn.close()
                    st.rerun()
            conn.close()

