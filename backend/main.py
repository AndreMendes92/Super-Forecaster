"""
main.py — the web API
----------------------
GET  /methods        -> lists which forecasting methods are available
GET  /dummy-data      -> returns fake historical data (for testing)
POST /forecast        -> upload historical data + parameters,
                          get back a forecast from one or more methods
"""

import io
import numpy as np
import pandas as pd
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from forecast_engine import run_multi_forecast, AVAILABLE_METHODS

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
    """List available forecasting methods, for the frontend to build a picker."""
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


@app.post("/forecast")
async def forecast(
    file: UploadFile = File(..., description="CSV with columns: week_start, volume"),
    weeks_ahead: int = Query(6, ge=1, le=12, description="How many weeks to forecast (1-12)"),
    seasonality_on: bool = Query(True, description="Factor in repeating yearly pattern (Holt-Winters only)"),
    methods: str = Query("holt_winters", description="Comma-separated method keys, e.g. 'holt_winters,linear_trend'"),
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
            df, weeks_ahead=weeks_ahead, seasonality_on=seasonality_on, methods=method_list
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    response = {
        "history": [
            {"week_start": row.week_start.strftime("%Y-%m-%d"), "volume": int(row.volume)}
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
