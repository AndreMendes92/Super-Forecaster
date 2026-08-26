"""
main.py — the web API
----------------------
GET  /methods                  -> lists available forecasting methods
GET  /dummy-data                -> fake historical data (for testing)
GET  /statcan/vector/{id}       -> real time series from Statistics Canada, by raw vector ID
GET  /statcan/geographies       -> every location StatCan's housing index covers
GET  /statcan/housing-types     -> the 3 index components StatCan tracks
GET  /statcan/price             -> real StatCan housing index series, by location name
GET  /repliers/price-history    -> real (or sandbox-sample) MLS sold-price stats
POST /forecast                   -> upload historical data + parameters,
                                     get back a forecast from one or more methods
POST /watches                    -> save a price alert
GET  /watches                    -> list an email's saved alerts
DELETE /watches/{id}             -> remove a saved alert
POST /run-alerts                 -> (secret-protected) checks every active alert
                                     against current prices and emails the ones
                                     that have been triggered. Meant to be called
                                     once a day by a GitHub Actions cron job.
"""

import io
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests as http_requests
from fastapi import FastAPI, UploadFile, File, Query, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from forecast_engine import run_multi_forecast, AVAILABLE_METHODS, HORIZON_PRESETS
from data_sources.statcan import fetch_statcan_vector
from data_sources.statcan_geography import list_geographies, list_housing_types, get_vector_id
from data_sources.repliers import fetch_repliers_price_history, COMMON_PROPERTY_TYPES
from db import init_db, get_db, Watch
from notify import send_alert_email, build_alert_message

app = FastAPI(title="Canada Housing Price Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


init_db()


def _parse_horizon(horizon: str):
    """horizon is either a preset name (short/medium/long) or a plain number of periods."""
    try:
        return int(horizon)
    except ValueError:
        return horizon


@app.get("/")
def root():
    return {"status": "ok", "message": "Canada Housing Price Tracker API is running"}


@app.get("/methods")
def methods():
    return AVAILABLE_METHODS


@app.get("/horizons")
def horizons():
    """Named forecast horizons (short/medium/long) and how many periods each is."""
    return HORIZON_PRESETS


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


@app.get("/statcan/geographies")
def statcan_geographies():
    """Every location (Canada, provinces, CMAs/cities) StatCan's New Housing Price Index covers."""
    try:
        return list_geographies()
    except http_requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Statistics Canada's API: {e}")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/statcan/housing-types")
def statcan_housing_types():
    """The 3 index components StatCan tracks: Total (house and land), House only, Land only."""
    try:
        return list_housing_types()
    except http_requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Statistics Canada's API: {e}")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/statcan/price")
def statcan_price(
    geography: str = Query(..., description="e.g. 'Toronto', 'British Columbia', 'Canada'"),
    housing_type: str = Query("Total (house and land)"),
    periods: int = Query(120, ge=8, le=500),
):
    """Real StatCan New Housing Price Index series, looked up by location name (no vector ID needed)."""
    try:
        match = get_vector_id(geography, housing_type)
        df = fetch_statcan_vector(match["vector_id"], latest_n=periods)
    except http_requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Statistics Canada's API: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "vector_id": match["vector_id"],
        "geography": match["geography"],
        "housing_type": match["housing_type"],
        "week_start": [d.strftime("%Y-%m-%d") for d in df["period_start"]],
        "volume": df["value"].tolist(),
    }


@app.get("/repliers/property-types")
def repliers_property_types():
    """Common property type values to populate a dropdown with."""
    return COMMON_PROPERTY_TYPES


@app.get("/repliers/price-history")
def repliers_price_history(
    city: str = Query(..., description="City name, e.g. 'Vancouver'"),
    property_type: str = Query(None, description="e.g. 'Condo Apt', 'Detached' — omit for all types"),
    months_back: int = Query(24, ge=6, le=120),
    stat: str = Query("avg", pattern="^(avg|med)$", description="avg or med sold price"),
):
    try:
        df = fetch_repliers_price_history(
            city=city, property_type=property_type, months_back=months_back, stat=stat
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except http_requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Repliers API error: {e}")
    except http_requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Repliers API: {e}")

    return {
        "city": city,
        "property_type": property_type,
        "stat": stat,
        "week_start": [d.strftime("%Y-%m-%d") for d in df["period_start"]],
        "volume": df["value"].tolist(),
        "sample_counts": df["count"].tolist(),
    }


@app.post("/forecast")
async def forecast(
    file: UploadFile = File(..., description="CSV with columns: week_start, volume"),
    horizon: str = Query("medium", description="'short', 'medium', 'long', or a number of periods (1-60)"),
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
            df, weeks_ahead=_parse_horizon(horizon), seasonality_on=seasonality_on,
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


# ---------------------------------------------------------------------
# Watches (saved price alerts)
# ---------------------------------------------------------------------

class WatchCreate(BaseModel):
    email: EmailStr
    data_source: str  # "statcan" or "repliers"
    geography: str
    property_type: str | None = None
    target_price: float
    direction: str  # "below" or "above"
    value_unit: str = "cad"  # "cad" or "index"
    label: str | None = None


class WatchOut(BaseModel):
    id: int
    email: str
    data_source: str
    geography: str
    property_type: str | None
    target_price: float
    direction: str
    value_unit: str
    label: str | None
    active: bool
    created_at: datetime
    last_notified_at: datetime | None
    last_checked_value: float | None

    class Config:
        from_attributes = True


@app.post("/watches", response_model=WatchOut)
def create_watch(watch: WatchCreate, db: Session = Depends(get_db)):
    if watch.direction not in ("below", "above"):
        raise HTTPException(status_code=400, detail="direction must be 'below' or 'above'")
    if watch.data_source not in ("statcan", "repliers"):
        raise HTTPException(status_code=400, detail="data_source must be 'statcan' or 'repliers'")

    db_watch = Watch(**watch.model_dump())
    db.add(db_watch)
    db.commit()
    db.refresh(db_watch)
    return db_watch


@app.get("/watches", response_model=list[WatchOut])
def list_watches(email: EmailStr = Query(...), db: Session = Depends(get_db)):
    return db.query(Watch).filter(Watch.email == email, Watch.active == True).order_by(Watch.created_at.desc()).all()  # noqa: E712


@app.delete("/watches/{watch_id}")
def delete_watch(watch_id: int, email: EmailStr = Query(..., description="Must match the alert's owner email"), db: Session = Depends(get_db)):
    db_watch = db.query(Watch).filter(Watch.id == watch_id, Watch.email == email).first()
    if not db_watch:
        raise HTTPException(status_code=404, detail="No alert with that ID for that email.")
    db.delete(db_watch)
    db.commit()
    return {"status": "deleted", "id": watch_id}


# ---------------------------------------------------------------------
# Daily alert check — called by a scheduled GitHub Actions job
# ---------------------------------------------------------------------

def _verify_alerts_secret(x_alerts_secret: str | None = Header(None)):
    expected = os.environ.get("ALERTS_SECRET")
    if not expected:
        raise HTTPException(status_code=500, detail="ALERTS_SECRET is not configured on the backend.")
    if x_alerts_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Alerts-Secret header.")


def _current_value_for_watch(w: Watch) -> float:
    if w.data_source == "statcan":
        match = get_vector_id(w.geography, w.property_type or "Total (house and land)")
        df = fetch_statcan_vector(match["vector_id"], latest_n=1)
        return float(df["value"].iloc[-1])
    else:
        df = fetch_repliers_price_history(city=w.geography, property_type=w.property_type, months_back=6, stat="avg")
        return float(df["value"].iloc[-1])


@app.post("/run-alerts", dependencies=[Depends(_verify_alerts_secret)])
def run_alerts(db: Session = Depends(get_db)):
    """
    Checks every active watch against the latest price/index value and
    emails the ones whose condition is now met. Skips re-notifying a
    watch two days in a row so an already-triggered alert doesn't spam
    your inbox daily — it fires again only if the value moves back and
    then re-crosses the target.
    """
    checked, triggered, errors = 0, 0, []

    for w in db.query(Watch).filter(Watch.active == True).all():  # noqa: E712
        checked += 1
        try:
            current_value = _current_value_for_watch(w)
        except Exception as e:
            errors.append({"watch_id": w.id, "error": str(e)})
            continue

        w.last_checked_value = current_value
        condition_met = (current_value < w.target_price) if w.direction == "below" else (current_value > w.target_price)

        already_notified_this_state = w.last_notified_at is not None
        if condition_met and not already_notified_this_state:
            try:
                subject, body = build_alert_message(w, current_value)
                send_alert_email(w.email, subject, body)
                w.last_notified_at = datetime.now(timezone.utc)
                triggered += 1
            except Exception as e:
                errors.append({"watch_id": w.id, "error": f"email failed: {e}"})
        elif not condition_met:
            # Condition no longer met — clear notified state so a future
            # re-crossing triggers a fresh email instead of staying silent.
            w.last_notified_at = None

    db.commit()
    return {"checked": checked, "triggered": triggered, "errors": errors}
