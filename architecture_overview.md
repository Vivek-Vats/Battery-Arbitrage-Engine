# Advanced BESS Arbitrage Engine - Architecture Deep Dive

Welcome to the comprehensive architecture guide for your Battery Energy Storage System (BESS) MVP. The system has evolved from a simple Day-Ahead arbitrage model into a highly sophisticated, AI-driven, multi-market co-optimization engine. 

Here is exactly how the backend models, forecasts, and settles every parameter.

---

## 1. The Core Infrastructure

The MVP operates on a highly decoupled, asynchronous architecture using SQLite (`bess_data.db`) as the central nervous system. 

1. **`app.py`**: The Streamlit frontend. It allows users to manipulate 12 physical/financial battery parameters and visualizes the dispatch schedule and Waterfall unit economics.
2. **`bess_orchestrator.py`**: A background daemon that constantly polls the database for `PENDING` jobs. When triggered, it routes data through the ML, Quant, and Financial engines and serializes the results back to the UI.

---

## 2. Machine Learning Forecasting (`ml_forecaster.py`)

Before the physics engine can make decisions, it needs to know what the future looks like. The MVP uses **XGBoost** to forecast market prices.

### The Two-Stage Day-Ahead / Imbalance Model
Because Imbalance prices are highly volatile and asymmetric, predicting them directly is prone to error. The MVP uses a two-stage "Base + Residual" architecture:
1. **Base Model**: Predicts the highly structured Day-Ahead wholesale price.
2. **Residual Model**: Predicts the *spread* (deviation) between the Day-Ahead price and the 15-minute Imbalance price.
3. **Synthesis**: The final forecasted price is `Base Prediction + Residual Prediction`.

### The Anomaly "Firewall" Meta-Model
Imbalance prices occasionally spike to extreme extremes (e.g., €500+ or -€200). 
- An **XGBClassifier** is trained to detect these anomalies. 
- It outputs a probability score, which is mathematically inverted to calculate an `AI_Optimized_Margin_EUR`. 
- If the AI is highly confident an anomaly will occur, it lowers the margin, allowing the battery to aggressively chase the spike. If it is uncertain, it raises the margin, forcing the battery to play it safe.

### aFRR Capacity Forecasting
Separate XGBoost Regressors are trained on the exogenous meta-features (Wind/Solar/Load/Gas) to directly predict the `aFRR_Up_Reserve` and `aFRR_Down_Reserve` capacity clearing prices.

---

## 3. The PyPSA Quant Optimizer (`quant_engine.py`)

This is the brain of the operation. It uses **PyPSA** (Python for Power System Analysis) and the **HiGHS** linear solver to execute a perfect-foresight co-optimization across all 35,136 15-minute intervals. 

Instead of a generic `StorageUnit`, the MVP uses a custom topology to force the solver to respect strict physics:

### The Topology
- **AC Bus**: The physical power grid.
- **BESS_DC_Bus**: The internal chemical battery.
- **Virtual Ancillary Bus**: A completely isolated "dummy" bus used purely to log the financial revenue of capacity reservations without physically moving energy.

### The Physical Links
To move energy between the AC Grid and the DC Battery, the engine uses explicit, directional links:
- **`DA_Charge`**: Costs `grid_fee_import`. Passes through `efficiency_store`.
- **`DA_Discharge`**: Costs `degradation_penalty + AI_Optimized_Margin`. Passes through `efficiency_dispatch`.
- **`aFRR_Down_Activation`**: Represents the physical charging from downward regulation. Costs `grid_fee_import`.
- **`aFRR_Up_Activation`**: Represents the physical discharging from upward regulation. Costs `degradation_penalty` minus the expected `activation_reward_proxy`.

### The Custom Linear Constraints (`extra_functionality`)
1. **Inverter Limits**: The sum of Day-Ahead and aFRR actions cannot exceed the battery's maximum MW power rating at any given second.
   - *Charge Bound*: `DA_Charge + aFRR_Down_Reserve <= Power_MW`
   - *Discharge Bound*: `DA_Discharge + aFRR_Up_Reserve <= Power_MW`
2. **Physical Headroom**: The battery cannot bid into the aFRR market unless it holds the exact physical energy required to fulfill the activation.
   - *Upward*: The State of Charge (SOC) must be >= the energy committed to `aFRR_Up`.
   - *Downward*: The battery must maintain enough empty space (Headroom) to physically absorb the energy committed to `aFRR_Down`.
3. **The Activation Tie**: Since capacity reservations only have a statistical chance of being activated, a strict linear tie forces the optimizer to assume exactly a **15% physical activation rate** for any reserved capacity. This perfectly prices the degradation risk of bidding into the market.

---

## 4. The Financial Engine (`finance_engine.py`)

Once the solver generates the optimal physical dispatch, the Financial Engine steps in to settle the cash flows and calculate the unit economics.

### Revenue & Settlement
- **Capacity Payouts**: The battery is paid (`Volume * Capacity Price`) purely for reserving `aFRR_Up` and `aFRR_Down`. 
- **Activation Payouts**: The physical 15% activations are settled against the TSO's Imbalance price. For downward activation, the sign is flipped so that negative imbalance prices correctly generate positive revenue (you get paid to charge).

### VWAP COGS Allocation
Because the battery charges from the grid and then "sells" that energy to both the Day-Ahead and aFRR markets, the total charging costs must be split. The engine uses a **Volume-Weighted Average Price (VWAP)** methodology to proportionally allocate the COGS to each market based on their exact physical discharge volumes.

### Bi-Directional Rainflow Degradation Proxy
The engine calculates the total internal DC energy throughput flowing in *both* directions:
- `Discharging MWh / efficiency_dispatch`
- `Charging MWh * efficiency_store`

This total throughput is divided by two to calculate the **Equivalent Full Cycles (EFC)**. The EFC is then evaluated against the `expected_lifespan_cycles` to calculate the exact `Annual_Degradation_Cost` in Euros.

### The Final KPIs
The engine calculates the LCOS, Simple Payback, and Annual ROI by discounting the CAPEX and OPEX cash flows across the lifespan using the WACC, returning a clean dictionary to the orchestrator to render the dashboard!
