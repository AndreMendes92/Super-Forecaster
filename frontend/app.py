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
st.caption("Upload historical weekly volume, or use dummy data, and forecast ahead.")

# Colors for up to a handful of comparison lines
METHOD_COLORS = ["#f97316", "#10b981", "#a855f7", "#ef4444"]

# ---- Fetch available methods from the backend ----
try:
    methods_resp = requests.get(f"{API_URL}/methods", timeout=10)
    available_methods = methods_resp.json() if methods_resp.status_code == 200 else {"holt_winters": "Holt-Winters (trend + seasonality)"}
except requests.exceptions.RequestException:
    available_methods = {"holt_winters": "Holt-Winters (trend + seasonality)"}
    st.warning("Couldn't reach the backend to load method list — defaulting to Holt-Winters. Check API_URL.")

# ---- Sidebar: parameters ----
with st.sidebar:
    st.header("Settings")
    weeks_ahead = st.slider("Weeks to forecast ahead", min_value=1, max_value=12, value=6)

    selected_labels = st.multiselect(
        "Forecasting method(s) — pick more than one to compare",
        options=list(available_methods.values()),
        default=[available_methods.get("holt_winters", list(available_methods.values())[0])],
    )
    label_to_key = {v: k for k, v in available_methods.items()}
    selected_methods = [label_to_key[label] for label in selected_labels]

    seasonality_on = st.toggle(
        "Factor in seasonality (applies to Holt-Winters only)", value=True
    )
    st.divider()

    use_dummy = st.checkbox("Use dummy data (no file needed)", value=True)
    uploaded_file = None
    if not use_dummy:
        uploaded_file = st.file_uploader(
            "Upload your own CSV — needs exactly two columns named "
            "'week_start' (YYYY-MM-DD) and 'volume' (a number)",
            type="csv",
        )

# ---- Get the historical data as a CSV in memory ----
csv_bytes = None
if use_dummy:
    resp = requests.get(f"{API_URL}/dummy-data", params={"weeks_of_history": 104})
    if resp.status_code == 200:
        data = resp.json()
        df = pd.DataFrame({"week_start": data["week_start"], "volume": data["volume"]})
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        csv_bytes = buf.getvalue().encode()
    else:
        st.error("Could not reach the backend to generate dummy data.")
elif uploaded_file is not None:
    csv_bytes = uploaded_file.getvalue()

# ---- Run forecast and display ----
if not selected_methods:
    st.info("Pick at least one forecasting method in the sidebar.")
elif csv_bytes:
    files = {"file": ("data.csv", csv_bytes, "text/csv")}
    params = {
        "weeks_ahead": weeks_ahead,
        "seasonality_on": seasonality_on,
        "methods": ",".join(selected_methods),
    }

    with st.spinner("Running forecast..."):
        resp = requests.post(f"{API_URL}/forecast", files=files, params=params)

    if resp.status_code != 200:
        st.error(f"Forecast failed: {resp.json().get('detail', resp.text)}")
    else:
        result = resp.json()
        history_df = pd.DataFrame(result["history"])

        col1, col2 = st.columns(2)
        col1.metric("Weeks of history", len(history_df))
        col2.metric("Forecasting ahead", f"{weeks_ahead} week{'s' if weeks_ahead > 1 else ''}")

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=history_df["week_start"], y=history_df["volume"],
            mode="lines", name="Historical volume", line=dict(color="#2563eb"),
        ))

        comparison_rows = {}
        for i, (method_key, fdata) in enumerate(result["forecasts"].items()):
            color = METHOD_COLORS[i % len(METHOD_COLORS)]
            fdf = pd.DataFrame(fdata["values"])
            fig.add_trace(go.Scatter(
                x=fdf["week_start"], y=fdf["forecast_volume"],
                mode="lines+markers", name=fdata["label"],
                line=dict(color=color, dash="dash"),
            ))
            if fdata["note"]:
                st.info(f"**{fdata['label']}**: {fdata['note']}")
            comparison_rows[fdata["label"]] = fdf.set_index("week_start")["forecast_volume"]

        fig.update_layout(
            title="Historical Volume & Forecast",
            xaxis_title="Week", yaxis_title="Volume",
            hovermode="x unified", height=500,
        )
        st.plotly_chart(fig, use_container_width=True)

        if len(comparison_rows) > 1:
            st.subheader("Compare methods, week by week")
            compare_df = pd.DataFrame(comparison_rows)
            compare_df.index.name = "week_start"
            st.dataframe(compare_df, use_container_width=True)
        else:
            with st.expander("See forecast numbers"):
                only_df = list(comparison_rows.values())[0].reset_index()
                st.dataframe(only_df, use_container_width=True, hide_index=True)
else:
    st.info(
        "Check 'Use dummy data' in the sidebar to try it instantly, "
        "or uncheck it to upload your own CSV file."
    )
