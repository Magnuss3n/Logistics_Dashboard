"""
app.py -- Streamlit entry point.
Run with:  streamlit run app.py   (from inside the cement-dashboard/ folder)

Currently wired to the part of the pipeline that's actually confirmed:
Road + Trade(TR) + Grade U1/U2, pre-truck-type-filter (that scope question
is still open -- see data_prep.py TRUCK_TYPE_SCOPE notes).
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from src.data_prep import load_base_tdc, clean_columns, apply_scope_filters, aggregate_daily, COL_DATE

st.set_page_config(page_title="Cement Logistics -- Demand & Trucks", layout="wide")
st.title("Cement Logistics Dashboard")
st.caption("Base TDC file, Jan-Jul 2026 | Road + Trade + U1/U2 scope")

uploaded = st.file_uploader("Upload base_tdc_file_jan-july.xlsx", type=["xlsx"])

if uploaded is None:
    st.info("Upload the base TDC file to begin. (Or drop it in data/ and point load_base_tdc at that path.)")
    st.stop()

with st.spinner("Loading and cleaning..."):
    raw = load_base_tdc(uploaded)
    raw = clean_columns(raw)

truck_scope = st.sidebar.radio(
    "Truck type scope (UNRESOLVED -- confirm before using for the board deck)",
    options=["None (Road+TR+U1/U2 only, safest for now)", "strict (T1/T2/T3 only, ~18% of qty)", "broad (T1/T2/T3 + DEPOT/MKT/DIRDE/DIV)"],
    index=0,
)
scope_map = {
    "None (Road+TR+U1/U2 only, safest for now)": None,
    "strict (T1/T2/T3 only, ~18% of qty)": "strict",
    "broad (T1/T2/T3 + DEPOT/MKT/DIRDE/DIV)": "broad",
}
filtered, funnel = apply_scope_filters(raw, truck_type_scope=scope_map[truck_scope])

st.subheader("Filter funnel")
st.dataframe(funnel, use_container_width=True)

st.subheader("Daily volume (aggregated)")
daily = aggregate_daily(filtered)
region_totals = daily.groupby([COL_DATE])["Qty"].sum().reset_index()
fig = px.line(region_totals, x=COL_DATE, y="Qty", title="Total daily Road+TR+U1/U2 volume, all regions")
st.plotly_chart(fig, use_container_width=True)

region_options = sorted(daily["Region"].dropna().unique().tolist())
region_pick = st.selectbox("Drill into a region", region_options)
region_daily = daily[daily["Region"] == region_pick].groupby(COL_DATE)["Qty"].sum().reset_index()
fig2 = px.line(region_daily, x=COL_DATE, y="Qty", title=f"Daily volume -- {region_pick}")
st.plotly_chart(fig2, use_container_width=True)

st.subheader("Raw aggregated table")
st.dataframe(daily, use_container_width=True)
