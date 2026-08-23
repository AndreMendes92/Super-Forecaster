"""
forecast_engine.py
-------------------
The "brain" — now supports multiple forecasting methods so you can
compare them side by side.

Methods available:
- holt_winters:   trend + seasonality (best when there's a real
                   repeating pattern, e.g. yearly cycles). This was
                   the original/default method.
- linear_trend:   a straight line through the data, ignoring
                   seasonality entirely. Good baseline to see "how
                   much does modeling seasonality actually help?"
- moving_average: the simplest possible forecast — just projects
                   forward the average of the last few weeks, flat.
                   Useful as a sanity-check baseline; if a fancier
                   method isn't beating this, it's not adding value.
"""

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

AVAILABLE_METHODS = {
    "holt_winters": "Holt-Winters (trend + seasonality)",
    "linear_trend": "Linear Trend (no seasonality)",
    "moving_average": "Moving Average (simple baseline)",
}


def _prep_series(df: pd.DataFrame) -> pd.Series:
    df = df.copy()
    df["week_start"] = pd.to_datetime(df["week_start"])
    df = df.sort_values("week_start")
    return df.set_index("week_start")["volume"]


def _future_dates(series: pd.Series, weeks_ahead: int) -> pd.DatetimeIndex:
    return pd.date_range(
        start=series.index[-1] + pd.Timedelta(weeks=1),
        periods=weeks_ahead,
        freq="W-MON",
    )


def _forecast_holt_winters(series: pd.Series, weeks_ahead: int, seasonality_on: bool):
    enough_history = len(series) >= 104
    use_seasonal = seasonality_on and enough_history

    note = None
    if seasonality_on and not enough_history:
        note = (
            f"Only {len(series)} weeks of history — need at least 104 "
            "(2 years) for reliable yearly seasonality, so this used "
            "trend-only instead."
        )

    model = ExponentialSmoothing(
        series,
        trend="add",
        seasonal="add" if use_seasonal else None,
        seasonal_periods=52 if use_seasonal else None,
        initialization_method="estimated",
    )
    fitted = model.fit()
    values = fitted.forecast(weeks_ahead).round().astype(int).values
    return values, use_seasonal, note


def _forecast_linear_trend(series: pd.Series, weeks_ahead: int):
    x = np.arange(len(series))
    y = series.values
    slope, intercept = np.polyfit(x, y, 1)
    future_x = np.arange(len(series), len(series) + weeks_ahead)
    values = (slope * future_x + intercept).round().astype(int)
    values = np.maximum(values, 0)
    return values, False, None


def _forecast_moving_average(series: pd.Series, weeks_ahead: int, window: int = 4):
    window = min(window, len(series))
    avg = series.tail(window).mean()
    values = np.full(weeks_ahead, round(avg)).astype(int)
    note = f"Flat projection of the average of the last {window} weeks ({round(avg)})."
    return values, False, note


_METHOD_FUNCS = {
    "holt_winters": lambda s, w, seasonality_on: _forecast_holt_winters(s, w, seasonality_on),
    "linear_trend": lambda s, w, seasonality_on: _forecast_linear_trend(s, w),
    "moving_average": lambda s, w, seasonality_on: _forecast_moving_average(s, w),
}


def run_multi_forecast(
    df: pd.DataFrame,
    weeks_ahead: int = 6,
    seasonality_on: bool = True,
    methods: list[str] = None,
):
    """
    Runs one or more forecasting methods on the same historical data
    and returns them all, so they can be compared on one chart.

    Returns: (history_df, results, unknown_methods)
    results is a dict: { method_key: {"label", "forecast_df", "used_seasonality", "note"} }
    """
    if methods is None:
        methods = ["holt_winters"]

    unknown = [m for m in methods if m not in AVAILABLE_METHODS]
    known = [m for m in methods if m in AVAILABLE_METHODS]

    if not 1 <= weeks_ahead <= 12:
        raise ValueError("weeks_ahead must be between 1 and 12")
    if not known:
        raise ValueError(f"No valid methods given. Available: {list(AVAILABLE_METHODS)}")

    series = _prep_series(df)
    future_dates = _future_dates(series, weeks_ahead)

    results = {}
    for method in known:
        values, used_seasonality, note = _METHOD_FUNCS[method](series, weeks_ahead, seasonality_on)
        forecast_df = pd.DataFrame({"week_start": future_dates, "forecast_volume": values})
        results[method] = {
            "label": AVAILABLE_METHODS[method],
            "forecast_df": forecast_df,
            "used_seasonality": used_seasonality,
            "note": note,
        }

    history_df = series.reset_index()
    history_df.columns = ["week_start", "volume"]

    return history_df, results, unknown
