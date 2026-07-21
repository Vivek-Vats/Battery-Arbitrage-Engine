# Battery Arbitrage Engine

The **Battery Arbitrage Engine** is a high-performance, machine-learning-driven optimization system designed to maximize the economic value of utility-scale Battery Energy Storage Systems (BESS) across multiple European power markets.

## Architecture Overview

The system bridges physical dispatch optimization with accurate financial accounting through three core components:

1. **AI Price Forecaster (`ml_forecaster.py`)**
   - Utilizes XGBoost meta-models to predict Day-Ahead prices, Imbalance prices, and aFRR Capacity/Activation prices.

2. **Physical Dispatch Optimizer (`quant_engine.py`)**
   - Built on PyPSA and the Highs LP solver.
   - Constrains the physical battery limits (State of Charge, C-rate, Degradation).
   - Features **Dynamic Market Slippage**, using 3-tier market depth nodes to constrain Imbalance "infinite liquidity" gambling. The optimizer dynamically assesses the order book depth to ensure realistic market engagement.

3. **True Tank Ledger (`finance_engine.py`)**
   - A Moving Average Cost Accounting engine that computes the True Cost of Goods Sold (COGS).
   - Solves the "Accounting Illusion" by blending charging costs (from DA or Imbalance) into a global `tank_cost_per_mwh` average, and correctly assigning COGS to the discharging markets (e.g., buying in DA, selling in Imbalance).

## Installation

1. Clone the repository.
2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up your API Keys:
   - Copy `.env.example` to `.env`
   - Add your TenneT and ENTSO-E API keys.

## Running the Engine

1. **Run the Orchestrator** to download data, train the ML models, optimize the battery, and save the results to the local SQLite database:
   ```bash
   python bess_orchestrator.py
   ```
2. **Launch the Dashboard** to visualize the Waterfall charts, Monthly Revenues, and Physical Dispatch profiles:
   ```bash
   streamlit run app.py
   ```
