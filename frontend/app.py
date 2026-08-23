"""
app.py — the screen you actually look at
------------------------------------------
This is a Streamlit app: a way to build a web page using only Python
(no HTML/CSS/JavaScript needed). It talks to the FastAPI backend
(main.py) over HTTP, the same way a browser would.

Run it locally with:
    streamlit run app.py

Before running, make sure the backend is running too (in another
terminal): uvicorn main:app --reload   (from the backend/ folder)
"""

import io
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

# Change this to your deployed backend URL once it's hosted online,
# e.g. "https://your-app-name.onrender.com"
API_URL = st.secrets.get("API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Forecast Tool", layout="wide")
st.title("📈 Volume Forecast Tool")
st.caption("Upload historical weekly volume, or use dummy data, and forecast ahead.")

# ---- Sidebar: parameters ----
with st.sidebar:
    st.header("Settings")
    weeks_ahead = st.slider("Weeks to forecast ahead", min_value=1, max_value=12, value=6)
    seasonality_on = st.toggle("Factor in seasonality", value=True)
    st.divider()
    use_dummy = st.checkbox("Use dummy data (no file needed)", value=True)
    uploaded_file = None
    if not use_dummy:
        uploaded_file = st.file_uploader(
            "Upload CSV (columns: week_start, volume)", type="csv"
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
if csv_bytes:
    files = {"file": ("data.csv", csv_bytes, "text/csv")}
    params = {"weeks_ahead": weeks_ahead, "seasonality_on": seasonality_on}

    with st.spinner("Running forecast..."):
        resp = requests.post(f"{API_URL}/forecast", files=files, params=params)

    if resp.status_code != 200:
        st.error(f"Forecast failed: {resp.json().get('detail', resp.text)}")
    else:
        result = resp.json()
        history_df = pd.DataFrame(result["history"])
        forecast_df = pd.DataFrame(result["forecast"])

        if result["note"]:
            st.info(result["note"])

        col1, col2, col3 = st.columns(3)
        col1.metric("Weeks of history", len(history_df))
        col2.metric("Forecasting ahead", f"{weeks_ahead} week{'s' if weeks_ahead > 1 else ''}")
        col3.metric("Seasonality used", "Yes" if result["used_seasonality"] else "No")

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=history_df["week_start"], y=history_df["volume"],
            mode="lines", name="Historical volume", line=dict(color="#2563eb"),
        ))
        fig.add_trace(go.Scatter(
            x=forecast_df["week_start"], y=forecast_df["forecast_volume"],
            mode="lines+markers", name="Forecast", line=dict(color="#f97316", dash="dash"),
        ))
        fig.update_layout(
            title="Historical Volume & Forecast",
            xaxis_title="Week", yaxis_title="Volume",
            hovermode="x unified", height=500,
        )
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("See forecast numbers"):
            st.dataframe(forecast_df, use_container_width=True, hide_index=True)
else:
    st.info("Upload a CSV or check 'Use dummy data' in the sidebar to get started.")
