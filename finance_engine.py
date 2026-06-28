import pandas as pd

def calculate_financials(dispatch_df, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan_years):
    # Calculate Total CAPEX and Annual OPEX based on new energy-centric valuation
    TOTAL_CAPEX = capex_per_kwh * energy_mwh * 1000
    ANNUAL_OPEX = opex_per_mw * power_mw
    
    # Calculate Annual Gross Revenue
    gross_revenue_series = (dispatch_df['p_dispatch'] * dispatch_df['price']) - (dispatch_df['p_store'] * dispatch_df['price'])
    annual_gross_revenue = gross_revenue_series.sum()
    
    # Calculate Annual Delivered MWh
    annual_delivered_mwh = dispatch_df['p_dispatch'].sum()
    
    # Discounted values over lifespan
    discounted_opex = sum(ANNUAL_OPEX / ((1 + wacc) ** t) for t in range(1, int(lifespan_years) + 1))
    total_discounted_lifetime_costs = TOTAL_CAPEX + discounted_opex
    
    discounted_delivered_mwh = sum(annual_delivered_mwh / ((1 + wacc) ** t) for t in range(1, int(lifespan_years) + 1))
    
    # LCOS Calculation
    # Avoid division by zero if there's no delivered energy
    if discounted_delivered_mwh > 0:
        lcos = total_discounted_lifetime_costs / discounted_delivered_mwh
    else:
        lcos = float('inf')
        
    # Net Cash Flow Calculation
    net_annual_cash_flow = annual_gross_revenue - ANNUAL_OPEX
    
    # Simple Payback Period
    if net_annual_cash_flow > 0:
        simple_payback_period = TOTAL_CAPEX / net_annual_cash_flow
    else:
        simple_payback_period = float('inf')
        
    # Annual ROI
    annual_roi = (net_annual_cash_flow / TOTAL_CAPEX) * 100
    
    # Return KPI dictionary
    return {
        'Annual_Gross_Revenue_EUR': annual_gross_revenue,
        'LCOS_EUR_per_MWh': lcos,
        'Simple_Payback_Years': simple_payback_period,
        'Annual_ROI_Percentage': annual_roi
    }
