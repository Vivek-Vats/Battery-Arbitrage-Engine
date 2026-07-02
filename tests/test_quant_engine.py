import pytest
import pandas as pd
from quant_engine import optimize_dispatch

@pytest.fixture
def prices_df():
    # 4-hour synthetic price curve
    prices = [10.0, 500.0, 10.0, 500.0]
    
    # Valid pd.DatetimeIndex with an hourly frequency (freq='H')
    index = pd.date_range(start='2026-07-01 00:00:00', periods=4, freq='h')
    
    # DataFrame containing 'price' column
    df = pd.DataFrame({'price': prices}, index=index)
    return df

def test_dod_constraint(prices_df):
    df = optimize_dispatch(
        prices_df=prices_df,
        power_mw=10.0,
        energy_mwh=20.0,
        grid_fee_import=0.0,
        efficiency_store=100.0,
        efficiency_dispatch=100.0,
        depth_of_discharge=80.0,
        degradation_penalty=2.0
    )
    
    # Dead energy floor for 20 MWh battery with 80% DoD is 20% = 4.0 MWh.
    # We evenly distribute this dead energy (2.0 MWh floor, 2.0 MWh ceiling).
    # Assert minimum state_of_charge is >= 2.0
    assert df['state_of_charge'].min() >= 2.0

def test_efficiency_losses(prices_df):
    df = optimize_dispatch(
        prices_df=prices_df,
        power_mw=10.0,
        energy_mwh=20.0,
        grid_fee_import=0.0,
        efficiency_store=90.0,
        efficiency_dispatch=90.0,
        depth_of_discharge=80.0,
        degradation_penalty=2.0
    )
    
    # Assert total energy discharged is strictly less than total energy stored
    assert df['p_dispatch'].sum() < df['p_store'].sum()

def test_power_constraints(prices_df):
    df = optimize_dispatch(
        prices_df=prices_df,
        power_mw=10.0,
        energy_mwh=20.0,
        grid_fee_import=0.0,
        efficiency_store=100.0,
        efficiency_dispatch=100.0,
        depth_of_discharge=80.0,
        degradation_penalty=2.0
    )
    assert df['p_dispatch'].max() <= 10.0
    assert df['p_store'].max() <= 10.0

def test_behavioral_degradation_penalty(prices_df):
    df = optimize_dispatch(
        prices_df=prices_df,
        power_mw=10.0,
        energy_mwh=20.0,
        grid_fee_import=0.0,
        efficiency_store=100.0,
        efficiency_dispatch=100.0,
        depth_of_discharge=80.0,
        degradation_penalty=5000.0
    )
    assert df['p_dispatch'].sum() == 0.0

def test_behavioral_grid_fee(prices_df):
    df = optimize_dispatch(
        prices_df=prices_df,
        power_mw=10.0,
        energy_mwh=20.0,
        grid_fee_import=5000.0,
        efficiency_store=100.0,
        efficiency_dispatch=100.0,
        depth_of_discharge=80.0,
        degradation_penalty=2.0
    )
    assert df['p_store'].sum() == 0.0

def test_timezone_preservation():
    prices = [10.0, 500.0, 10.0, 500.0]
    index = pd.date_range(start='2026-07-01 00:00:00', periods=4, freq='h', tz='Europe/Amsterdam')
    df_input = pd.DataFrame({'price': prices}, index=index)
    
    df = optimize_dispatch(
        prices_df=df_input,
        power_mw=10.0,
        energy_mwh=20.0,
        grid_fee_import=0.0,
        efficiency_store=100.0,
        efficiency_dispatch=100.0,
        depth_of_discharge=80.0,
        degradation_penalty=2.0
    )
    assert df.index.tz is not None
    assert str(df.index.tz) == 'Europe/Amsterdam'
