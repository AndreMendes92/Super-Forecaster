"""
app.py — the screen you actually look at
------------------------------------------
Streamlit frontend for the Canada Housing Price Tracker. Talks to the
FastAPI backend over HTTP.

Run it locally with:
    streamlit run app.py
(make sure the backend is running too, from backend/: uvicorn main:app --reload)
"""

import io
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

API_URL = st.secrets.get("API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Canada Housing Price Tracker", layout="wide", page_icon="🏠")
st.title("🏠 Canada Housing Price Tracker")
st.caption(
    "Explore real Statistics Canada housing data (or sample MLS-style data), "
    "forecast short/medium/long term trends, and get emailed when a location "
    "hits the price you're waiting for."
)

METHOD_COLORS = ["#f97316", "#10b981", "#a855f7", "#ef4444"]
HORIZON_LABELS = {"short": "Short term (~3 months)", "medium": "Medium term (~12 months)", "long": "Long term (~3 years)"}


# ---------------------------------------------------------------------
# Small cached helpers to talk to the backend
# ---------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _get_json(path: str, params: dict | None = None):
    resp = requests.get(f"{API_URL}{path}", params=params or {}, timeout=45)
    resp.raise_for_status()
    return resp.json()


def _safe_get_json(path: str, params: dict | None = None, error_label: str = "backend"):
    try:
        return _get_json(path, params), None
    except requests.exceptions.HTTPError as e:
        # HTTPError is a RequestException subclass, so it must be checked
        # first — otherwise the broader except below always wins and we
        # lose the specific "detail" message our own backend sent back.
        try:
            detail = e.response.json().get("detail", str(e)) if e.response is not None else str(e)
        except ValueError:
            # The error body wasn't JSON at all — Render's own proxy
            # (not our app) served a raw HTML error page, usually
            # because the backend didn't respond in time. Give a plain
            # explanation instead of dumping HTML at the user.
            status = e.response.status_code if e.response is not None else "?"
            detail = (
                f"{error_label} didn't respond in time (HTTP {status} from the "
                "server hosting the backend, not from Statistics Canada itself). "
                "This is usually temporary — try again in a moment."
            )
        return None, detail
    except requests.exceptions.RequestException as e:
        return None, f"Couldn't reach {error_label}: {e}"


@st.cache_data(ttl=3600, show_spinner=False)
def _available_methods():
    try:
        resp = requests.get(f"{API_URL}/methods", timeout=45)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException:
        return {"holt_winters": "Holt-Winters (trend + seasonality)"}


def _run_forecast(week_start: list, volume: list, horizon: str, methods: list, freq: str, seasonality_on: bool):
    df = pd.DataFrame({"week_start": week_start, "volume": volume})
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    files = {"file": ("data.csv", buf.getvalue().encode(), "text/csv")}
    params = {
        "horizon": horizon,
        "seasonality_on": seasonality_on,
        "methods": ",".join(methods),
        "freq": freq,
    }
    resp = requests.post(f"{API_URL}/forecast", files=files, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _plot_history_and_forecast(history_df: pd.DataFrame, forecasts: dict, y_label: str):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=history_df["week_start"], y=history_df["volume"],
        mode="lines", name="Historical", line=dict(color="#2563eb"),
    ))
    for i, (method_key, fdata) in enumerate(forecasts.items()):
        color = METHOD_COLORS[i % len(METHOD_COLORS)]
        fdf = pd.DataFrame(fdata["values"])
        fig.add_trace(go.Scatter(
            x=fdf["week_start"], y=fdf["forecast_volume"],
            mode="lines+markers", name=fdata["label"],
            line=dict(color=color, dash="dash"),
        ))
        if fdata["note"]:
            st.info(f"**{fdata['label']}**: {fdata['note']}")
    fig.update_layout(
        title="Historical Data & Forecast",
        xaxis_title="Period", yaxis_title=y_label,
        hovermode="x unified", height=480,
    )
    st.plotly_chart(fig, width="stretch")


explore_tab, alerts_tab = st.tabs(["📈 Explore & Forecast", "🔔 My Alerts"])

# =======================================================================
# TAB 1 — Explore & Forecast
# =======================================================================
with explore_tab:
    available_methods = _available_methods()

    col_source, col_settings = st.columns([1, 1])
    with col_source:
        data_source = st.radio(
            "Data source",
            ["Statistics Canada (real data)", "Repliers MLS (condo/detached/etc. by city)"],
            help=(
                "StatCan's New Housing Price Index is real, free, government data, "
                "covering major cities — but it tracks NEW-build prices, not resale "
                "averages, and doesn't split out condos specifically. Repliers gives "
                "real per-property-type city data, but returns realistic SAMPLE data "
                "on a free key, not live listings, unless you add a paid key later."
            ),
        )

    geography = None
    property_type_value = None
    freq = "M"
    is_statcan = data_source.startswith("Statistics Canada")

    if is_statcan:
        geos, geos_err = _safe_get_json("/statcan/geographies", error_label="Statistics Canada")
        types, types_err = _safe_get_json("/statcan/housing-types", error_label="Statistics Canada")
        if geos_err or types_err:
            st.error(geos_err or types_err)
            st.stop()
        geo_names = [g["name"] for g in geos]
        type_names = [t["name"] for t in types]

        with col_settings:
            default_geo_idx = geo_names.index("Canada") if "Canada" in geo_names else 0
            geography = st.selectbox("Location", geo_names, index=default_geo_idx)
            property_type_value = st.selectbox(
                "Index component", type_names,
                help="'Total (house and land)' is the usual headline figure.",
            )
        periods_back = st.slider("Months of history to pull", min_value=24, max_value=300, value=96, step=12)
    else:
        with col_settings:
            geography = st.text_input("City", value="Toronto")
            prop_types, pt_err = _safe_get_json("/repliers/property-types", error_label="the backend")
            property_type_value = st.selectbox("Property type", ["All types"] + (prop_types or []))
            if property_type_value == "All types":
                property_type_value = None
        periods_back = st.slider("Months of history to pull", min_value=6, max_value=120, value=24, step=6)
        stat = st.radio("Statistic", ["avg", "med"], format_func=lambda x: "Average" if x == "avg" else "Median", horizontal=True)

    st.divider()
    col_h, col_m, col_s = st.columns([1, 1.4, 0.8])
    with col_h:
        horizon = st.radio("Forecast horizon", list(HORIZON_LABELS.keys()), format_func=lambda k: HORIZON_LABELS[k], horizontal=True)
    with col_m:
        selected_labels = st.multiselect(
            "Forecasting method(s) — pick more than one to compare",
            options=list(available_methods.values()),
            default=[available_methods.get("holt_winters", list(available_methods.values())[0])],
        )
        label_to_key = {v: k for k, v in available_methods.items()}
        selected_methods = [label_to_key[l] for l in selected_labels]
    with col_s:
        seasonality_on = st.toggle("Seasonality", value=True)

    if not selected_methods:
        st.info("Pick at least one forecasting method above.")
    elif is_statcan and geography and property_type_value:
        with st.spinner(f"Pulling {property_type_value} for {geography} from Statistics Canada..."):
            data, err = _safe_get_json(
                "/statcan/price",
                {"geography": geography, "housing_type": property_type_value, "periods": int(periods_back)},
                error_label="Statistics Canada",
            )
        if err:
            st.error(err)
        else:
            st.caption(
                f"Matched **{data['geography']} — {data['housing_type']}** "
                f"(StatCan vector v{data['vector_id']}). Values are index points "
                "(not dollars) — 100 was the baseline in StatCan's reference period."
            )
            try:
                result = _run_forecast(data["week_start"], data["volume"], horizon, selected_methods, "M", seasonality_on)
            except requests.exceptions.RequestException as e:
                st.error(f"Forecast failed: {e}")
            else:
                history_df = pd.DataFrame(result["history"])
                col1, col2, col3 = st.columns(3)
                col1.metric("Months of history", len(history_df))
                col2.metric("Latest index value", f"{history_df['volume'].iloc[-1]:.1f}")
                col3.metric("Forecasting ahead", HORIZON_LABELS[horizon])
                _plot_history_and_forecast(history_df, result["forecasts"], "Index value")

    elif not is_statcan and geography:
        with st.spinner(f"Pulling {stat} sold price for {property_type_value or 'all property types'} in {geography} from Repliers..."):
            data, err = _safe_get_json(
                "/repliers/price-history",
                {"city": geography, "property_type": property_type_value, "months_back": int(periods_back), "stat": stat},
                error_label="Repliers",
            )
        if err:
            st.error(err)
        else:
            st.warning(
                "On a free/sandbox Repliers key this is **realistic sample data**, "
                "not real listings. Add a paid REPLIERS_API_KEY on the backend for "
                "real MLS prices — see README.md."
            )
            try:
                result = _run_forecast(data["week_start"], data["volume"], horizon, selected_methods, "M", seasonality_on)
            except requests.exceptions.RequestException as e:
                st.error(f"Forecast failed: {e}")
            else:
                history_df = pd.DataFrame(result["history"])
                col1, col2, col3 = st.columns(3)
                col1.metric("Months of history", len(history_df))
                col2.metric("Latest price", f"${history_df['volume'].iloc[-1]:,.0f}")
                col3.metric("Forecasting ahead", HORIZON_LABELS[horizon])
                _plot_history_and_forecast(history_df, result["forecasts"], "Sold price ($)")

# =======================================================================
# TAB 2 — My Alerts
# =======================================================================
with alerts_tab:
    st.subheader("Get emailed when a price hits your target")
    st.caption(
        "Alerts are checked once a day by an automated job (see README.md for "
        "setup) — you'll get an email the day your target is met."
    )

    email = st.text_input("Your email", key="alerts_email", placeholder="you@example.com")

    with st.form("new_alert_form", clear_on_submit=True):
        st.markdown("**New alert**")
        c1, c2 = st.columns(2)
        with c1:
            alert_source = st.selectbox("Data source", ["Statistics Canada (real)", "Repliers MLS (sample unless keyed)"])
        is_statcan_alert = alert_source.startswith("Statistics Canada")

        with c2:
            direction = st.selectbox("Notify me when the value goes...", ["below", "above"])

        c3, c4 = st.columns(2)
        with c3:
            alert_geography = st.text_input(
                "Location",
                help="For StatCan: a location name like 'Toronto', 'British Columbia', or 'Canada'. For Repliers: a city name.",
            )
        with c4:
            alert_property_type = st.text_input(
                "Type",
                value="Total (house and land)" if is_statcan_alert else "",
                help="For StatCan: 'Total (house and land)', 'House only', or 'Land only'. For Repliers: e.g. 'Condo Apt', 'Detached', or leave blank for all types.",
            )

        target_price = st.number_input(
            "Target value",
            min_value=0.0, step=1000.0 if not is_statcan_alert else 1.0,
            help="Index points for StatCan (e.g. 120.5), dollars for Repliers (e.g. 650000).",
        )
        alert_label = st.text_input("Label (optional)", placeholder="e.g. Toronto condo for us")

        submitted = st.form_submit_button("Save alert")

        if submitted:
            if not email:
                st.error("Enter your email above first.")
            elif not alert_geography:
                st.error("Enter a location.")
            else:
                payload = {
                    "email": email,
                    "data_source": "statcan" if is_statcan_alert else "repliers",
                    "geography": alert_geography,
                    "property_type": alert_property_type or None,
                    "target_price": float(target_price),
                    "direction": direction,
                    "value_unit": "index" if is_statcan_alert else "cad",
                    "label": alert_label or None,
                }
                try:
                    resp = requests.post(f"{API_URL}/watches", json=payload, timeout=30)
                    resp.raise_for_status()
                    st.success("Alert saved! It'll be checked in the next daily run.")
                    _get_json.clear()
                except requests.exceptions.RequestException as e:
                    detail = e.response.json().get("detail", str(e)) if getattr(e, "response", None) is not None else str(e)
                    st.error(f"Couldn't save alert: {detail}")

    st.divider()
    st.markdown("**Your saved alerts**")
    if not email:
        st.info("Enter your email above to see your saved alerts.")
    else:
        try:
            resp = requests.get(f"{API_URL}/watches", params={"email": email}, timeout=30)
            resp.raise_for_status()
            watches = resp.json()
        except requests.exceptions.RequestException as e:
            watches = []
            st.error(f"Couldn't load your alerts: {e}")

        if not watches:
            st.caption("No alerts saved yet.")
        for w in watches:
            unit_symbol = "index pts" if w["value_unit"] == "index" else "$"
            target_display = f"{w['target_price']:,.1f} {unit_symbol}" if w["value_unit"] == "index" else f"${w['target_price']:,.0f}"
            cols = st.columns([5, 1])
            with cols[0]:
                st.write(
                    f"**{w['label'] or w['geography']}** — {w['data_source']} · "
                    f"{w['geography']} · {w['property_type'] or 'All types'} · "
                    f"notify when **{w['direction']}** {target_display}"
                    + (f" · last checked: {w['last_checked_value']:.1f}" if w["last_checked_value"] is not None else "")
                )
            with cols[1]:
                if st.button("Delete", key=f"del_{w['id']}"):
                    try:
                        del_resp = requests.delete(f"{API_URL}/watches/{w['id']}", params={"email": email}, timeout=30)
                        del_resp.raise_for_status()
                        st.rerun()
                    except requests.exceptions.RequestException as e:
                        st.error(f"Couldn't delete: {e}")
