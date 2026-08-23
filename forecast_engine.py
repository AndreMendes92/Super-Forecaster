"""
forecast_engine.py
-------------------
Same "brain" as before, just reshaped so a web API can call it:
instead of reading a CSV file, it takes data that's already in memory
(e.g. uploaded by a user through a browser, or pulled from another
platform's API).
"""

import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing


def run_forecast(df: pd.DataFrame, weeks_ahead: int = 6, seasonality_on: bool = True):
    """
    df: DataFrame with columns [week_start, volume]
    weeks_ahead: 1-12
    seasonality_on: whether to try to detect a repeating yearly pattern

    Returns: (history_df, forecast_df, used_seasonality: bool, note: str|None)
    """
    if not 1 <= weeks_ahead <= 12:
        raise ValueError("weeks_ahead must be between 1 and 12")

    df = df.copy()
    df["week_start"] = pd.to_datetime(df["week_start"])
    df = df.sort_values("week_start")
    series = df.set_index("week_start")["volume"]

    enough_history_for_seasonality = len(series) >= 104
    use_seasonal = seasonality_on and enough_history_for_seasonality

    note = None
    if seasonality_on and not enough_history_for_seasonality:
        note = (
            f"Only {len(series)} weeks of history provided — need at "
            "least 104 (2 years) to reliably detect yearly seasonality, "
            "so this forecast used trend-only instead."
        )

    model = ExponentialSmoothing(
        series,
        trend="add",
        seasonal="add" if use_seasonal else None,
        seasonal_periods=52 if use_seasonal else None,
        initialization_method="estimated",
    )
    fitted_model = model.fit()
    forecast_values = fitted_model.forecast(weeks_ahead)

    forecast_dates = pd.date_range(
        start=series.index[-1] + pd.Timedelta(weeks=1),
        periods=weeks_ahead,
        freq="W-MON",
    )
    forecast_df = pd.DataFrame({
        "week_start": forecast_dates,
        "forecast_volume": forecast_values.round().astype(int).values,
    })

    history_df = series.reset_index()
    history_df.columns = ["week_start", "volume"]

    return history_df, forecast_df, use_seasonal, note
