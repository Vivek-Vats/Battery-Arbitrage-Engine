import pandas as pd
import pypsa
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def optimize_dispatch(prices_df, power_mw, energy_mwh, grid_fee_import, efficiency_store, efficiency_dispatch, depth_of_discharge, degradation_penalty):
    """
    Optimizes the dispatch of a Battery Energy Storage System (BESS) 
    using a fast, single-shot perfect foresight formulation via PyPSA StorageUnit.

    Args:
        prices_df (pd.DataFrame): DataFrame containing a 'price' column and a datetime index.
        power_mw (float): The power capacity of the BESS in MW.
        energy_mwh (float): The energy capacity of the BESS in MWh.
        grid_fee_import (float): The grid fee import cost in EUR/MWh.
        efficiency_store (float): Round-trip charging efficiency (percentage).
        efficiency_dispatch (float): Round-trip discharging efficiency (percentage).
        depth_of_discharge (float): Depth of Discharge limit (percentage).
        degradation_penalty (float): Cycle degradation cost penalty in EUR/MWh.

    Returns:
        pd.DataFrame: Consolidated dispatch DataFrame with p_dispatch, p_store, state_of_charge, and price.
    """
    # Convert percentage inputs into decimals
    eff_store = efficiency_store / 100.0
    eff_dispatch = efficiency_dispatch / 100.0
    
    # StorageUnit does not natively support minimum SOC constraints.
    # We enforce DoD by restricting the solver to only trade the 'usable' energy,
    # and treating the remaining energy as a mathematically dead floor and ceiling.
    usable_energy_mwh = energy_mwh * (depth_of_discharge / 100.0)
    dead_energy_mwh = energy_mwh - usable_energy_mwh
    lower_dead_energy_mwh = dead_energy_mwh / 2.0

    # Strip timezone to avoid PyPSA snapshot errors
    prices_df_naive = prices_df.copy()
    if prices_df_naive.index.tz is not None:
        prices_df_naive.index = prices_df_naive.index.tz_localize(None)

    n = pypsa.Network()
    n.set_snapshots(prices_df_naive.index)
    n.snapshot_weightings.loc[:] = 0.25

    # AC Bus
    n.add("Bus", "AC")

    # Grid Connection (Infinite Sink/Source)
    n.add("Generator", "Grid", 
          bus="AC", 
          p_nom=100000, 
          p_min_pu=-1, 
          marginal_cost=prices_df_naive['price'])

    # Dynamic StorageUnit Formulation
    n.add("StorageUnit", "lithium_ion_bess",
          bus="AC",
          p_nom=power_mw,
          max_hours=(usable_energy_mwh / power_mw),
          marginal_cost=degradation_penalty + grid_fee_import,
          efficiency_store=eff_store,
          efficiency_dispatch=eff_dispatch,
          cyclic_state_of_charge=True)

    # Solve & Extract
    try:
        status, condition = n.optimize(
            solver_name='highs', 
            solver_options={'log_to_console': False, 'output_flag': False}
        )
    except Exception as e:
        logging.warning(f"HiGHS solver failed or is not available. Falling back to GLPK. Error: {e}")
        status, condition = n.optimize(solver_name='glpk')

    logging.info(f"Optimization status: {status} - {condition}")

    p_dispatch = n.storage_units_t.p_dispatch['lithium_ion_bess']
    p_store = n.storage_units_t.p_store['lithium_ion_bess']
    
    # Shift the solver's SOC output up by the lower dead energy bound to correctly center the physical inventory
    soc = n.storage_units_t.state_of_charge['lithium_ion_bess'] + lower_dead_energy_mwh

    dispatch_df = pd.DataFrame({
        'p_dispatch': p_dispatch,
        'p_store': p_store,
        'state_of_charge': soc,
        'price': prices_df_naive['price']
    }, index=prices_df_naive.index)

    # Restore original timezone aware index if applicable
    dispatch_df.index = prices_df.index 

    return dispatch_df
