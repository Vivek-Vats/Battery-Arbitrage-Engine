import pypsa
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def optimize_dispatch(prices_df, power_mw, energy_mwh):
    """
    Optimizes the dispatch of a Battery Energy Storage System (BESS) 
    for market arbitrage using linear programming.

    Args:
        prices_df (pd.DataFrame): DataFrame containing a 'price' column and a datetime index.
        power_mw (float): The power capacity of the BESS in MW.
        energy_mwh (float): The energy capacity of the BESS in MWh.

    Returns:
        pd.DataFrame: Consolidated dispatch DataFrame with p_dispatch, p_store, state_of_charge, and price.
    """
    n = pypsa.Network()
    n.set_snapshots(prices_df.index)
    
    n.add("Bus", "AC")
    
    n.add("Generator", "Grid", 
          bus="AC", 
          p_nom=100000, 
          p_min_pu=-1, 
          marginal_cost=prices_df['price'])
    
    n.add("StorageUnit", "lithium_ion_bess", 
          bus="AC", 
          p_nom=power_mw, 
          max_hours=(energy_mwh / power_mw), 
          marginal_cost=2.0, 
          state_of_charge_initial=0.0, 
          cyclic_state_of_charge=True)
    
    try:
        status = n.optimize(solver_name='highs')
    except Exception as e:
        logging.warning(f"HiGHS solver failed or is not available. Falling back to GLPK. Error: {e}")
        status = n.optimize(solver_name='glpk')
        
    logging.info(f"Optimization status: {status}")
    
    p_dispatch = n.storage_units_t.p_dispatch['lithium_ion_bess']
    p_store = n.storage_units_t.p_store['lithium_ion_bess']
    soc = n.storage_units_t.state_of_charge['lithium_ion_bess']
    
    dispatch_df = pd.DataFrame({
        'p_dispatch': p_dispatch,
        'p_store': p_store,
        'state_of_charge': soc,
        'price': prices_df['price']
    }, index=prices_df.index)
    
    return dispatch_df
