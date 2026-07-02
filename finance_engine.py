import pandas as pd

def calculate_financials(dispatch_df, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan_years, grid_fee_import, efficiency_dispatch, expected_lifespan_cycles=6000.0):
    time_step_hours = 0.25
    
    # Calculate Total CAPEX and Annual OPEX based on new energy-centric valuation
    TOTAL_CAPEX = capex_per_kwh * energy_mwh * 1000
    ANNUAL_OPEX = opex_per_mw * power_mw
    
    # Calculate Annual Gross Revenue
    cost_of_charging = dispatch_df['p_store'] * (dispatch_df['price'] + grid_fee_import)
    revenue_from_discharging = dispatch_df['p_dispatch'] * dispatch_df['price']
    gross_revenue_series = (revenue_from_discharging - cost_of_charging) * time_step_hours
    annual_gross_revenue = gross_revenue_series.sum()
    
    # Calculate Annual Delivered MWh
    annual_delivered_mwh = dispatch_df['p_dispatch'].sum() * time_step_hours
    average_spread = annual_gross_revenue / annual_delivered_mwh if annual_delivered_mwh > 0 else 0
    
    # Convert WACC from percentage to decimal
    wacc_decimal = wacc / 100.0
    
    # Discounted values over lifespan
    discounted_opex = sum(ANNUAL_OPEX / ((1 + wacc_decimal) ** t) for t in range(1, int(lifespan_years) + 1))
    total_discounted_lifetime_costs = TOTAL_CAPEX + discounted_opex
    
    discounted_delivered_mwh = sum(annual_delivered_mwh / ((1 + wacc_decimal) ** t) for t in range(1, int(lifespan_years) + 1))
    
    # LCOS Calculation
    # Avoid division by zero if there's no delivered energy
    if discounted_delivered_mwh > 0:
        lcos = total_discounted_lifetime_costs / discounted_delivered_mwh
    else:
        lcos = float('inf')
        
    # Net Cash Flow Calculation
    net_annual_cash_flow = annual_gross_revenue - ANNUAL_OPEX
    
    # Rainflow counting proxy / Battery wear-and-tear
    eff_dispatch_decimal = efficiency_dispatch / 100.0
    internal_dc_throughput_mwh = (dispatch_df['p_dispatch'].sum() * time_step_hours) / eff_dispatch_decimal
    efc = internal_dc_throughput_mwh / energy_mwh
    annual_degradation_cost = (efc / expected_lifespan_cycles) * TOTAL_CAPEX
    expected_physical_lifespan = expected_lifespan_cycles / efc if efc > 0 else float('inf')
    net_annual_cash_flow -= annual_degradation_cost
    
    # Simple Payback Period
    if net_annual_cash_flow > 0:
        simple_payback_period = TOTAL_CAPEX / net_annual_cash_flow
    else:
        simple_payback_period = float('inf')
        
    # Annual ROI
    annual_roi = (net_annual_cash_flow / TOTAL_CAPEX) * 100
    
    # Return KPI dictionary
    return {
        'Annual_Gross_Revenue_EUR': float(annual_gross_revenue),
        'LCOS_EUR_per_MWh': float(lcos),
        'Simple_Payback_Years': float(simple_payback_period),
        'Annual_ROI_Percentage': float(annual_roi),
        'Equivalent_Full_Cycles': float(efc),
        'Annual_Degradation_Cost_EUR': float(annual_degradation_cost),
        'Net_Annual_Profit_EUR': float(net_annual_cash_flow),
        'Total_CAPEX_EUR': float(TOTAL_CAPEX),
        'Average_Spread_EUR_per_MWh': float(average_spread),
        'Expected_Lifespan_Years': float(expected_physical_lifespan)
    }
