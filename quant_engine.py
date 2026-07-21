import pandas as pd
import pypsa
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def optimize_dispatch(prices_df, power_mw, energy_mwh, current_soc, eff_store, eff_dispatch, dod_pu, degradation_penalty, grid_fee_import=0.08):
    prices_df_naive = prices_df.copy()
    if prices_df_naive.index.tzinfo is not None:
        prices_df_naive.index = prices_df_naive.index.tz_convert('UTC').tz_localize(None)

    prices_df_naive['date'] = prices_df_naive.index.date
    grouped = prices_df_naive.groupby('date')
    
    all_dispatch_results = []

    for date, daily_df in grouped:
        n = pypsa.Network()
        n.set_snapshots(daily_df.index)

        # Base Grid & Battery only
        n.add("Bus", "AC")
        n.add("Bus", "BESS_DC_Bus")
        n.add("Bus", "Imbalance_AC")

        charge_cost = grid_fee_import
        discharge_cost = degradation_penalty
        if 'AI_Optimized_Margin_EUR' in daily_df.columns:
            discharge_cost = degradation_penalty + daily_df['AI_Optimized_Margin_EUR']

        n.add("Generator", "Grid", 
              bus="AC", 
              p_nom=100000, 
              p_min_pu=-1, 
              marginal_cost=charge_cost)
              
        imb_cost = daily_df.get('forecast_price', pd.Series(0.0, index=daily_df.index))
        n.add("Generator", "Imbalance_AC_Gen", 
              bus="Imbalance_AC", 
              p_nom=100000, 
              p_min_pu=-1, 
              marginal_cost=imb_cost)

        n.add("Store", "Battery", 
              bus="BESS_DC_Bus", 
              e_nom=energy_mwh, 
              e_min_pu=(1.0 - dod_pu), 
              e_initial=current_soc, 
              e_cyclic=False)

        forecast_da_price_series = daily_df['forecast_da_price'] if 'forecast_da_price' in daily_df.columns else daily_df.get('da_price_actual', pd.Series(0.0, index=daily_df.index))
        forecast_imb_price_series = daily_df['forecast_price'] if 'forecast_price' in daily_df.columns else daily_df.get('price', pd.Series(0.0, index=daily_df.index))

        n.add("Link", "DA_Charge", 
              bus0="AC", 
              bus1="BESS_DC_Bus", 
              p_nom=power_mw, 
              efficiency=eff_store, 
              marginal_cost=grid_fee_import + forecast_da_price_series)

        n.add("Link", "DA_Discharge", 
              bus0="BESS_DC_Bus", 
              bus1="AC", 
              p_nom=power_mw, 
              efficiency=eff_dispatch, 
              marginal_cost=degradation_penalty - forecast_da_price_series)

        # Market Depth Elasticity Tiers (from TenneT MOL)
        safe_vol = daily_df.get('forecast_safe_volume_mw', pd.Series(20.0, index=daily_df.index))
        sat_vol = daily_df.get('forecast_saturation_volume_mw', pd.Series(50.0, index=daily_df.index))

        tier1_pu = (safe_vol / power_mw).clip(lower=0.0, upper=1.0).fillna(0.0)
        tier2_pu = ((sat_vol - safe_vol) / power_mw).clip(lower=0.0, upper=1.0).fillna(0.0)
        tier3_pu = (1.0 - tier1_pu - tier2_pu).clip(lower=0.0, upper=1.0).fillna(0.0)

        # Imbalance Tiers (Cost increases with volume for Charge)
        n.add("Link", "Imbalance_Charge_Tier1", bus0="Imbalance_AC", bus1="BESS_DC_Bus", p_nom=power_mw, p_max_pu=tier1_pu, efficiency=eff_store, marginal_cost=grid_fee_import + forecast_imb_price_series * 1.0)
        n.add("Link", "Imbalance_Charge_Tier2", bus0="Imbalance_AC", bus1="BESS_DC_Bus", p_nom=power_mw, p_max_pu=tier2_pu, efficiency=eff_store, marginal_cost=grid_fee_import + forecast_imb_price_series * 1.05)
        n.add("Link", "Imbalance_Charge_Tier3", bus0="Imbalance_AC", bus1="BESS_DC_Bus", p_nom=power_mw, p_max_pu=tier3_pu, efficiency=eff_store, marginal_cost=grid_fee_import + forecast_imb_price_series * 1.50)

        # Imbalance Tiers (Revenue decreases with volume for Discharge)
        n.add("Link", "Imbalance_Discharge_Tier1", bus0="BESS_DC_Bus", bus1="Imbalance_AC", p_nom=power_mw, p_max_pu=tier1_pu, efficiency=eff_dispatch, marginal_cost=degradation_penalty - forecast_imb_price_series * 1.0)
        n.add("Link", "Imbalance_Discharge_Tier2", bus0="BESS_DC_Bus", bus1="Imbalance_AC", p_nom=power_mw, p_max_pu=tier2_pu, efficiency=eff_dispatch, marginal_cost=degradation_penalty - forecast_imb_price_series * 0.95)
        n.add("Link", "Imbalance_Discharge_Tier3", bus0="BESS_DC_Bus", bus1="Imbalance_AC", p_nom=power_mw, p_max_pu=tier3_pu, efficiency=eff_dispatch, marginal_cost=degradation_penalty - forecast_imb_price_series * 0.50)

        # Ancillary Services
        n.add("Bus", "Virtual_Ancillary_Bus")

        fcr_cost = daily_df.get('fcr_price_eur_mw', pd.Series(0.0, index=daily_df.index))
        n.add("Generator", "FCR_Reserve", 
              bus="Virtual_Ancillary_Bus", 
              p_nom=power_mw, 
              marginal_cost=-fcr_cost)

        afrr_up_cost = daily_df.get('forecast_afrr_up_price_mw', pd.Series(0.0, index=daily_df.index))
        n.add("Generator", "aFRR_Up_Reserve_Tier1", bus="Virtual_Ancillary_Bus", p_nom=power_mw, p_max_pu=tier1_pu, marginal_cost=-afrr_up_cost * 1.0)
        n.add("Generator", "aFRR_Up_Reserve_Tier2", bus="Virtual_Ancillary_Bus", p_nom=power_mw, p_max_pu=tier2_pu, marginal_cost=-afrr_up_cost * 0.6)
        n.add("Generator", "aFRR_Up_Reserve_Tier3", bus="Virtual_Ancillary_Bus", p_nom=power_mw, p_max_pu=tier3_pu, marginal_cost=-afrr_up_cost * 0.1)

        afrr_down_cost = daily_df.get('forecast_afrr_down_price_mw', pd.Series(0.0, index=daily_df.index))
        n.add("Generator", "aFRR_Down_Reserve_Tier1", bus="Virtual_Ancillary_Bus", p_nom=power_mw, p_max_pu=tier1_pu, marginal_cost=-afrr_down_cost * 1.0)
        n.add("Generator", "aFRR_Down_Reserve_Tier2", bus="Virtual_Ancillary_Bus", p_nom=power_mw, p_max_pu=tier2_pu, marginal_cost=-afrr_down_cost * 0.6)
        n.add("Generator", "aFRR_Down_Reserve_Tier3", bus="Virtual_Ancillary_Bus", p_nom=power_mw, p_max_pu=tier3_pu, marginal_cost=-afrr_down_cost * 0.1)

        n.add("Generator", "Virtual_Sink", 
              bus="Virtual_Ancillary_Bus", 
              p_nom=100000, 
              p_min_pu=-1, 
              p_max_pu=0,
              marginal_cost=0.0)

        # Use true activation prices for aFRR
        afrr_up_act_reward = daily_df.get('forecast_afrr_up_activation_price_mwh', daily_df.get('forecast_price', pd.Series(0.0, index=daily_df.index)))
        afrr_down_act_reward = daily_df.get('forecast_afrr_down_activation_price_mwh', daily_df.get('forecast_price', pd.Series(0.0, index=daily_df.index)))
        
        n.add("Link", "aFRR_Up_Activation", 
              bus0="BESS_DC_Bus", 
              bus1="AC", 
              p_nom=power_mw, 
              efficiency=eff_dispatch, 
              marginal_cost=degradation_penalty - (afrr_up_act_reward - forecast_imb_price_series))

        n.add("Link", "aFRR_Down_Activation", 
              bus0="AC", 
              bus1="BESS_DC_Bus", 
              p_nom=power_mw, 
              efficiency=eff_store, 
              marginal_cost=grid_fee_import + afrr_down_act_reward)

        def extra_functionality(n, snapshots):
            m = n.model
            
            p_discharge = m.variables["Link-p"].sel(name="DA_Discharge")
            p_charge = m.variables["Link-p"].sel(name="DA_Charge")
            p_imb_dis_1 = m.variables["Link-p"].sel(name="Imbalance_Discharge_Tier1")
            p_imb_dis_2 = m.variables["Link-p"].sel(name="Imbalance_Discharge_Tier2")
            p_imb_dis_3 = m.variables["Link-p"].sel(name="Imbalance_Discharge_Tier3")
            
            p_imb_chg_1 = m.variables["Link-p"].sel(name="Imbalance_Charge_Tier1")
            p_imb_chg_2 = m.variables["Link-p"].sel(name="Imbalance_Charge_Tier2")
            p_imb_chg_3 = m.variables["Link-p"].sel(name="Imbalance_Charge_Tier3")
            
            p_afrr_up_act = m.variables["Link-p"].sel(name="aFRR_Up_Activation")
            p_afrr_down_act = m.variables["Link-p"].sel(name="aFRR_Down_Activation")
            
            fcr_res = m.variables["Generator-p"].sel(name="FCR_Reserve")
            afrr_up_1 = m.variables["Generator-p"].sel(name="aFRR_Up_Reserve_Tier1")
            afrr_up_2 = m.variables["Generator-p"].sel(name="aFRR_Up_Reserve_Tier2")
            afrr_up_3 = m.variables["Generator-p"].sel(name="aFRR_Up_Reserve_Tier3")
            
            afrr_down_1 = m.variables["Generator-p"].sel(name="aFRR_Down_Reserve_Tier1")
            afrr_down_2 = m.variables["Generator-p"].sel(name="aFRR_Down_Reserve_Tier2")
            afrr_down_3 = m.variables["Generator-p"].sel(name="aFRR_Down_Reserve_Tier3")

            # 1. Total Inverter Power Constraints (DA + Imbalance + Reserve + Activation)
            max_p_out = p_discharge + p_imb_dis_1 + p_imb_dis_2 + p_imb_dis_3 + fcr_res + afrr_up_1 + afrr_up_2 + afrr_up_3 + p_afrr_up_act
            m.add_constraints(max_p_out <= power_mw, name="Inverter_Max_Discharge_Power")

            max_p_in = p_charge + p_imb_chg_1 + p_imb_chg_2 + p_imb_chg_3 + fcr_res + afrr_down_1 + afrr_down_2 + afrr_down_3 + p_afrr_down_act
            m.add_constraints(max_p_in <= power_mw, name="Inverter_Max_Charge_Power")

            # 2. Energy/SOC Constraints for Reserves
            # FCR requires +/- 1C for 15 minutes = 0.25h
            # aFRR Up requires 1C for 15 minutes = 0.25h
            # aFRR Down requires 1C for 15 minutes = 0.25h
            soc = m.variables["Store-e"].sel(name="Battery")
            e_min = energy_mwh * (1.0 - dod_pu)
            
            # FCR requires energy in both directions (headroom and footroom)
            m.add_constraints(soc - (fcr_res * 0.25) - ((afrr_up_1 + afrr_up_2 + afrr_up_3) * 0.25) >= e_min, name="Reserve_Energy_Footroom")
            m.add_constraints(soc + (fcr_res * 0.25) + ((afrr_down_1 + afrr_down_2 + afrr_down_3) * 0.25) <= energy_mwh, name="Reserve_Energy_Headroom")

            # 3. Virtual Sink for Financial Balance of Virtual Ancillary Bus
            v_sink = m.variables["Generator-p"].sel(name="Virtual_Sink")
            m.add_constraints(fcr_res + afrr_up_1 + afrr_up_2 + afrr_up_3 + afrr_down_1 + afrr_down_2 + afrr_down_3 + v_sink == 0, name="Virtual_Bus_Balance")

            # 4. Activation Ratio Links
            activation_ratio = daily_df.get('forecast_activation_ratio', pd.Series(0.05, index=daily_df.index)).values
            import xarray as xr
            ar_xr = xr.DataArray(activation_ratio, coords=[snapshots], dims=["snapshot"])
            
            m.add_constraints(p_afrr_up_act == (afrr_up_1 + afrr_up_2 + afrr_up_3) * ar_xr, name="aFRR_Up_Activation_Ratio")
            m.add_constraints(p_afrr_down_act == (afrr_down_1 + afrr_down_2 + afrr_down_3) * ar_xr, name="aFRR_Down_Activation_Ratio")

        try:
            status, condition = n.optimize(
                solver_name='highs', 
                solver_options={'log_to_console': False, 'output_flag': False},
                extra_functionality=extra_functionality
            )
        except Exception as e:
            logging.warning(f"HiGHS solver failed or is not available. Falling back to GLPK. Error: {e}")
            status, condition = n.optimize(solver_name='glpk', extra_functionality=extra_functionality)

        logging.info(f"Optimization status: {status} - {condition}")

        da_discharge = n.links_t.p0["DA_Discharge"].values
        da_charge = n.links_t.p0["DA_Charge"].values
        soc = n.stores_t.e["Battery"].values

        current_soc = soc[-1]

        dispatch_df = pd.DataFrame({
            'p_dispatch': da_discharge,
            'p_store': da_charge,
            'state_of_charge': soc,
            'price': daily_df['price'],
            'forecast_price': daily_df['forecast_price'],
            'da_price_actual': daily_df.get('da_price_actual', daily_df['price'])
        }, index=daily_df.index)
        
        # We need these for the frontend MVP metrics mapping
        dispatch_df['aFRR_Up_Reserve'] = (n.generators_t.p["aFRR_Up_Reserve_Tier1"] + n.generators_t.p["aFRR_Up_Reserve_Tier2"] + n.generators_t.p["aFRR_Up_Reserve_Tier3"]).values
        dispatch_df['aFRR_Up_Reserve_Tier1'] = n.generators_t.p["aFRR_Up_Reserve_Tier1"].values
        dispatch_df['aFRR_Up_Reserve_Tier2'] = n.generators_t.p["aFRR_Up_Reserve_Tier2"].values
        dispatch_df['aFRR_Up_Reserve_Tier3'] = n.generators_t.p["aFRR_Up_Reserve_Tier3"].values
        dispatch_df['aFRR_Down_Reserve'] = (n.generators_t.p["aFRR_Down_Reserve_Tier1"] + n.generators_t.p["aFRR_Down_Reserve_Tier2"] + n.generators_t.p["aFRR_Down_Reserve_Tier3"]).values
        dispatch_df['aFRR_Down_Reserve_Tier1'] = n.generators_t.p["aFRR_Down_Reserve_Tier1"].values
        dispatch_df['aFRR_Down_Reserve_Tier2'] = n.generators_t.p["aFRR_Down_Reserve_Tier2"].values
        dispatch_df['aFRR_Down_Reserve_Tier3'] = n.generators_t.p["aFRR_Down_Reserve_Tier3"].values
        
        dispatch_df['FCR_Reserve'] = n.generators_t.p["FCR_Reserve"].values
        dispatch_df['aFRR_Up_Activation'] = n.links_t.p0["aFRR_Up_Activation"].values
        dispatch_df['aFRR_Down_Activation'] = n.links_t.p0["aFRR_Down_Activation"].values
        
        dispatch_df['Imbalance_Charge'] = (n.links_t.p0["Imbalance_Charge_Tier1"] + n.links_t.p0["Imbalance_Charge_Tier2"] + n.links_t.p0["Imbalance_Charge_Tier3"]).values
        dispatch_df['Imbalance_Charge_Tier1'] = n.links_t.p0["Imbalance_Charge_Tier1"].values
        dispatch_df['Imbalance_Charge_Tier2'] = n.links_t.p0["Imbalance_Charge_Tier2"].values
        dispatch_df['Imbalance_Charge_Tier3'] = n.links_t.p0["Imbalance_Charge_Tier3"].values
        
        dispatch_df['Imbalance_Discharge'] = (n.links_t.p0["Imbalance_Discharge_Tier1"] + n.links_t.p0["Imbalance_Discharge_Tier2"] + n.links_t.p0["Imbalance_Discharge_Tier3"]).values
        dispatch_df['Imbalance_Discharge_Tier1'] = n.links_t.p0["Imbalance_Discharge_Tier1"].values
        dispatch_df['Imbalance_Discharge_Tier2'] = n.links_t.p0["Imbalance_Discharge_Tier2"].values
        dispatch_df['Imbalance_Discharge_Tier3'] = n.links_t.p0["Imbalance_Discharge_Tier3"].values
        dispatch_df['DA_Charge'] = n.links_t.p0["DA_Charge"].values
        dispatch_df['DA_Discharge'] = n.links_t.p0["DA_Discharge"].values
        
        all_dispatch_results.append(dispatch_df)

    final_df = pd.concat(all_dispatch_results)
    final_df.index = prices_df.index 
    return final_df
