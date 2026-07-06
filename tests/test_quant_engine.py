import pytest
import pandas as pd
from quant_engine import optimize_dispatch

@pytest.fixture
def prices_df():
    """
    Generate a 4-hour synthetic price curve with a 15-minute frequency.
    """
    index = pd.date_range(start="2024-01-01", periods=4, freq="15min")
    return pd.DataFrame({"price": [10.0, 500.0, 10.0, 500.0], "forecast_price": [10.0, 500.0, 10.0, 500.0]}, index=index)

def test_dod_constraint(prices_df):
    """
    Test Depth of Discharge constraint.
    """
    df = optimize_dispatch(
        prices_df=prices_df,
        power_mw=100.0,
        energy_mwh=200.0,
        grid_fee_import=0.0,
        efficiency_store=100.0,
        efficiency_dispatch=100.0,
        depth_of_discharge=80.0,
        degradation_penalty=0.0
    )
    
    assert df['state_of_charge'].min() >= 40.0

def test_efficiency_losses(prices_df):
    """
    Test Efficiency Losses constraint.
    """
    df = optimize_dispatch(
        prices_df=prices_df,
        power_mw=100.0,
        energy_mwh=200.0,
        grid_fee_import=0.0,
        efficiency_store=90.0,
        efficiency_dispatch=90.0,
        depth_of_discharge=80.0,
        degradation_penalty=0.0
    )
    
    assert df['p_dispatch'].sum() < df['p_store'].sum()

def test_grid_fee_avoidance():
    """
    Test Grid Fee & Penalty Avoidance constraint.
    """
    index = pd.date_range(start="2024-01-01", periods=4, freq="15min")
    local_df = pd.DataFrame({"price": [10.0, 20.0, 10.0, 20.0], "forecast_price": [10.0, 20.0, 10.0, 20.0]}, index=index)
    
    df = optimize_dispatch(
        prices_df=local_df,
        power_mw=100.0,
        energy_mwh=200.0,
        grid_fee_import=15.0,
        efficiency_store=100.0,
        efficiency_dispatch=100.0,
        depth_of_discharge=80.0,
        degradation_penalty=5.0
    )
    
    assert df['p_dispatch'].sum() + df['p_store'].sum() == 0.0
