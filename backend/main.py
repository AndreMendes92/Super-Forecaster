"""
main.py — the web API
----------------------
GET  /methods              -> lists available forecasting methods
GET  /dummy-data            -> fake historical data (for testing)
GET  /statcan/vector/{id}   -> pulls a real time series from Statistics
                                Canada's free public API
POST /forecast              -> upload historical data + parameters,
                                get back a forecast from one or more methods
"""

import io
import numpy as np
import pandas as pd
import requests as http_requests
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from forecast_engine import run_multi_forecast, AVAILABLE_METHODS
from data_sources.statcan import fetch_statcan_vector

app = FastAPI(title="Forecast Tool API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "ok", "message": "Forecast Tool API is running"}


@app.get("/methods")
def methods():
    return AVAILABLE_METHODS


@app.get("/dummy-data")
def dummy_data(weeks_of_history: int = Query(104, ge=8, le=260)):
    np.random.seed(42)
    start_date = pd.Timestamp("2024-01-01")
    dates = pd.date_range(start=start_date, periods=weeks_of_history, freq="W-MON")

    base_volume = 1000
    trend = np.linspace(0, 300, weeks_of_history)
    seasonality = 200 * np.sin(2 * np.pi * (np.arange(weeks_of_history) / 52))
    noise = np.random.normal(0, 40, weeks_of_history)

    volume = np.round(np.maximum(base_volume + trend + seasonality + noise, 0)).astype(int)

    return {
        "week_start": [d.strftime("%Y-%m-%d") for d in dates],
        "volume": volume.tolist(),
    }


@app.get("/statcan/vector/{vector_id}")
def statcan_vector(
    vector_id: int,
    periods: int = Query(120, ge=8, le=500, description="How many recent data points to pull"),
):
    """
    Pulls a real time series from Statistics Canada's free public API.
    Find vector IDs on any StatCan table page — see data_sources/statcan.py
    for the step-by-step. Returns data shaped the same way as
    /dummy-data, so it's a drop-in real-data replacement (monthly,
    not weekly — use freq=M when calling /forecast with this data).
    """
    try:
        df = fetch_statcan_vector(vector_id, latest_n=periods)
    except http_requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Statistics Canada's API: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "vector_id": vector_id,
        "week_start": [d.strftime("%Y-%m-%d") for d in df["period_start"]],
        "volume": df["value"].tolist(),
    }


@app.post("/forecast")
async def forecast(
    file: UploadFile = File(..., description="CSV with columns: week_start, volume"),
    weeks_ahead: int = Query(6, ge=1, le=12, description="How many periods ahead to forecast (1-12)"),
    seasonality_on: bool = Query(True, description="Factor in repeating seasonal pattern"),
    methods: str = Query("holt_winters", description="Comma-separated method keys"),
    freq: str = Query("W", pattern="^(W|M)$", description="Data frequency: W=weekly, M=monthly"),
):
    try:
        contents = await file.read()
        df = pd.read_csv(io.BytesIO(contents))
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read the uploaded file as CSV")

    if "week_start" not in df.columns or "volume" not in df.columns:
        raise HTTPException(
            status_code=400,
            detail="CSV must have columns named exactly 'week_start' and 'volume'",
        )

    method_list = [m.strip() for m in methods.split(",") if m.strip()]

    try:
        history_df, results, unknown = run_multi_forecast(
            df, weeks_ahead=weeks_ahead, seasonality_on=seasonality_on,
            methods=method_list, freq=freq,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    response = {
        "history": [
            {"week_start": row.week_start.strftime("%Y-%m-%d"), "volume": float(row.volume)}
            for row in history_df.itertuples()
        ],
        "forecasts": {},
        "unknown_methods": unknown,
    }

    for method_key, data in results.items():
        fdf = data["forecast_df"]
        response["forecasts"][method_key] = {
            "label": data["label"],
            "used_seasonality": data["used_seasonality"],
            "note": data["note"],
            "values": [
                {"week_start": row.week_start.strftime("%Y-%m-%d"), "forecast_volume": int(row.forecast_volume)}
                for row in fdf.itertuples()
            ],
        }

    return response
