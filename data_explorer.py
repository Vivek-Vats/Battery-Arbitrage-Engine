import streamlit as st
import pandas as pd
import sqlite3
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os

st.set_page_config(page_title="Data Explorer & Verification", layout="wide", page_icon="🔍")

st.title("Data Explorer & Verification")
st.markdown("Visually inspect and correlate raw exogenous inputs alongside the actual optimization outputs.")

@st.cache_data(ttl=60)
def load_data():
    db_path = 'bess_data.db'
    if not os.path.exists(db_path):
        st.error(f"Database {db_path} not found.")
        return pd.DataFrame()
        
    try:
        import io
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get the most recently completed job
        cursor.execute("SELECT job_id FROM job_queue WHERE status='COMPLETED' ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
        if not row:
            st.warning("No completed jobs found in the database.")
            conn.close()
            return pd.DataFrame()
            
        latest_job_id = row[0]
        
        # Extract the dispatch_json for the latest job
        cursor.execute("SELECT dispatch_json FROM job_results WHERE job_id = ?", (latest_job_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result or not result[0]:
            st.warning("No dispatch data found for the latest job.")
            return pd.DataFrame()
            
        dispatch_json = result[0]
        merged_df = pd.read_json(io.StringIO(dispatch_json), orient='records')
        
        # Ensure datetime is correct type and sort
        merged_df['datetime'] = pd.to_datetime(merged_df['datetime'])
        merged_df = merged_df.sort_values('datetime').reset_index(drop=True)
        merged_df.fillna(0, inplace=True)
        
        return merged_df
        
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return pd.DataFrame()

df = load_data()

if df.empty:
    st.stop()

# Sidebar UI
st.sidebar.header("Explorer Settings")

min_date = df['datetime'].min().date()
max_date = df['datetime'].max().date()

start_date = st.sidebar.date_input("Start Date", value=min_date)
end_date = st.sidebar.date_input("End Date", value=max_date)

# Filter Data by Date
mask = (df['datetime'].dt.date >= start_date) & (df['datetime'].dt.date <= end_date)
filtered_df = df.loc[mask]

# Column selection
all_columns = [col for col in df.columns if col != 'datetime']
all_columns.sort()

st.sidebar.subheader("Plotly Axes")
primary_cols = st.sidebar.multiselect("Primary Y-Axis (Lines)", options=all_columns, default=[])
secondary_cols = st.sidebar.multiselect("Secondary Y-Axis (Bars)", options=all_columns, default=[])

if not primary_cols and not secondary_cols:
    st.info("Please select at least one column from the sidebar to visualize.")
    st.stop()

# Plotting
fig = make_subplots(specs=[[{"secondary_y": True}]])

colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
color_idx = 0

# Add primary axis traces (Lines)
for col in primary_cols:
    fig.add_trace(
        go.Scatter(
            x=filtered_df['datetime'],
            y=filtered_df[col],
            name=f"{col} (Primary)",
            mode='lines',
            line=dict(color=colors[color_idx % len(colors)], width=2)
        ),
        secondary_y=False
    )
    color_idx += 1

# Add secondary axis traces (Bars)
for col in secondary_cols:
    fig.add_trace(
        go.Bar(
            x=filtered_df['datetime'],
            y=filtered_df[col],
            name=f"{col} (Secondary)",
            marker_color=colors[color_idx % len(colors)],
            opacity=0.6
        ),
        secondary_y=True
    )
    color_idx += 1

fig.update_layout(
    title="Interactive Data Timeline",
    hovermode="x unified",
    barmode="group",
    height=700,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=20, r=20, t=60, b=20),
)

fig.update_xaxes(title_text="Datetime")
fig.update_yaxes(title_text="Primary Axis", secondary_y=False)
fig.update_yaxes(title_text="Secondary Axis", secondary_y=True)

st.plotly_chart(fig, use_container_width=True)

# Data Table View
with st.expander("View Raw Data"):
    selected_cols = ['datetime'] + list(set(primary_cols + secondary_cols))
    st.dataframe(filtered_df[selected_cols])
