"""
main.py — the web API
----------------------
This turns forecast_engine.py into something a website (or Postman,
or curl, or anything) can call over the internet.

Two endpoints:
  GET  /dummy-data          -> returns fake historical data (for testing)
  POST /forecast            -> upload historical data + parameters,
                                get back a forecast

Run it locally with:
    uvicorn main:app --reload
Then open http://127.0.0.1:8000/docs to see and try the API in your
browser (FastAPI builds that page for you automatically).
"""

import io
import numpy as np
import pandas as pd
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from forecast_engine import run_forecast

app = FastAPI(title="Forecast Tool API")

# CORS = permission for a website running on a different address (like
# your Streamlit frontend, or later a hosted frontend) to call this API.
# "*" means "allow any website" — fine while building, you can restrict
# it later to just your own frontend's address.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "ok", "message": "Forecast Tool API is running"}


@app.get("/dummy-data")
def dummy_data(weeks_of_history: int = Query(104, ge=8, le=260)):
    """Generates fake weekly volume data (trend + seasonality + noise)."""
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


class ForecastResponse(BaseModel):
    history: list
    forecast: list
    used_seasonality: bool
    note: str | None


@app.post("/forecast", response_model=ForecastResponse)
async def forecast(
    file: UploadFile = File(..., description="CSV with columns: week_start, volume"),
    weeks_ahead: int = Query(6, ge=1, le=12, description="How many weeks to forecast (1-12)"),
    seasonality_on: bool = Query(True, description="Factor in repeating yearly pattern"),
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

    try:
        history_df, forecast_df, used_seasonality, note = run_forecast(
            df, weeks_ahead=weeks_ahead, seasonality_on=seasonality_on
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "history": [
            {"week_start": row.week_start.strftime("%Y-%m-%d"), "volume": int(row.volume)}
            for row in history_df.itertuples()
        ],
        "forecast": [
            {"week_start": row.week_start.strftime("%Y-%m-%d"), "forecast_volume": int(row.forecast_volume)}
            for row in forecast_df.itertuples()
        ],
        "used_seasonality": used_seasonality,
        "note": note,
    }
