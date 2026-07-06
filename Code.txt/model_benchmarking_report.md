# Statistical Benchmarking Report

I have executed a deep statistical analysis on the current ML pipeline (`ml_forecaster.py`) to benchmark its performance and identify structural weaknesses.

## 1. Global Performance Metrics

| Metric | Value | Interpretation |
|--------|-------|----------------|
| **MAE** | 52.28 €/MWh | On average, predictions are off by ~€52. |
| **RMSE** | 159.59 €/MWh | Extremely high penalty from large outliers. |
| **R² Score** | **0.0362** | **CRITICAL:** The model only explains 3.6% of the variance. It is currently regressing heavily to the mean. |
| **Bias** | +16.08 €/MWh | Slight systematic over-prediction. |
| **Residual Kurtosis** | **410.1** | **CRITICAL:** Massive "fat tails" in the error distribution. The model is completely blind to black swan events. |

## 2. Error by Price Regime (Blind Spots)
The model's accuracy deteriorates catastrophically during extreme market events:

| Market State | True Price Range | Mean Absolute Error (MAE) |
|--------------|-----------------|---------------------------|
| **Normal** | 0 to 100 €/MWh | 41.99 €/MWh |
| **High** | 100 to 200 €/MWh | 55.09 €/MWh |
| **Negative** | < 0 €/MWh | **101.98 €/MWh** (Model fails to predict deep crashes) |
| **Extreme Spikes** | > 200 €/MWh | **449.59 €/MWh** (Model completely misses scarcity pricing) |

## 3. Temporal Error Distribution
The highest errors occur during specific grid stress hours:
* **Hour 12 (Noon)**: MAE of `83.40 €/MWh`. This is peak solar curtailment time (duck curve belly).
* **Hour 16-19 (Evening Peak)**: MAE ranges from `65.00` to `73.85 €/MWh`. This is when solar drops off and evening demand ramps up.

> [!WARNING]
> **Conclusion**
> The model is too conservative. Because XGBoost uses standard Mean Squared Error (MSE) mechanics under the hood, it avoids predicting extreme values (like €800 or -€200) to minimize its average penalty, defaulting to "safe" predictions around €80-€150. For a battery Arbitrage Engine, missing extreme spikes is fatal because those spikes contain 80% of the annual revenue.
