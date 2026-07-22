import pandas as pd

def calculate_financials(dispatch_df, power_mw, energy_mwh, capex_per_kwh, opex_per_mw, wacc, lifespan_years, grid_fee_import, efficiency_store, efficiency_dispatch, expected_lifespan_cycles=6000.0):
    """
    Calculates key financial metrics including stacked cash flows from Day-Ahead energy arbitrage 
    and ancillary service capacity revenues.
    
    Expected columns in dispatch_df:
    - DA_Charge, DA_Discharge
    - aFRR_Up_Reserve, afrr_up_price_mw
    """
    time_step_hours = 0.25
    
    # Calculate Total CAPEX and Annual OPEX based on new energy-centric valuation
    TOTAL_CAPEX = capex_per_kwh * energy_mwh * 1000
    ANNUAL_OPEX = opex_per_mw * power_mw
    
    # Disaggregated Energy Markets (Day-Ahead & Passive Imbalance)
    zero_series = pd.Series(0.0, index=dispatch_df.index)
    
    # Day-Ahead Vector
    da_price_actual = dispatch_df.get('da_price_actual', zero_series)
    da_charge_cost = dispatch_df.get('DA_Charge', zero_series) * (da_price_actual + grid_fee_import)
    da_discharge_rev = dispatch_df.get('DA_Discharge', zero_series) * da_price_actual
    total_da_revenue = (da_discharge_rev * time_step_hours).sum()
    total_charging_cost_da = (da_charge_cost * time_step_hours).sum()
    
    # Imbalance Vector
    imb_price = dispatch_df.get('price', zero_series)
    imb_charge = dispatch_df.get('Imbalance_Charge', zero_series)
    imb_discharge = dispatch_df.get('Imbalance_Discharge', zero_series)
    
    # Cost increases with Imbalance Charge Tiers
    imb_charge_cost = (
        dispatch_df.get('Imbalance_Charge_Tier1', zero_series) * (imb_price * 1.0 + grid_fee_import) +
        dispatch_df.get('Imbalance_Charge_Tier2', zero_series) * (imb_price * 1.05 + grid_fee_import) +
        dispatch_df.get('Imbalance_Charge_Tier3', zero_series) * (imb_price * 1.50 + grid_fee_import)
    )
    
    # Revenue drops with Imbalance Discharge Tiers
    imb_discharge_rev = (
        dispatch_df.get('Imbalance_Discharge_Tier1', zero_series) * (imb_price * 1.0) +
        dispatch_df.get('Imbalance_Discharge_Tier2', zero_series) * (imb_price * 0.95) +
        dispatch_df.get('Imbalance_Discharge_Tier3', zero_series) * (imb_price * 0.50)
    )
    
    Total_Imbalance_Discharge_Revenue_EUR = (imb_discharge_rev * time_step_hours).sum()
    Total_Imbalance_Charging_Cost_EUR = (imb_charge_cost * time_step_hours).sum()
    
    # Total aggregated charging cost across all paths
    total_charging_cost = total_charging_cost_da + Total_Imbalance_Charging_Cost_EUR
    energy_revenue = total_da_revenue + Total_Imbalance_Discharge_Revenue_EUR - total_charging_cost
    
    # Calculate aFRR Up Capacity Revenue (Slippage Tiers)
    tier1_up_rev = dispatch_df.get('aFRR_Up_Reserve_Tier1', zero_series) * dispatch_df.get('afrr_up_price_mw', zero_series) * 1.0
    tier2_up_rev = dispatch_df.get('aFRR_Up_Reserve_Tier2', zero_series) * dispatch_df.get('afrr_up_price_mw', zero_series) * 0.6
    tier3_up_rev = dispatch_df.get('aFRR_Up_Reserve_Tier3', zero_series) * dispatch_df.get('afrr_up_price_mw', zero_series) * 0.1
    afrr_capacity_revenue = (tier1_up_rev + tier2_up_rev + tier3_up_rev) * time_step_hours
    
    # Calculate aFRR Down Capacity Revenue (Slippage Tiers)
    tier1_dn_rev = dispatch_df.get('aFRR_Down_Reserve_Tier1', zero_series) * dispatch_df.get('afrr_down_price_mw', zero_series) * 1.0
    tier2_dn_rev = dispatch_df.get('aFRR_Down_Reserve_Tier2', zero_series) * dispatch_df.get('afrr_down_price_mw', zero_series) * 0.6
    tier3_dn_rev = dispatch_df.get('aFRR_Down_Reserve_Tier3', zero_series) * dispatch_df.get('afrr_down_price_mw', zero_series) * 0.1
    afrr_down_capacity_revenue = (tier1_dn_rev + tier2_dn_rev + tier3_dn_rev) * time_step_hours

    # Calculate Global Market Slippage
    ideal_afrr_up = (dispatch_df.get('aFRR_Up_Reserve', zero_series) * dispatch_df.get('afrr_up_price_mw', zero_series)) * time_step_hours
    ideal_afrr_down = (dispatch_df.get('aFRR_Down_Reserve', zero_series) * dispatch_df.get('afrr_down_price_mw', zero_series)) * time_step_hours
    ideal_imb_chg = imb_charge * (imb_price + grid_fee_import) * time_step_hours
    ideal_imb_dis = imb_discharge * imb_price * time_step_hours
    
    afrr_up_slippage = (ideal_afrr_up - afrr_capacity_revenue).sum()
    afrr_down_slippage = (ideal_afrr_down - afrr_down_capacity_revenue).sum()
    imb_chg_slippage = ((imb_charge_cost * time_step_hours) - ideal_imb_chg).sum()
    imb_dis_slippage = (ideal_imb_dis - (imb_discharge_rev * time_step_hours)).sum()
    
    slippage_loss = afrr_up_slippage + afrr_down_slippage + imb_chg_slippage + imb_dis_slippage
    
    afrr_activation_revenue_series = (dispatch_df.get('aFRR_Up_Activation', zero_series) * dispatch_df.get('afrr_up_activation_price_mwh', zero_series)) * time_step_hours
    afrr_activation_revenue = afrr_activation_revenue_series
    
    afrr_down_activation_revenue_series = (-dispatch_df.get('aFRR_Down_Activation', zero_series) * (dispatch_df.get('afrr_down_activation_price_mwh', zero_series) + grid_fee_import)) * time_step_hours
    afrr_down_activation_revenue = afrr_down_activation_revenue_series
    
    fcr_capacity_revenue = (dispatch_df.get('FCR_Reserve', zero_series) * dispatch_df.get('fcr_price_eur_mw', zero_series)) * time_step_hours
    
    # ---------------------------------------------------------
    # True Tank Ledger (Moving Average Cost Accounting)
    # ---------------------------------------------------------
    tank_soc = 0.0
    tank_cost_per_mwh = 0.0
    
    da_cogs_array = []
    imb_cogs_array = []
    afrr_cogs_array = []
    
    da_charge_array = dispatch_df.get('DA_Charge', zero_series).values
    da_discharge_array = dispatch_df.get('DA_Discharge', zero_series).values
    da_price_array = da_price_actual.values
    
    imb_charge_array = imb_charge.values
    imb_discharge_array = imb_discharge.values
    imb_charge_cost_array = imb_charge_cost.values
    
    afrr_up_act_array = dispatch_df.get('aFRR_Up_Activation', zero_series).values
    afrr_down_act_array = dispatch_df.get('aFRR_Down_Activation', zero_series).values
    # Note: aFRR Down acts as a charging volume
    
    for i in range(len(dispatch_df)):
        # 1. Incoming Charge (MWh)
        da_in = da_charge_array[i] * time_step_hours
        da_in_cost = da_in * (da_price_array[i] + grid_fee_import)
        
        imb_in = imb_charge_array[i] * time_step_hours
        imb_in_cost = imb_charge_cost_array[i] * time_step_hours
        
        afrr_down_in = afrr_down_act_array[i] * time_step_hours
        # The true physical cost to charge via aFRR down is the activation price plus the grid fee.
        # This accurately loads the COGS into the tank for when the energy is eventually discharged.
        afrr_act_price = dispatch_df.get('afrr_down_activation_price_mwh', zero_series).values[i]
        afrr_in_cost = afrr_down_in * (afrr_act_price + grid_fee_import) 
        
        total_in_vol = da_in + imb_in + afrr_down_in
        if total_in_vol > 0:
            total_in_cost = da_in_cost + imb_in_cost + afrr_in_cost
            # Update moving average
            new_tank_soc = tank_soc + total_in_vol
            tank_cost_per_mwh = ((tank_soc * tank_cost_per_mwh) + total_in_cost) / new_tank_soc
            tank_soc = new_tank_soc
            
        # 2. Outgoing Discharge (MWh)
        da_out = da_discharge_array[i] * time_step_hours
        imb_out = imb_discharge_array[i] * time_step_hours
        afrr_out = afrr_up_act_array[i] * time_step_hours
        
        # Calculate specific COGS for these outgoing volumes
        da_cogs_array.append(da_out * tank_cost_per_mwh)
        imb_cogs_array.append(imb_out * tank_cost_per_mwh)
        afrr_cogs_array.append(afrr_out * tank_cost_per_mwh)
        
        total_out_vol = da_out + imb_out + afrr_out
        tank_soc = max(0, tank_soc - total_out_vol)

    dispatch_df['DA_COGS_EUR'] = da_cogs_array
    dispatch_df['Imbalance_COGS_EUR'] = imb_cogs_array
    dispatch_df['aFRR_COGS_EUR'] = afrr_cogs_array
    
    # ---------------------------------------------------------

    da_cogs = sum(da_cogs_array)
    imb_cogs = sum(imb_cogs_array)
    afrr_cogs = sum(afrr_cogs_array)
    
    Net_DA_Revenue_EUR = total_da_revenue - da_cogs
    Net_Imbalance_Revenue_EUR = Total_Imbalance_Discharge_Revenue_EUR - imb_cogs
    net_afrr_act_revenue = afrr_activation_revenue.sum() - afrr_cogs
    
    energy_revenue = Net_DA_Revenue_EUR + Net_Imbalance_Revenue_EUR
    total_gross_revenue = energy_revenue + afrr_capacity_revenue.sum() + net_afrr_act_revenue + afrr_down_capacity_revenue.sum() + fcr_capacity_revenue.sum()
    
    # Calculate Annual Delivered MWh
    da_vol = dispatch_df.get('DA_Discharge', zero_series).sum() * time_step_hours
    imb_vol = imb_discharge.sum() * time_step_hours
    afrr_act_vol = dispatch_df.get('aFRR_Up_Activation', zero_series).sum() * time_step_hours
    total_vol = da_vol + imb_vol + afrr_act_vol
    annual_delivered_mwh = total_vol
    average_spread = total_gross_revenue / annual_delivered_mwh if annual_delivered_mwh > 0 else 0
    
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
    if len(dispatch_df) > 0 and isinstance(dispatch_df.index, pd.DatetimeIndex):
        sim_days = (dispatch_df.index[-1] - dispatch_df.index[0]).total_seconds() / 86400.0
    else:
        sim_days = len(dispatch_df) / 96.0 if len(dispatch_df) > 0 else 0.0
    annualization_factor = 365.0 / sim_days if sim_days > 0 else 1.0

    # Rainflow counting proxy / Battery wear-and-tear
    eff_store_decimal = efficiency_store / 100.0
    eff_dispatch_decimal = efficiency_dispatch / 100.0
    
    # True Physical Battery Degradation (calculated from actual SoC changes to avoid counting virtual passive arbitrage transits)
    soc_series = dispatch_df.get('state_of_charge', zero_series)
    internal_dc_throughput_mwh = soc_series.diff().abs().sum()
    
    efc = (internal_dc_throughput_mwh / 2) / energy_mwh if energy_mwh > 0 else 0
    annual_degradation_cost = (efc / expected_lifespan_cycles) * TOTAL_CAPEX
    
    # Scale to annual equivalent
    total_gross_revenue_annual = total_gross_revenue * annualization_factor
    annual_degradation_cost_annual = annual_degradation_cost * annualization_factor
    
    max_calendar_years = 15.0
    annual_calendar_aging_cost = TOTAL_CAPEX / max_calendar_years
    realized_degradation_cost = max(annual_degradation_cost_annual, annual_calendar_aging_cost)
    
    net_annual_cash_flow = total_gross_revenue_annual - ANNUAL_OPEX - realized_degradation_cost
    expected_physical_lifespan = expected_lifespan_cycles / (efc * annualization_factor) if efc > 0 else float('inf')
    
    # Simple Payback Period
    if net_annual_cash_flow > 0:
        simple_payback_period = TOTAL_CAPEX / net_annual_cash_flow
    else:
        simple_payback_period = float('inf')
        
    # Annual ROI
    annual_roi = (net_annual_cash_flow / TOTAL_CAPEX) * 100
    
    # Return KPI dictionary
    return {
        'Total_Gross_Revenue_EUR': float(total_gross_revenue),
        'Market_Slippage_Loss_EUR': float(slippage_loss),
        'Total_DA_Revenue_EUR': float(total_da_revenue),
        'Total_Charging_Cost_EUR': float(total_charging_cost),
        'Total_OPEX_EUR': float(ANNUAL_OPEX),
        'Ancillary_Capacity_Revenue_EUR': float(afrr_capacity_revenue.sum()),
        'Ancillary_Activation_Revenue_EUR': float(afrr_activation_revenue.sum()),
        'LCOS_EUR_per_MWh': float(lcos),
        'Simple_Payback_Years': float(simple_payback_period),
        'Annual_ROI_Percentage': float(annual_roi),
        'Equivalent_Full_Cycles': float(efc),
        'Annual_Degradation_Cost_EUR': float(realized_degradation_cost),
        'Net_Annual_Profit_EUR': float(net_annual_cash_flow),
        'Total_CAPEX_EUR': float(TOTAL_CAPEX),
        'Average_Spread_EUR_per_MWh': float(average_spread),
        'Expected_Lifespan_Years': float(expected_physical_lifespan),
        'Net_DA_Revenue_EUR': float(Net_DA_Revenue_EUR),
        'Net_aFRR_Activation_Revenue_EUR': float(net_afrr_act_revenue),
        'DA_COGS_EUR': float(da_cogs),
        'aFRR_COGS_EUR': float(afrr_cogs),
        'Ancillary_Down_Capacity_Revenue_EUR': float(afrr_down_capacity_revenue.sum()),
        'Ancillary_Down_Activation_Revenue_EUR': float(afrr_down_activation_revenue.sum()),
        'Net_Imbalance_Revenue_EUR': float(Net_Imbalance_Revenue_EUR),
        'Total_Imbalance_Charging_Cost_EUR': float(Total_Imbalance_Charging_Cost_EUR),
        'Total_Imbalance_Discharge_Revenue_EUR': float(Total_Imbalance_Discharge_Revenue_EUR),
        'FCR_Capacity_Revenue_EUR': float(fcr_capacity_revenue.sum())
    }
