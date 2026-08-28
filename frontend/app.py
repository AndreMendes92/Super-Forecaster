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


explore_tab, alerts_tab, places_tab = st.tabs(
    ["📈 Explore & Forecast", "🔔 My Alerts", "🏘️ Best Places to Live"]
)

# Criteria shown on the "Best Places to Live" tab, in display order.
# `higher_is_better` is the *default* scoring direction — every
# criterion except population density has an obvious one (safer/
# cheaper/more walkable is better), so only that one exposes a
# direction toggle in the UI. Weight defaults reflect that: density
# and income start at 0 (shown, but not counted in the ranking) since
# "denser is better" is a matter of taste and income is context, not
# a livability criterion on its own.
LIVABILITY_CRITERIA = [
    {"key": "crime_severity_index", "label": "Safety (lower crime)", "default_weight": 5, "higher_is_better": False},
    {"key": "average_rent", "label": "Affordability (lower rent)", "default_weight": 5, "higher_is_better": False},
    {"key": "walkability", "label": "Walkability (amenity density)", "default_weight": 3, "higher_is_better": True},
    {"key": "transit", "label": "Transit access", "default_weight": 3, "higher_is_better": True},
    {"key": "green_space", "label": "Green space access", "default_weight": 2, "higher_is_better": True},
    {"key": "population_density", "label": "Population density", "default_weight": 0, "higher_is_better": True, "direction_toggle": True},
    {"key": "median_household_income", "label": "Household income (context)", "default_weight": 0, "higher_is_better": True},
]


def _normalize_criterion(raw_values: dict, higher_is_better: bool) -> dict:
    """
    Percentile-rank scales a {municipality_id: value} dict to 0-100,
    flipped when lower is better. Municipalities with a None value are
    simply left out of the returned dict (not scored 0) — the
    composite-score step below treats a missing sub-score as "excluded
    from this municipality's average", not "penalized".

    Deliberately rank-based, not min-max: min-max scaling lets one
    outlier stretch a criterion's whole range and compress every other
    municipality into a narrow band near one end (e.g. Vancouver's
    population density dwarfing every suburb) — real user-reported
    symptom: dragging a heavily-weighted slider barely moved the
    composite score, because that criterion wasn't actually
    discriminating between most municipalities even at full weight.
    Percentile rank fixes that structurally: every weighted criterion
    contributes an evenly-spread 0-100 signal by construction,
    regardless of its raw distribution's shape, so each slider has a
    comparable, visible effect on the result.

    Ties get the *average* of the ranks they'd otherwise occupy (via
    pandas' default rank method), not an arbitrary tiebreak order —
    several municipalities share an identical crime value on purpose
    (shared police detachments, e.g. Ridge Meadows RCMP covering both
    Maple Ridge and Pitt Meadows; see livability_geography.py on the
    backend), and they should keep scoring identically here too.
    """
    valid = {k: v for k, v in raw_values.items() if v is not None}
    n = len(valid)
    if n < 2:
        return {k: 50.0 for k in valid}
    ranks = pd.Series(valid).rank(method="average")  # 1..n, ties share the average rank
    pct = (ranks - 1) / (n - 1) * 100
    if not higher_is_better:
        pct = 100 - pct
    return pct.to_dict()


def _compute_rankings(places: dict, weights: dict, directions: dict) -> pd.DataFrame:
    """
    weights/directions are {criterion_key: value} for every criterion
    in LIVABILITY_CRITERIA. Returns one row per municipality with its
    composite score (weighted average of available normalized
    sub-scores — a criterion with weight 0 or missing data for that
    municipality is simply excluded from its average, not counted as
    zero) plus how many of the weighted-on criteria actually had data.
    """
    normalized = {
        key: _normalize_criterion(
            {pid: p["criteria"].get(key, {}).get("value") for pid, p in places.items()},
            directions[key],
        )
        for key in weights
    }

    rows = []
    for pid, p in places.items():
        weighted_sum, total_weight, n_used, n_weighted = 0.0, 0.0, 0, 0
        for key, w in weights.items():
            if w <= 0:
                continue
            n_weighted += 1
            score = normalized[key].get(pid)
            if score is None:
                continue
            weighted_sum += score * w
            total_weight += w
            n_used += 1
        rows.append({
            "id": pid,
            "name": p["name"],
            "composite": (weighted_sum / total_weight) if total_weight > 0 else None,
            "criteria_used": f"{n_used}/{n_weighted}" if n_weighted else "0/0",
        })

    df = pd.DataFrame(rows).sort_values("composite", ascending=False, na_position="last").reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def _build_heatmap_figure(chart_df: pd.DataFrame, boundaries: dict):
    """
    A Plotly choropleth map of Metro Vancouver, one municipality per
    shape, shaded by composite score. Only municipalities with both a
    score and a cached boundary (see /livability/boundaries) can be
    drawn — boundaries fill in gradually over a municipality's first
    couple of refreshes (see livability_boundaries.py), so this is
    expected to be partial right after initial setup. Returns None if
    there's nothing drawable yet, so the caller can show a plain
    message instead of an empty map.
    """
    mappable = chart_df[chart_df["id"].isin(boundaries.keys())]
    if mappable.empty:
        return None

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": pid, "properties": {"id": pid}, "geometry": boundaries[pid]}
            for pid in mappable["id"]
        ],
    }

    # go.Choroplethmap (MapLibre-based, no token needed) — the modern
    # replacement for the older, now-removed go.Choroplethmapbox.
    fig = go.Figure(go.Choroplethmap(
        geojson=geojson,
        locations=mappable["id"],
        z=mappable["composite"],
        featureidkey="properties.id",
        colorscale="RdYlGn", zmin=0, zmax=100,
        marker_opacity=0.75, marker_line_width=1.2, marker_line_color="white",
        text=mappable["name"],
        hovertemplate="<b>%{text}</b><br>Score: %{z:.1f}/100<extra></extra>",
        colorbar_title="Score",
    ))
    fig.update_layout(
        map_style="carto-positron",
        map_zoom=8.4, map_center={"lat": 49.22, "lon": -122.85},
        margin=dict(l=0, r=0, t=0, b=0), height=560,
    )
    return fig

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
                "covering Canada and a few provinces/regions — but it tracks NEW-build "
                "prices, not resale averages, doesn't split out condos specifically, "
                "and doesn't break down by individual city. Repliers gives real "
                "per-property-type city data, but returns realistic SAMPLE data on a "
                "free key, not live listings, unless you add a paid key later."
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
                help="For StatCan: 'Canada', 'Ontario', 'British Columbia', or 'Prairie region' (this table only breaks down to province/region, not individual cities). For Repliers: a city name.",
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

# =======================================================================
# TAB 3 — Best Places to Live (Metro Vancouver)
# =======================================================================
with places_tab:
    header_col, refresh_col = st.columns([5, 1])
    with header_col:
        st.subheader("Compare Metro Vancouver municipalities")
    with refresh_col:
        if st.button("🔄 Refresh data", help="Backend responses are cached for up to an hour — click to bypass that and pull the latest cached values now."):
            _get_json.clear()
            st.rerun()
    st.caption(
        "Scored across the 21 Metro Vancouver municipalities + Electoral Area A — "
        "the coarsest granularity, but the only one where every criterion below has "
        "real, free, region-wide data (see README.md for exactly where each number "
        "comes from and what it doesn't capture). Adjust the sliders to match what "
        "you actually care about — the ranking recomputes instantly."
    )

    data, err = _safe_get_json("/livability/places", error_label="the backend")
    if err:
        st.error(err)
        st.stop()

    places = data.get("places") or {}
    meta = data.get("meta") or {}
    boundaries, _boundaries_err = _safe_get_json("/livability/boundaries", error_label="the backend")
    boundaries = boundaries or {}

    if not places:
        st.info(
            "No livability data cached yet. On first setup, trigger the backend's "
            "`POST /livability/refresh-cache` once (see README.md) — after that it "
            "refreshes automatically on a monthly schedule."
        )
        st.stop()

    if meta.get("computed_at"):
        st.caption(f"Data last refreshed: {meta['computed_at']}")

    # Coverage per criterion — how many municipalities actually have a
    # value right now. Shown next to every slider so it's obvious
    # up front which criteria are "live" today vs. still filling in,
    # rather than discovering it only after an empty ranking.
    coverage = {
        c["key"]: sum(1 for p in places.values() if p["criteria"].get(c["key"], {}).get("value") is not None)
        for c in LIVABILITY_CRITERIA
    }
    n_places = len(places)

    st.markdown("**How much does each criterion matter to you?**")
    weight_cols = st.columns(len(LIVABILITY_CRITERIA))
    weights, directions = {}, {}
    for col, criterion in zip(weight_cols, LIVABILITY_CRITERIA):
        key = criterion["key"]
        with col:
            weights[key] = st.slider(criterion["label"], 0, 10, criterion["default_weight"], key=f"weight_{key}")
            if criterion.get("direction_toggle") and weights[key] > 0:
                directions[key] = st.checkbox("Denser is better", value=True, key=f"dir_{key}")
            else:
                directions[key] = criterion["higher_is_better"]
            covered = coverage[key]
            st.caption(f"✅ {covered}/{n_places} municipalities" if covered > 0 else "⏳ not loaded yet")

    rankings = _compute_rankings(places, weights, directions)

    if rankings["composite"].isna().all():
        weighted_labels = [c["label"] for c in LIVABILITY_CRITERIA if weights[c["key"]] > 0]
        st.warning(
            "No municipality has data yet for **any** of the criteria you've weighted "
            f"({', '.join(weighted_labels)}) — see the ✅/⏳ counts above each slider. "
            "Try weighting a criterion marked ✅ instead (Population density and "
            "Household income are usually the most reliably populated), or click "
            "**🔄 Refresh data** above after the next scheduled backend refresh."
        )
    else:
        st.divider()
        st.markdown("**Heat map**")
        chart_df = rankings.dropna(subset=["composite"]).sort_values("composite")
        heatmap_fig = _build_heatmap_figure(chart_df, boundaries)
        if heatmap_fig is not None:
            st.plotly_chart(heatmap_fig, width="stretch")
            missing = set(chart_df["id"]) - set(boundaries.keys())
            if missing:
                missing_names = chart_df[chart_df["id"].isin(missing)]["name"].tolist()
                st.caption(
                    f"Boundary not loaded yet for: {', '.join(missing_names)} — "
                    "these fill in over the next couple of scheduled refreshes."
                )
        else:
            st.info(
                "No municipality boundaries cached yet for the map — they load in "
                "gradually (one new one per refresh at most, out of politeness to the "
                "free geocoder that provides them). The ranking below still works."
            )

        col_table, col_chart = st.columns([1, 1.2])
        with col_table:
            st.markdown("**Ranking**")
            display_df = rankings.dropna(subset=["composite"]).copy()
            display_df["composite"] = display_df["composite"].round(1)
            st.dataframe(
                display_df[["rank", "name", "composite", "criteria_used"]].rename(
                    columns={"name": "Municipality", "composite": "Score (0-100)", "criteria_used": "Criteria used"}
                ),
                hide_index=True, width="stretch",
            )
        with col_chart:
            fig = go.Figure(go.Bar(
                x=chart_df["composite"], y=chart_df["name"], orientation="h",
                marker=dict(color=chart_df["composite"], colorscale="Blues"),
            ))
            fig.update_layout(title="Composite livability score", xaxis_title="Score (0-100)", height=520, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, width="stretch")

    st.divider()
    st.markdown("**Municipality detail**")
    muni_names = {p["id"]: p["name"] for p in places.values()}
    selected_id = st.selectbox(
        "Pick a municipality", options=list(muni_names.keys()), format_func=lambda pid: muni_names[pid],
    )
    if selected_id:
        detail = places[selected_id]["criteria"]
        for criterion in LIVABILITY_CRITERIA:
            c = detail.get(criterion["key"], {})
            value = c.get("value")
            value_display = f"{value:,.1f}" if isinstance(value, (int, float)) else "not available"
            with st.container(border=True):
                cols = st.columns([2, 1, 3])
                cols[0].markdown(f"**{criterion['label']}**")
                cols[1].markdown(value_display)
                source_bits = " · ".join(x for x in [c.get("source"), c.get("as_of")] if x)
                note_bits = f" — {c['note']}" if c.get("note") else ""
                cols[2].caption(f"{c.get('unit', '')} · {source_bits}{note_bits}")
