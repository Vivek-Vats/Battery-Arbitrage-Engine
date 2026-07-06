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
        if 'Wind' in col or 'Solar' in col or 'Load' in col:
            df[col] = df[col].ffill().fillna(0)

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
    hourly_imbalance_avg = df['imbalance_price_15m'].resample('h').transform('mean')
    df['target_residual'] = df['imbalance_price_15m'] - hourly_imbalance_avg
    
    df['target_anomaly'] = ((df['imbalance_price_15m'] > 200) | (df['imbalance_price_15m'] < 0)).astype(int)
    
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
    
    exogenous_cols = [col for col in df.columns if 'Wind' in col or 'Solar' in col or 'Load' in col]
    features.extend(exogenous_cols)
    
    X_train = train[features]
    y_train_base = train['target_base']
    y_train_res = train['target_residual']
    y_train_anomaly = train['target_anomaly']
    
    X_test = test[features]
    
    # Meta-Model
    print("Training Anomaly Meta-Model...")
    anomaly_model = xgb.XGBClassifier(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42)
    anomaly_model.fit(X_train, y_train_anomaly)
    
    X_train_meta = X_train.copy()
    X_test_meta = X_test.copy()
    X_train_meta['AI_Optimized_Margin_EUR'] = 50.0 - (anomaly_model.predict_proba(X_train)[:, 1] * 50.0)
    X_test_meta['AI_Optimized_Margin_EUR'] = 50.0 - (anomaly_model.predict_proba(X_test)[:, 1] * 50.0)
    X_train_meta['AI_Optimized_Margin_EUR'] = X_train_meta['AI_Optimized_Margin_EUR'].clip(lower=0.0)
    X_test_meta['AI_Optimized_Margin_EUR'] = X_test_meta['AI_Optimized_Margin_EUR'].clip(lower=0.0)
    
    # Base Model
    print("Training Base Model...")
    base_model = xgb.XGBRegressor(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42, objective=asymmetric_mse_objective)
    base_model.fit(X_train_meta, y_train_base)
    
    # Residual Model
    print("Training Residual Model...")
    res_model = xgb.XGBRegressor(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42, objective=asymmetric_mse_objective)
    res_model.fit(X_train_meta, y_train_res)
    
    # Predictions
    print("Predicting on Test Set...")
    base_pred = base_model.predict(X_test_meta)
    res_pred = res_model.predict(X_test_meta)
    
    forecast_price = base_pred + res_pred
    
    # Output DB
    result_df = pd.DataFrame({
        'datetime': test.index,
        'price': test['imbalance_price_15m'].values,
        'forecast_price': forecast_price,
        'AI_Optimized_Margin_EUR': X_test_meta['AI_Optimized_Margin_EUR'].values
    }).set_index('datetime')
    
    print("Saving to database...")
    conn = sqlite3.connect(db_path)
    result_df.to_sql('forecasted_market_data', conn, if_exists='replace', index=True)
    conn.close()
    
    print(f"Predictions successfully saved to {db_path} in 'forecasted_market_data'.")

if __name__ == "__main__":
    main()
