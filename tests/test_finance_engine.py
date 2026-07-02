import pytest
import pandas as pd
from finance_engine import calculate_financials

@pytest.fixture
def dispatch_df():
    """
    Mock dispatch_df with 4 rows representing 1 hour of 15-minute intervals.
    """
    return pd.DataFrame({
        'p_dispatch': [10.0, 10.0, 0.0, 0.0],
        'p_store': [0.0, 0.0, 10.0, 10.0],
        'price': [100.0, 150.0, 20.0, 10.0]
    })

def test_calculate_financials_grid_fee(dispatch_df):
    """
    Asserts the Gross Revenue correctly subtracts the grid_fee_import during charging.
    """
    # Base parameters
    power_mw = 10
    energy_mwh = 20
    capex_per_kwh = 200
    opex_per_mw = 10000
    wacc = 7.0
    lifespan_years = 15
    efficiency_dispatch = 90.0
    
    # Calculate with 0 grid fee
    res_no_fee = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=power_mw,
        energy_mwh=energy_mwh,
        capex_per_kwh=capex_per_kwh,
        opex_per_mw=opex_per_mw,
        wacc=wacc,
        lifespan_years=lifespan_years,
        grid_fee_import=0.0,
        efficiency_dispatch=efficiency_dispatch
    )
    
    # Calculate with grid fee of 10 EUR/MWh
    grid_fee = 10.0
    res_with_fee = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=power_mw,
        energy_mwh=energy_mwh,
        capex_per_kwh=capex_per_kwh,
        opex_per_mw=opex_per_mw,
        wacc=wacc,
        lifespan_years=lifespan_years,
        grid_fee_import=grid_fee,
        efficiency_dispatch=efficiency_dispatch
    )
    
    # Expected difference: Total charging MWh * grid_fee
    # time_step_hours = 0.25
    # Total p_store = 20.0 (10.0 + 10.0)
    # Charging MWh = 20.0 * 0.25 = 5.0 MWh
    # Expected fee cost = 5.0 * 10 = 50.0 EUR
    expected_diff = dispatch_df['p_store'].sum() * 0.25 * grid_fee
    
    actual_diff = res_no_fee['Annual_Gross_Revenue_EUR'] - res_with_fee['Annual_Gross_Revenue_EUR']
    
    assert actual_diff == pytest.approx(expected_diff), f"Expected gross revenue difference of {expected_diff}, but got {actual_diff}"

def test_calculate_financials_degradation(dispatch_df):
    """
    Asserts the Rainflow proxy correctly applies the efficiency_dispatch loss to the internal DC throughput.
    """
    power_mw = 10
    energy_mwh = 20
    capex_per_kwh = 200
    opex_per_mw = 10000
    wacc = 7.0
    lifespan_years = 15
    grid_fee_import = 0.0
    
    # Calculate with 100% efficiency
    res_100_eff = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=power_mw,
        energy_mwh=energy_mwh,
        capex_per_kwh=capex_per_kwh,
        opex_per_mw=opex_per_mw,
        wacc=wacc,
        lifespan_years=lifespan_years,
        grid_fee_import=grid_fee_import,
        efficiency_dispatch=100.0
    )
    
    # Calculate with 50% efficiency
    res_50_eff = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=power_mw,
        energy_mwh=energy_mwh,
        capex_per_kwh=capex_per_kwh,
        opex_per_mw=opex_per_mw,
        wacc=wacc,
        lifespan_years=lifespan_years,
        grid_fee_import=grid_fee_import,
        efficiency_dispatch=50.0
    )
    
    # At 50% efficiency, the internal_dc_throughput_mwh should be double compared to 100% efficiency
    # Therefore, EFC and degradation cost should be double
    
    assert res_50_eff['Equivalent_Full_Cycles'] == pytest.approx(res_100_eff['Equivalent_Full_Cycles'] * 2), \
        "EFC did not double when efficiency halved"
    
    assert res_50_eff['Annual_Degradation_Cost_EUR'] == pytest.approx(res_100_eff['Annual_Degradation_Cost_EUR'] * 2), \
        "Degradation cost did not double when efficiency halved"
    
    # Furthermore, verify the exact mathematical formulation
    # p_dispatch total = 20.0
    # delivered energy = 20.0 * 0.25 = 5.0 MWh
    # At 50% efficiency: internal throughput = 5.0 / 0.5 = 10.0 MWh
    # EFC = 10.0 / energy_mwh(20) = 0.5
    assert res_50_eff['Equivalent_Full_Cycles'] == pytest.approx(0.5)

def test_calculate_financials_edge_cases(dispatch_df):
    """
    Test zero division safety and negative cash flow edge cases.
    """
    # 1. Zero out p_dispatch and p_store to test zero division safety
    df_zero = dispatch_df.copy()
    df_zero['p_dispatch'] = 0.0
    df_zero['p_store'] = 0.0
    
    res_zero = calculate_financials(
        dispatch_df=df_zero,
        power_mw=10,
        energy_mwh=20,
        capex_per_kwh=200,
        opex_per_mw=10000,
        wacc=7.0,
        lifespan_years=15,
        grid_fee_import=0.0,
        efficiency_dispatch=90.0
    )
    assert res_zero['LCOS_EUR_per_MWh'] == float('inf')
    
    # 2. Set a massive opex_per_mw to force a negative cash flow
    res_negative = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=10,
        energy_mwh=20,
        capex_per_kwh=200,
        opex_per_mw=1000000,  # massive OPEX
        wacc=7.0,
        lifespan_years=15,
        grid_fee_import=0.0,
        efficiency_dispatch=90.0
    )
    assert res_negative['Simple_Payback_Years'] == float('inf')


def test_calculate_financials_lcos_discounting(dispatch_df):
    """
    Test LCOS logic with 0% and 7% WACC.
    """
    res_0_wacc = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=10,
        energy_mwh=20,
        capex_per_kwh=200,
        opex_per_mw=10000,
        wacc=0.0,
        lifespan_years=15,
        grid_fee_import=0.0,
        efficiency_dispatch=90.0
    )
    
    # Expected LCOS at 0% WACC
    total_capex = 200 * 20 * 1000
    annual_opex = 10000 * 10
    annual_delivered_mwh = dispatch_df['p_dispatch'].sum() * 0.25
    
    expected_lcos_0 = (total_capex + annual_opex * 15) / (annual_delivered_mwh * 15)
    assert res_0_wacc['LCOS_EUR_per_MWh'] == pytest.approx(expected_lcos_0)
    
    res_7_wacc = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=10,
        energy_mwh=20,
        capex_per_kwh=200,
        opex_per_mw=10000,
        wacc=7.0,
        lifespan_years=15,
        grid_fee_import=0.0,
        efficiency_dispatch=90.0
    )
    
    # LCOS at 7% WACC should be strictly greater than at 0% WACC
    assert res_7_wacc['LCOS_EUR_per_MWh'] > res_0_wacc['LCOS_EUR_per_MWh']


def test_calculate_financials_capex_opex_scaling(dispatch_df):
    """
    Test ROI scaling when CAPEX doubles.
    """
    # Force degradation cost to 0 by removing dispatch, 
    # so that cash flow is constant and ROI halves exactly when CAPEX doubles.
    df_scaling = dispatch_df.copy()
    df_scaling['p_dispatch'] = 0.0
    
    res_baseline = calculate_financials(
        dispatch_df=df_scaling,
        power_mw=10,
        energy_mwh=20,
        capex_per_kwh=200,
        opex_per_mw=10000,
        wacc=7.0,
        lifespan_years=15,
        grid_fee_import=0.0,
        efficiency_dispatch=90.0
    )
    
    res_double = calculate_financials(
        dispatch_df=df_scaling,
        power_mw=10,
        energy_mwh=20,
        capex_per_kwh=400,
        opex_per_mw=10000,
        wacc=7.0,
        lifespan_years=15,
        grid_fee_import=0.0,
        efficiency_dispatch=90.0
    )
    
    assert res_double['Annual_ROI_Percentage'] == pytest.approx(res_baseline['Annual_ROI_Percentage'] / 2.0)

def test_calculate_financials_new_executive_metrics(dispatch_df):
    """
    Test the newly added executive metrics: Net Annual Profit, Total CAPEX, and Average Spread.
    """
    power_mw = 10
    energy_mwh = 20
    capex_per_kwh = 200
    opex_per_mw = 10000
    wacc = 7.0
    lifespan_years = 15
    grid_fee_import = 0.0
    efficiency_dispatch = 100.0
    
    res = calculate_financials(
        dispatch_df=dispatch_df,
        power_mw=power_mw,
        energy_mwh=energy_mwh,
        capex_per_kwh=capex_per_kwh,
        opex_per_mw=opex_per_mw,
        wacc=wacc,
        lifespan_years=lifespan_years,
        grid_fee_import=grid_fee_import,
        efficiency_dispatch=efficiency_dispatch
    )
    
    # 1. Total CAPEX = 200 EUR/kWh * 20 MWh * 1000 = 4,000,000 EUR
    expected_total_capex = 200 * 20 * 1000
    assert res['Total_CAPEX_EUR'] == pytest.approx(expected_total_capex)
    
    # 2. Net Annual Profit = Gross Revenue - Annual OPEX - Annual Degradation
    expected_net_profit = res['Annual_Gross_Revenue_EUR'] - (opex_per_mw * power_mw) - res['Annual_Degradation_Cost_EUR']
    assert res['Net_Annual_Profit_EUR'] == pytest.approx(expected_net_profit)
    
    # 3. Average Spread = Gross Revenue / Delivered MWh
    annual_delivered_mwh = dispatch_df['p_dispatch'].sum() * 0.25
    expected_spread = res['Annual_Gross_Revenue_EUR'] / annual_delivered_mwh if annual_delivered_mwh > 0 else 0
    assert res['Average_Spread_EUR_per_MWh'] == pytest.approx(expected_spread)
