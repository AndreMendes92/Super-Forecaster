"""
forecast_engine.py
-------------------
The "brain" — supports multiple forecasting methods, compared side by
side, and now supports both weekly data (your own uploads) and
monthly data (common for real-world public datasets like government
housing/economic stats).

Methods available:
- holt_winters:   trend + seasonality (best when there's a real
                   repeating pattern). Original default method.
- arima:          a classic statistical time-series model (ARIMA).
                   Looks at how each value relates to recent past
                   values and past errors, rather than assuming a
                   fixed seasonal shape the way Holt-Winters does.
                   Good second opinion to compare against Holt-Winters.
- linear_trend:   a straight line through the data, ignoring
                   seasonality. Baseline to see "how much does
                   seasonality actually help?"
- moving_average: simplest possible forecast — projects forward the
                   flat average of the last few periods. Sanity-check
                   baseline.
"""

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

AVAILABLE_METHODS = {
    "holt_winters": "Holt-Winters (trend + seasonality)",
    "arima": "ARIMA (statistical time series)",
    "linear_trend": "Linear Trend (no seasonality)",
    "moving_average": "Moving Average (simple baseline)",
}

# Named forecast horizons so the app can talk about "short/medium/long
# term" instead of making people guess a raw number of periods.
# Counts are in *periods* of whatever freq is in use (months for "M",
# weeks for "W") — e.g. medium = 12 months for monthly StatCan data,
# but 12 weeks (~3 months) for weekly data.
HORIZON_PRESETS = {
    "short": 3,
    "medium": 12,
    "long": 36,
}

MAX_PERIODS_AHEAD = 60


def resolve_horizon(horizon) -> int:
    """Accepts either a preset name ('short'/'medium'/'long') or an int
    number of periods, and returns a validated period count."""
    if isinstance(horizon, str):
        if horizon not in HORIZON_PRESETS:
            raise ValueError(f"horizon must be one of {list(HORIZON_PRESETS)} or an integer")
        return HORIZON_PRESETS[horizon]
    periods = int(horizon)
    if not 1 <= periods <= MAX_PERIODS_AHEAD:
        raise ValueError(f"periods_ahead must be between 1 and {MAX_PERIODS_AHEAD}")
    return periods

# How each supported data frequency maps to seasonal cycle length and
# date-stepping behaviour.
FREQ_INFO = {
    "W": {"seasonal_periods": 52, "pandas_freq": "W-MON", "label": "weekly"},
    "M": {"seasonal_periods": 12, "pandas_freq": "MS", "label": "monthly"},
}


def _prep_series(df: pd.DataFrame) -> pd.Series:
    df = df.copy()
    df["week_start"] = pd.to_datetime(df["week_start"])
    df = df.sort_values("week_start")
    return df.set_index("week_start")["volume"]


def _future_dates(series: pd.Series, periods_ahead: int, freq: str) -> pd.DatetimeIndex:
    info = FREQ_INFO[freq]
    if freq == "W":
        start = series.index[-1] + pd.Timedelta(weeks=1)
    else:
        start = series.index[-1] + pd.DateOffset(months=1)
    return pd.date_range(start=start, periods=periods_ahead, freq=info["pandas_freq"])


def _forecast_holt_winters(series, periods_ahead, seasonality_on, seasonal_periods):
    enough_history = len(series) >= 2 * seasonal_periods
    use_seasonal = seasonality_on and enough_history

    note = None
    if seasonality_on and not enough_history:
        note = (
            f"Only {len(series)} periods of history — need at least "
            f"{2 * seasonal_periods} (2 full cycles) for reliable "
            "seasonality, so this used trend-only instead."
        )

    model = ExponentialSmoothing(
        series,
        trend="add",
        seasonal="add" if use_seasonal else None,
        seasonal_periods=seasonal_periods if use_seasonal else None,
        initialization_method="estimated",
    )
    fitted = model.fit()
    values = fitted.forecast(periods_ahead).round().astype(int).values
    return values, use_seasonal, note


def _forecast_arima(series, periods_ahead, seasonality_on, seasonal_periods):
    enough_history = len(series) >= 2 * seasonal_periods
    # Seasonal ARIMA with a long cycle (e.g. 52 weeks) is slow and prone
    # to not converging, so we only attempt the seasonal component for
    # shorter cycles (like monthly data, cycle=12). For weekly data we
    # run plain (non-seasonal) ARIMA and say so — it still gives a
    # useful second opinion alongside Holt-Winters, which does handle
    # the long seasonal cycle.
    attempt_seasonal = seasonality_on and enough_history and seasonal_periods <= 12
    seasonal_order = (1, 1, 1, seasonal_periods) if attempt_seasonal else (0, 0, 0, 0)

    note = None
    if seasonality_on and seasonal_periods > 12:
        note = (
            "ARIMA here runs without the long seasonal cycle (fitting "
            "seasonal ARIMA on a 52-period cycle is slow and often "
            "unstable) — treat it as a non-seasonal second opinion "
            "alongside Holt-Winters, which does model that cycle."
        )
    elif seasonality_on and not enough_history:
        note = (
            f"Only {len(series)} periods of history — need at least "
            f"{2 * seasonal_periods} for seasonal ARIMA, so this used "
            "non-seasonal ARIMA instead."
        )

    try:
        model = SARIMAX(
            series, order=(1, 1, 1), seasonal_order=seasonal_order,
            enforce_stationarity=False, enforce_invertibility=False,
        )
        fitted = model.fit(disp=False)
    except Exception:
        # Fallback to a simpler, near-always-stable spec if the fancier
        # one fails to converge on this particular dataset.
        model = SARIMAX(
            series, order=(1, 1, 0), seasonal_order=(0, 0, 0, 0),
            enforce_stationarity=False, enforce_invertibility=False,
        )
        fitted = model.fit(disp=False)
        seasonal_order = (0, 0, 0, 0)
        note = (note + " " if note else "") + "(Fell back to a simpler ARIMA spec after the first one didn't converge cleanly.)"

    values = fitted.forecast(periods_ahead)
    values = np.round(np.maximum(values, 0)).astype(int)
    return values, seasonal_order != (0, 0, 0, 0), note


def _forecast_linear_trend(series, periods_ahead, seasonality_on, seasonal_periods):
    x = np.arange(len(series))
    y = series.values
    slope, intercept = np.polyfit(x, y, 1)
    future_x = np.arange(len(series), len(series) + periods_ahead)
    values = (slope * future_x + intercept).round().astype(int)
    values = np.maximum(values, 0)
    return values, False, None


def _forecast_moving_average(series, periods_ahead, seasonality_on, seasonal_periods, window: int = 4):
    window = min(window, len(series))
    avg = series.tail(window).mean()
    values = np.full(periods_ahead, round(avg)).astype(int)
    note = f"Flat projection of the average of the last {window} periods ({round(avg)})."
    return values, False, note


_METHOD_FUNCS = {
    "holt_winters": _forecast_holt_winters,
    "arima": _forecast_arima,
    "linear_trend": _forecast_linear_trend,
    "moving_average": _forecast_moving_average,
}


def run_multi_forecast(
    df: pd.DataFrame,
    weeks_ahead: int = 6,
    seasonality_on: bool = True,
    methods: list[str] = None,
    freq: str = "W",
):
    """
    Runs one or more forecasting methods on the same historical data.

    weeks_ahead: how many periods ahead to forecast. Accepts either an
    int (1-60) or a horizon preset name ("short"/"medium"/"long" — see
    HORIZON_PRESETS). Named weeks_ahead for backward compatibility,
    but represents "periods" generically — weeks if freq="W", months
    if freq="M".
    freq: "W" (weekly, default) or "M" (monthly — use for most
    real-world public datasets, which are usually monthly).

    Returns: (history_df, results, unknown_methods)
    """
    if methods is None:
        methods = ["holt_winters"]
    if freq not in FREQ_INFO:
        raise ValueError(f"freq must be one of {list(FREQ_INFO)}")
    weeks_ahead = resolve_horizon(weeks_ahead)

    unknown = [m for m in methods if m not in AVAILABLE_METHODS]
    known = [m for m in methods if m in AVAILABLE_METHODS]

    if not known:
        raise ValueError(f"No valid methods given. Available: {list(AVAILABLE_METHODS)}")

    series = _prep_series(df)
    seasonal_periods = FREQ_INFO[freq]["seasonal_periods"]
    future_dates = _future_dates(series, weeks_ahead, freq)

    results = {}
    for method in known:
        values, used_seasonality, note = _METHOD_FUNCS[method](
            series, weeks_ahead, seasonality_on, seasonal_periods
        )
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
