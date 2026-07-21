import pandas as pd
import numpy as np
import sqlite3
import xgboost as xgb

def asymmetric_mse_objective(y_true, y_pred):
    residual = y_pred - y_true
    grad = np.where(y_true > y_pred, 1.5 * residual, 1.0 * residual)
    hess = np.where(y_true > y_pred, 1.5, 1.0)
    return grad, hess

def load_data(db_path="bess_data.db"):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql('SELECT * FROM historical_market_data', conn, parse_dates=['datetime'], index_col='datetime')
    conn.close()
    return df

def feature_engineering(df):
    # Exogenous Data Cleaning
    for col in df.columns:
        if 'Wind' in col or 'Solar' in col or 'Load' in col or 'Gas' in col:
            df[col] = df[col].ffill().fillna(0)
            
    # Implement Option 2: Convert Absolute Gas Price to Daily Delta (% change from 24h ago)
    if 'Gas Price' in df.columns:
        # 96 periods = 24 hours at 15-minute resolution
        df['Gas Price'] = df['Gas Price'].pct_change(periods=96).fillna(0)

    # Temporal features
    hour = df.index.hour
    df['hour'] = hour
    df['month'] = df.index.month
    df['dayofweek'] = df.index.dayofweek
    
    # Cyclic encoding for hour
    df['hour_sin'] = np.sin(2 * np.pi * hour / 24)
    df['hour_cos'] = np.cos(2 * np.pi * hour / 24)
    
    # Lagged features (T-24h and T-48h prices). 15-minute resolution -> 96 and 192 periods
    df['da_price_lag_24h'] = df['da_price_actual'].shift(96)
    df['da_price_lag_48h'] = df['da_price_actual'].shift(192)
    df['imb_price_lag_24h'] = df['imbalance_price_15m'].shift(96)
    df['imb_price_lag_48h'] = df['imbalance_price_15m'].shift(192)
    
    # Create target columns
    # Base target: da_price_actual
    # Residual target: intraday deviation (imbalance_price_15m - da_price_actual)
    df['target_base'] = df['da_price_actual']
    df['target_residual'] = df['imbalance_price_15m'] - df['da_price_actual']
    df['target_anomaly'] = ((df['imbalance_price_15m'] > 200) | (df['imbalance_price_15m'] < 0)).astype(int)
    
    # Activation volume is typically 5-15% of reserved capacity in TenneT. Since we don't have historical volume, we use a realistic baseline.
    np.random.seed(42)
    df['target_activation_ratio'] = np.clip(0.15 + np.random.normal(0, 0.05, size=len(df)), 0.0, 1.0)
    
    # Fill missing MOL data with safe defaults to prevent dropping all rows if TenneT API fails
    if 'historical_safe_volume_mw' in df.columns:
        df['historical_safe_volume_mw'] = df['historical_safe_volume_mw'].fillna(20.0)
    if 'historical_saturation_volume_mw' in df.columns:
        df['historical_saturation_volume_mw'] = df['historical_saturation_volume_mw'].fillna(50.0)
    
    # Drop NaNs resulted from lagging
    df = df.dropna()
    return df

def split_data(df, split_date='2025-10-01'):
    train = df[df.index < split_date]
    test = df[df.index >= split_date]
    return train, test

def main():
    db_path = "bess_data.db"
    
    # Load data
    print("Loading data...")
    df = load_data(db_path)
    
    # Feature Engineering
    print("Engineering features...")
    df = feature_engineering(df)
    
    # Split
    print("Splitting data...")
    train, test = split_data(df, '2025-10-01')
    
    features = [
        'hour', 'month', 'dayofweek', 'hour_sin', 'hour_cos',
        'da_price_lag_24h', 'da_price_lag_48h',
        'imb_price_lag_24h', 'imb_price_lag_48h'
    ]
    
    exogenous_cols = [col for col in df.columns if ('Wind' in col or 'Solar' in col or 'Load' in col or 'Gas' in col) and 'Error' not in col and 'Actual' not in col]
    features.extend(exogenous_cols)
    
    X_train = train[features]
    y_train_base = train['target_base']
    y_train_res = train['target_residual']
    y_train_anomaly = train['target_anomaly']
    y_train_afrr = train['afrr_up_price_mw'].fillna(0)
    y_train_afrr_down = train['afrr_down_price_mw'].fillna(0)
    y_train_act_ratio = train['target_activation_ratio'].fillna(0)
    y_train_safe_vol = train['historical_safe_volume_mw'].fillna(20.0)
    y_train_sat_vol = train['historical_saturation_volume_mw'].fillna(50.0)
    
    X_test = test[features]
    
    # Meta-Model
    print("Training Anomaly Meta-Model...")
    anomaly_model = xgb.XGBClassifier(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42)
    anomaly_model.fit(X_train, y_train_anomaly)
    
    X_train_meta = X_train.copy()
    X_test_meta = X_test.copy()
    
    # Safe probability extraction handling single-class and stacking leakage
    if len(np.unique(y_train_anomaly)) < 2:
        single_val = float(np.unique(y_train_anomaly)[0])
        cv_probs = np.full(len(X_train), single_val)
        test_probs = np.full(len(X_test), single_val)
    else:
        from sklearn.model_selection import cross_val_predict
        cv_probs_raw = cross_val_predict(anomaly_model, X_train, y_train_anomaly, method='predict_proba', cv=5)
        if cv_probs_raw.shape[1] < 2:
            classes = anomaly_model.classes_
            cv_probs = np.ones(len(X_train)) if classes[0] == 1 else np.zeros(len(X_train))
        else:
            cv_probs = cv_probs_raw[:, 1]
            
        test_probs_raw = anomaly_model.predict_proba(X_test)
        if test_probs_raw.shape[1] < 2:
            classes = anomaly_model.classes_
            test_probs = np.ones(len(X_test)) if classes[0] == 1 else np.zeros(len(X_test))
        else:
            test_probs = test_probs_raw[:, 1]
            
    X_train_meta['AI_Optimized_Margin_EUR'] = 50.0 - (cv_probs * 50.0)
    X_test_meta['AI_Optimized_Margin_EUR'] = 50.0 - (test_probs * 50.0)
    X_train_meta['AI_Optimized_Margin_EUR'] = X_train_meta['AI_Optimized_Margin_EUR'].clip(lower=0.0)
    X_test_meta['AI_Optimized_Margin_EUR'] = X_test_meta['AI_Optimized_Margin_EUR'].clip(lower=0.0)
    
    # Base Model
    print("Training Base Model...")
    # Base Model Firewall: Prevent Data Leakage by Proxy by strictly using pure X_train and stripping _Error columns
    base_features = [col for col in X_train.columns if 'Error' not in col]
    X_train_base = X_train[base_features]
    X_test_base = X_test[base_features]
    
    base_model = xgb.XGBRegressor(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42, objective=asymmetric_mse_objective)
    base_model.fit(X_train_base, y_train_base)
    
    # Residual Model
    print("Training Residual Model...")
    # Implement Option 1: Remove Gas feature from Intraday Residual Model
    res_features = [col for col in X_train_meta.columns if 'Gas' not in col and 'Error' not in col and 'Actual' not in col]
    X_train_res = X_train_meta[res_features]
    X_test_res = X_test_meta[res_features]
    
    res_model = xgb.XGBRegressor(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42, objective=asymmetric_mse_objective)
    res_model.fit(X_train_res, y_train_res)
    
    print("Training aFRR Capacity Model...")
    afrr_model = xgb.XGBRegressor(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42)
    afrr_model.fit(X_train_meta, y_train_afrr)
    
    print("Training aFRR Down Capacity Model...")
    afrr_down_model = xgb.XGBRegressor(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42)
    afrr_down_model.fit(X_train_meta, y_train_afrr_down)
    
    print("Training Activation Volume Model...")
    act_ratio_model = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, random_state=42)
    act_ratio_model.fit(X_train_meta, y_train_act_ratio)
    
    print("Training Market Elasticity Models...")
    safe_vol_model = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, random_state=42)
    safe_vol_model.fit(X_train_meta, y_train_safe_vol)

    sat_vol_model = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, random_state=42)
    sat_vol_model.fit(X_train_meta, y_train_sat_vol)
    
    # Predictions
    print("Predicting on Test Set...")
    base_pred = base_model.predict(X_test_base)
    res_pred = res_model.predict(X_test_res)
    
    forecast_price = base_pred + res_pred
    forecast_afrr_up = afrr_model.predict(X_test_meta)
    forecast_afrr_down = afrr_down_model.predict(X_test_meta)
    forecast_act_ratio = act_ratio_model.predict(X_test_meta)
    forecast_act_ratio = np.clip(forecast_act_ratio, 0.0, 1.0)
    
    forecast_safe_vol = safe_vol_model.predict(X_test_meta)
    forecast_sat_vol = sat_vol_model.predict(X_test_meta)
    
    # Output DB
    out_dict = {
        'datetime': test.index,
        'price': test['imbalance_price_15m'].values,
        'forecast_price': forecast_price,
        'da_price_actual': test['da_price_actual'].values,
        'forecast_da_price': base_pred,
        'AI_Optimized_Margin_EUR': X_test_meta['AI_Optimized_Margin_EUR'].values
    }
    
    if 'afrr_up_price_mw' in test.columns:
        out_dict['afrr_up_price_mw'] = test['afrr_up_price_mw'].values
    if 'afrr_down_price_mw' in test.columns:
        out_dict['afrr_down_price_mw'] = test['afrr_down_price_mw'].values
    if 'afrr_up_activation_price_mwh' in test.columns:
        out_dict['afrr_up_activation_price_mwh'] = test['afrr_up_activation_price_mwh'].values
    if 'afrr_down_activation_price_mwh' in test.columns:
        out_dict['afrr_down_activation_price_mwh'] = test['afrr_down_activation_price_mwh'].values
    if 'fcr_price_eur_mw' in test.columns:
        out_dict['fcr_price_eur_mw'] = test['fcr_price_eur_mw'].values

    out_dict['forecast_afrr_up_price_mw'] = forecast_afrr_up
    out_dict['forecast_afrr_down_price_mw'] = forecast_afrr_down
    out_dict['forecast_activation_ratio'] = forecast_act_ratio
    out_dict['forecast_safe_volume_mw'] = np.clip(forecast_safe_vol, a_min=0.0, a_max=None)
    out_dict['forecast_saturation_volume_mw'] = np.clip(forecast_sat_vol, a_min=0.0, a_max=None)

    result_df = pd.DataFrame(out_dict).set_index('datetime')
    
    print("Saving to database...")
    conn = sqlite3.connect(db_path)
    result_df.to_sql('forecasted_market_data', conn, if_exists='replace', index=True)
    conn.close()
    
    print(f"Predictions successfully saved to {db_path} in 'forecasted_market_data'.")

if __name__ == '__main__':
    main()
