import pytest
import pandas as pd
import numpy as np
from finance_engine import calculate_financials

@pytest.fixture
def dispatch_df():
    """
    Mock dispatch_df fixture with exactly 1 year of 15-minute synthetic data (35,040 rows).
    Contains known p_dispatch, p_store, and price values.
    """
    n_rows = 35040
    p_store = np.zeros(n_rows)
    p_dispatch = np.zeros(n_rows)
    price = np.full(n_rows, 50.0)
    
    # 8760 periods of charging at 100 MW (1/4 of the year)
    p_store[:8760] = 100.0
    price[:8760] = 10.0
    
    # 8760 periods of discharging at 100 MW
    p_dispatch[8760:17520] = 100.0
    price[8760:17520] = 100.0
    
    df = pd.DataFrame({
        'p_dispatch': p_dispatch,
        'p_store': p_store,
        'price': price
    })
    return df

def test_wacc_discounting(dispatch_df):
    """
    Test WACC Discounting:
    Manually calculate the expected discounted LCOS for a €36M CAPEX, 15-year, 7% WACC system.
    """
    power_mw = 100
    energy_mwh = 200
    capex_per_kwh = 180
    opex_per_mw = 15000
    wacc = 7.0
    lifespan_years = 15
    grid_fee_import = 15
    efficiency_dispatch = 93.0
    expected_lifespan_cycles = 6000

    results = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=power_mw,
        energy_mwh=energy_mwh,
        capex_per_kwh=capex_per_kwh,
        opex_per_mw=opex_per_mw,
        wacc=wacc,
        lifespan_years=lifespan_years,
        grid_fee_import=grid_fee_import,
        efficiency_dispatch=efficiency_dispatch,
        expected_lifespan_cycles=expected_lifespan_cycles
    )
    
    # Manual Calculation
    TOTAL_CAPEX = capex_per_kwh * energy_mwh * 1000 # 180 * 200 * 1000 = 36,000,000
    ANNUAL_OPEX = opex_per_mw * power_mw # 15000 * 100 = 1,500,000
    wacc_decimal = wacc / 100.0 # 0.07
    
    discounted_opex = sum(ANNUAL_OPEX / ((1 + wacc_decimal) ** t) for t in range(1, lifespan_years + 1))
    total_discounted_lifetime_costs = TOTAL_CAPEX + discounted_opex
    
    time_step_hours = 0.25
    annual_delivered_mwh = dispatch_df['p_dispatch'].sum() * time_step_hours
    
    discounted_delivered_mwh = sum(annual_delivered_mwh / ((1 + wacc_decimal) ** t) for t in range(1, lifespan_years + 1))
    
    expected_lcos = total_discounted_lifetime_costs / discounted_delivered_mwh
    
    assert abs(results['LCOS_EUR_per_MWh'] - expected_lcos) < 0.01

def test_dynamic_lifespan(dispatch_df):
    """
    Test Dynamic Lifespan:
    Run the engine twice (6000 vs 3000 expected lifespan cycles) and assert Annual_Degradation_Cost_EUR doubles.
    """
    kwargs = dict(
        dispatch_df=dispatch_df,
        power_mw=100,
        energy_mwh=200,
        capex_per_kwh=180,
        opex_per_mw=15000,
        wacc=7.0,
        lifespan_years=15,
        grid_fee_import=15,
        efficiency_dispatch=93.0
    )
    
    res_6000 = calculate_financials(**kwargs, expected_lifespan_cycles=6000)
    res_3000 = calculate_financials(**kwargs, expected_lifespan_cycles=3000)
    
    deg_cost_6000 = res_6000['Annual_Degradation_Cost_EUR']
    deg_cost_3000 = res_3000['Annual_Degradation_Cost_EUR']
    
    assert abs(deg_cost_3000 - (2 * deg_cost_6000)) < 1e-5

def test_dc_throughput(dispatch_df):
    """
    Test Throughput Leak:
    Assert Equivalent_Full_Cycles (EFC) correctly accounts for inverter efficiency,
    meaning internal DC wear is proportionally higher than external AC dispatch sum.
    """
    efficiency = 93.0
    energy_mwh = 200
    
    results = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=100,
        energy_mwh=energy_mwh,
        capex_per_kwh=180,
        opex_per_mw=15000,
        wacc=7.0,
        lifespan_years=15,
        grid_fee_import=15,
        efficiency_dispatch=efficiency,
        expected_lifespan_cycles=6000
    )
    
    time_step_hours = 0.25
    annual_delivered_mwh = dispatch_df['p_dispatch'].sum() * time_step_hours
    
    # EFC without efficiency penalty (AC only)
    efc_ac_only = annual_delivered_mwh / energy_mwh
    
    # Expected EFC with efficiency penalty (DC throughput)
    internal_dc_throughput_mwh = annual_delivered_mwh / (efficiency / 100.0)
    expected_efc = internal_dc_throughput_mwh / energy_mwh
    
    actual_efc = results['Equivalent_Full_Cycles']
    
    # Assert it correctly matches the calculation
    assert abs(actual_efc - expected_efc) < 1e-5
    
    # Assert that DC wear is proportionally higher than AC sum
    assert actual_efc > efc_ac_only
    assert abs(actual_efc - (efc_ac_only / 0.93)) < 1e-5
