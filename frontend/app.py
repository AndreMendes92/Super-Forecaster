"""
app.py — the screen you actually look at
------------------------------------------
Streamlit frontend. Talks to the FastAPI backend over HTTP.

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

st.set_page_config(page_title="Forecast Tool", layout="wide")
st.title("📈 Volume Forecast Tool")
st.caption("Upload historical data, use dummy data, or pull a real public dataset — then forecast ahead.")

METHOD_COLORS = ["#f97316", "#10b981", "#a855f7", "#ef4444"]


def _fetch_methods():
    for timeout in (45, 20):
        try:
            resp = requests.get(f"{API_URL}/methods", timeout=timeout)
            if resp.status_code == 200:
                return resp.json(), None
        except requests.exceptions.RequestException:
            continue
    return None, "Couldn't reach the backend to load the method list — defaulting to Holt-Winters. If this keeps happening, check API_URL in Secrets."


with st.spinner("Connecting to backend (may take up to a minute if it's waking up)..."):
    available_methods, methods_error = _fetch_methods()

if available_methods is None:
    available_methods = {"holt_winters": "Holt-Winters (trend + seasonality)"}
    st.warning(methods_error)

# ---- Sidebar ----
with st.sidebar:
    st.header("Settings")
    weeks_ahead = st.slider("Periods to forecast ahead", min_value=1, max_value=12, value=6)

    selected_labels = st.multiselect(
        "Forecasting method(s) — pick more than one to compare",
        options=list(available_methods.values()),
        default=[available_methods.get("holt_winters", list(available_methods.values())[0])],
    )
    label_to_key = {v: k for k, v in available_methods.items()}
    selected_methods = [label_to_key[label] for label in selected_labels]

    seasonality_on = st.toggle("Factor in seasonality", value=True)
    st.divider()

    data_source = st.radio(
        "Data source",
        ["Use dummy data", "Upload my own CSV", "Pull real data (StatCan)", "Pull real MLS prices (Repliers)"],
    )

    uploaded_file = None
    statcan_vector_id = None
    statcan_periods = 60
    repliers_city = None
    repliers_property_type = None
    repliers_months = 24
    repliers_stat = "avg"
    is_index_data = False

    if data_source == "Upload my own CSV":
        uploaded_file = st.file_uploader(
            "CSV needs exactly two columns named 'week_start' (YYYY-MM-DD) and 'volume' (a number)",
            type="csv",
        )
    elif data_source == "Pull real data (StatCan)":
        st.caption(
            "Pulls a real time series from Statistics Canada's free public API. "
            "Data is monthly, so seasonality here means a 12-month cycle."
        )
        statcan_vector_id = st.text_input(
            "StatCan vector ID",
            value="111955442",
            help="Default is Canada's New Housing Price Index (Total, house+land). "
                 "To find a BC or city-specific vector: open the relevant StatCan "
                 "table page → 'Add/Remove data' → 'Customize layout' → tick "
                 "'Display vector identifier and coordinate' → Apply. The table "
                 "will show a 'Vector' column per geography.",
        )
        statcan_periods = st.number_input(
            "How many recent months to pull", min_value=8, max_value=300, value=60
        )
        with st.expander("Where do I find other vector IDs?"):
            st.markdown(
                "1. Go to a StatCan table page, e.g. "
                "[New Housing Price Index](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810020501)\n"
                "2. Click **Add/Remove data**\n"
                "3. Click **Customize layout**\n"
                "4. Tick **Display vector identifier and coordinate**, click Apply\n"
                "5. A **Vector** column appears — pick the row for the geography "
                "you want (e.g. British Columbia, or a specific city's CMA)"
            )
        is_index_data = st.checkbox(
            "This is a price index (e.g. NHPI) — show % change from start instead of raw index points",
            value=True,
            help="An index like StatCan's NHPI has no dollar meaning on its own — "
                 "100 is just the baseline period. Showing % change from the start "
                 "of your pulled history makes the trend directly readable.",
        )
    elif data_source == "Pull real MLS prices (Repliers)":
        st.caption(
            "Pulls real sold-price statistics from the Repliers MLS API. "
            "On a free/sandbox key this returns realistic sample data, "
            "not real listings — upgrade to a production key for real prices."
        )

        try:
            agg_resp = requests.get(f"{API_URL}/repliers/aggregates", timeout=30)
            if agg_resp.status_code == 200:
                agg_data = agg_resp.json()
                city_options = [c["value"] for c in agg_data["cities"][:30]]
                type_options = [p["value"] for p in agg_data["property_types"]]
            else:
                city_options, type_options = [], []
                st.warning(f"Couldn't load real city/property-type list: {agg_resp.json().get('detail', '')}")
        except requests.exceptions.RequestException as e:
            city_options, type_options = [], []
            st.warning(f"Couldn't reach backend to load city/property-type list: {e}")

        if city_options:
            repliers_city = st.selectbox(
                "City (from your account's actual data)", city_options,
            )
        else:
            st.info("Falling back to free text — city list unavailable.")
            repliers_city = st.text_input("City", value="Toronto")

        if type_options:
            repliers_property_type = st.selectbox("Property type", ["All types"] + type_options)
            if repliers_property_type == "All types":
                repliers_property_type = None
        else:
            repliers_property_type = None

        repliers_months = st.number_input("How many recent months to pull", min_value=6, max_value=120, value=24)
        repliers_stat = st.radio("Statistic", ["avg", "med"], format_func=lambda x: "Average" if x == "avg" else "Median", horizontal=True)

# ---- Get the historical data as a CSV in memory, and figure out freq ----
csv_bytes = None
freq = "W"

if data_source == "Use dummy data":
    resp = requests.get(f"{API_URL}/dummy-data", params={"weeks_of_history": 104}, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        df = pd.DataFrame({"week_start": data["week_start"], "volume": data["volume"]})
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        csv_bytes = buf.getvalue().encode()
    else:
        st.error("Could not reach the backend to generate dummy data.")

elif data_source == "Upload my own CSV" and uploaded_file is not None:
    csv_bytes = uploaded_file.getvalue()

elif data_source == "Pull real data (StatCan)" and statcan_vector_id:
    freq = "M"
    try:
        with st.spinner(f"Pulling vector {statcan_vector_id} from Statistics Canada..."):
            resp = requests.get(
                f"{API_URL}/statcan/vector/{statcan_vector_id}",
                params={"periods": int(statcan_periods)},
                timeout=45,
            )
        if resp.status_code == 200:
            data = resp.json()
            df = pd.DataFrame({"week_start": data["week_start"], "volume": data["volume"]})
            buf = io.StringIO()
            df.to_csv(buf, index=False)
            csv_bytes = buf.getvalue().encode()
            st.success(f"Pulled {len(df)} months of real data for vector {statcan_vector_id}.")
        else:
            st.error(f"StatCan fetch failed: {resp.json().get('detail', resp.text)}")
    except requests.exceptions.RequestException as e:
        st.error(f"Could not reach the backend/StatCan: {e}")

elif data_source == "Pull real MLS prices (Repliers)" and repliers_city:
    freq = "M"
    try:
        label = f"{repliers_stat} sold price for {repliers_property_type or 'all property types'} in {repliers_city}"
        with st.spinner(f"Pulling {label} from Repliers..."):
            resp = requests.get(
                f"{API_URL}/repliers/price-history",
                params={
                    "city": repliers_city,
                    "property_type": repliers_property_type,
                    "months_back": int(repliers_months),
                    "stat": repliers_stat,
                },
                timeout=45,
            )
        if resp.status_code == 200:
            data = resp.json()
            df = pd.DataFrame({"week_start": data["week_start"], "volume": data["volume"]})
            buf = io.StringIO()
            df.to_csv(buf, index=False)
            csv_bytes = buf.getvalue().encode()
            st.success(f"Pulled {len(df)} months of {label}.")
        else:
            st.error(f"Repliers fetch failed: {resp.json().get('detail', resp.text)}")
    except requests.exceptions.RequestException as e:
        st.error(f"Could not reach the backend/Repliers: {e}")

# ---- Run forecast and display ----
if not selected_methods:
    st.info("Pick at least one forecasting method in the sidebar.")
elif csv_bytes:
    files = {"file": ("data.csv", csv_bytes, "text/csv")}
    params = {
        "weeks_ahead": weeks_ahead,
        "seasonality_on": seasonality_on,
        "methods": ",".join(selected_methods),
        "freq": freq,
    }

    with st.spinner("Running forecast..."):
        resp = requests.post(f"{API_URL}/forecast", files=files, params=params, timeout=60)

    if resp.status_code != 200:
        st.error(f"Forecast failed: {resp.json().get('detail', resp.text)}")
    else:
        result = resp.json()
        history_df = pd.DataFrame(result["history"])
        period_label = "months" if freq == "M" else "weeks"

        # If this is index data (e.g. NHPI), convert raw index points to
        # % change from the start of the pulled history — the index level
        # itself has no dollar meaning, but % change from a fixed point is
        # directly interpretable.
        baseline = None
        y_label = "Value"
        if is_index_data and len(history_df) > 0:
            baseline = history_df["volume"].iloc[0]
            baseline_date = history_df["week_start"].iloc[0]
            history_df = history_df.copy()
            history_df["volume"] = (history_df["volume"] / baseline - 1) * 100
            y_label = f"% change since {baseline_date}"
            st.caption(
                f"Showing % change relative to {baseline_date} (index value "
                f"{baseline:.1f} at that point = 0% on this chart)."
            )

        col1, col2 = st.columns(2)
        col1.metric(f"{period_label.capitalize()} of history", len(history_df))
        col2.metric("Forecasting ahead", f"{weeks_ahead} {period_label[:-1] if weeks_ahead == 1 else period_label}")

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=history_df["week_start"], y=history_df["volume"],
            mode="lines", name="Historical", line=dict(color="#2563eb"),
        ))

        comparison_rows = {}
        for i, (method_key, fdata) in enumerate(result["forecasts"].items()):
            color = METHOD_COLORS[i % len(METHOD_COLORS)]
            fdf = pd.DataFrame(fdata["values"])
            if baseline is not None:
                fdf = fdf.copy()
                fdf["forecast_volume"] = (fdf["forecast_volume"] / baseline - 1) * 100
            fig.add_trace(go.Scatter(
                x=fdf["week_start"], y=fdf["forecast_volume"],
                mode="lines+markers", name=fdata["label"],
                line=dict(color=color, dash="dash"),
            ))
            if fdata["note"]:
                st.info(f"**{fdata['label']}**: {fdata['note']}")
            comparison_rows[fdata["label"]] = fdf.set_index("week_start")["forecast_volume"]

        fig.update_layout(
            title="Historical Data & Forecast",
            xaxis_title="Period", yaxis_title=y_label,
            hovermode="x unified", height=500,
        )
        st.plotly_chart(fig, width='stretch')

        if len(comparison_rows) > 1:
            st.subheader("Compare methods, period by period")
            compare_df = pd.DataFrame(comparison_rows)
            compare_df.index.name = "period_start"
            st.dataframe(compare_df, width='stretch')
        else:
            with st.expander("See forecast numbers"):
                only_df = list(comparison_rows.values())[0].reset_index()
                st.dataframe(only_df, width='stretch', hide_index=True)
else:
    st.info("Choose a data source in the sidebar to get started.")
