"""
app.py -- Streamlit entry point.
Two tabs:
  Track 1 -- Total road volume baseline (Road+TR+U1/U2, pre-truck-type-filter
             stopgap -- see src/data_prep.py TRUCK_TYPE_SCOPE notes, still
             UNRESOLVED, do not use "strict"/"broad" for a board number
             without sign-off).
  Track 2 -- SO territory tagging + day-of-month distribution, built by
             joining Order Register (order history) to a TDC extract on
             Customer Code to pick up Territory/Branch/Region. STO tagging
             is not built yet -- SO only, per current scope.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from src.data_prep import (
    load_base_tdc,
    clean_columns,
    apply_scope_filters,
    aggregate_daily,
    build_customer_territory_map,
    find_territory_conflicts,
    load_order_register,
    clean_order_register,
    tag_so_territory,
    so_match_summary,
    COL_DATE,
    COL_REGION,
    COL_BRANCH,
    COL_TERRITORY,
    COL_TERRITORY_NAME,
    OREG_COL_DATE,
    OREG_COL_QTY,
)

from src.forecasting import day_of_month_share
from src import nt_forecast
from src.truck_requirements import add_truck_requirement_columns
from src.territory_cost_profile import build_territory_cost_profile, attach_cost_profile

st.set_page_config(page_title="Cement Logistics -- Demand & Trucks", layout="wide")
st.title("Cement Logistics Dashboard")

tab1, tab2 = st.tabs(["Total Road Volume (Track 1)", "SO Territory Forecast (Track 2)"])

# =================================================================================
# TRACK 1 -- Total road volume baseline
# =================================================================================
with tab1:
    st.caption("Base TDC file, Jan-Jul 2026 | Road + Trade + U1/U2 scope")

    tdc_upload_1 = st.file_uploader("Upload base_tdc_file_jan-july.xlsx", type=["xlsx"], key="tdc_track1")

    if tdc_upload_1 is None:
        st.info("Upload the base TDC file to begin.")
    else:
        with st.spinner("Loading and cleaning..."):
            raw = load_base_tdc(tdc_upload_1)
            raw = clean_columns(raw)

        truck_scope = st.sidebar.radio(
            "Truck type scope (UNRESOLVED -- confirm before using for the board deck)",
            options=[
                "None (Road+TR+U1/U2 only, safest for now)",
                "strict (T1/T2/T3 only, ~18% of qty)",
                "broad (T1/T2/T3 + DEPOT/MKT/DIRDE/DIV)",
            ],
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

        region_options = sorted(daily[COL_REGION].dropna().unique().tolist())
        region_pick = st.selectbox("Drill into a region", region_options, key="region_pick_track1")
        region_daily = daily[daily[COL_REGION] == region_pick].groupby(COL_DATE)["Qty"].sum().reset_index()
        fig2 = px.line(region_daily, x=COL_DATE, y="Qty", title=f"Daily volume -- {region_pick}")
        st.plotly_chart(fig2, use_container_width=True)

        st.subheader("Raw aggregated table")
        st.dataframe(daily, use_container_width=True)

# =================================================================================
# TRACK 2 -- SO territory tagging + day-of-month distribution
# =================================================================================
with tab2:
    st.caption(
        "Order Register (order history) tagged with Territory/Branch/Region via a "
        "TDC extract's customer master. Satna plant, Trade_Type NT+TR. SO only -- "
        "STO tagging is not built yet."
    )

    c1, c2 = st.columns(2)
    oreg_upload = c1.file_uploader("Upload Order Register (.xlsx)", type=["xlsx"], key="oreg_track2")
    tdc_upload_2 = c2.file_uploader(
        "Upload a TDC extract for the customer-territory master (.xlsx)", type=["xlsx"], key="tdc_track2"
    )

    date_col1, date_col2 = st.columns(2)
    date_start = date_col1.date_input("History window start", value=pd.Timestamp("2026-04-01"), key="date_start_t2")
    date_end = date_col2.date_input("History window end", value=pd.Timestamp("2026-06-30"), key="date_end_t2")

    if not (oreg_upload and tdc_upload_2):
        st.info("Upload both the Order Register and a TDC extract to run the SO territory pipeline.")
    else:
        with st.spinner("Building customer-territory map and tagging orders..."):
            tdc_raw = load_base_tdc(tdc_upload_2)
            cust_map = build_customer_territory_map(tdc_raw)
            conflicts = find_territory_conflicts(tdc_raw)
            cost_profile = build_territory_cost_profile(tdc_raw)

            oreg_raw = load_order_register(oreg_upload)
            oreg_clean = clean_order_register(oreg_raw, date_start=str(date_start), date_end=str(date_end))
            tagged = tag_so_territory(oreg_clean, cust_map)

        summary = so_match_summary(tagged)

        st.subheader("Territory match quality")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total SO qty", f"{summary['total_qty']:,.0f}")
        m2.metric("Matched qty", f"{summary['total_qty'] - summary['unmatched_qty']:,.0f}")
        m3.metric("Unmatched qty", f"{summary['unmatched_qty']:,.0f}", f"{summary['unmatched_pct']}%")
        m4.metric("Customer-territory conflicts in TDC", len(conflicts["CUSTOMER CODE"].unique()) if len(conflicts) else 0)
 
        if summary["unmatched_pct"] > 5:
            worst_trade = max(summary["unmatched_qty_by_trade_type"], key=summary["unmatched_qty_by_trade_type"].get)
            st.warning(
                f"{summary['unmatched_pct']}% of qty (mostly {worst_trade}) couldn't be tagged to a "
                "territory -- these customer codes don't appear in the TDC extract for this window. "
                "Consider a Mapping ref fallback if this needs to shrink."
            )
 
        with st.expander("Unmatched qty breakdown by trade type"):
            st.json(summary["unmatched_qty_by_trade_type"])
 
        st.subheader("Day-of-month SO distribution by territory")
        tagged_matched = tagged[tagged[COL_TERRITORY].notna()].copy()
        pattern = day_of_month_share(
            tagged_matched,
            date_col=OREG_COL_DATE,
            qty_col=OREG_COL_QTY,
            group_cols=[COL_REGION, COL_BRANCH, COL_TERRITORY],
        )
        pattern["territory_key"] = pattern[COL_REGION] + " | " + pattern[COL_BRANCH] + " | " + pattern[COL_TERRITORY]
 
        territories = sorted(pattern["territory_key"].unique())
        # Default to the 3 highest-volume territories rather than the first 3
        # alphabetically -- an alphabetical default was silently favoring
        # Bihar-prefixed territories every time regardless of actual volume.
        territory_volume = (
            tagged_matched.assign(
                territory_key=(
                    tagged_matched[COL_REGION] + " | " + tagged_matched[COL_BRANCH] + " | " + tagged_matched[COL_TERRITORY]
                )
            )
            .groupby("territory_key")[OREG_COL_QTY]
            .sum()
            .sort_values(ascending=False)
        )
        default_territories = [t for t in territory_volume.index if t in territories][:3]
        selected = st.multiselect("Select territories to compare", territories, default=default_territories)
 
        if selected:
            chart_df = pattern[pattern["territory_key"].isin(selected)]
            fig3 = px.line(
                chart_df,
                x="day",
                y="day_share_of_month",
                color="territory_key",
                markers=True,
                title="Day-of-month share of monthly SO qty",
            )
            fig3.update_layout(yaxis_tickformat=".0%")
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.caption("Select at least one territory to see its distribution curve.")
 
        with st.expander("Day-of-month distribution table"):
            st.dataframe(pattern, use_container_width=True)
            st.download_button(
                "Download distribution (CSV)",
                pattern.to_csv(index=False),
                file_name="so_day_of_month_distribution.csv",
            )
 
        with st.expander("Customer -> Territory map (from TDC)"):
            st.dataframe(cust_map, use_container_width=True)
 
        st.subheader("Aug-26 daily forecast (DD targets split by territory)")
        from src.dd_targets import AUG26_DD_TARGETS, REGION_CODE_TO_BRANCH
        from src.forecasting import build_daily_territory_forecast
 
        forecast = build_daily_territory_forecast(
            aug26_targets=AUG26_DD_TARGETS,
            region_code_to_branch=REGION_CODE_TO_BRANCH,
            historical_tagged_df=tagged_matched,
            branch_col=COL_BRANCH,
            territory_col=COL_TERRITORY,
            territory_name_col=COL_TERRITORY_NAME,
            date_col=OREG_COL_DATE,
            qty_col=OREG_COL_QTY,
        )
        forecast = attach_cost_profile(
            forecast, cost_profile, territory_col_in_forecast="territory_name", qty_col="forecast_qty"
        )
        if forecast.attrs.get("unmatched_rows"):
            st.warning(f"{forecast.attrs['unmatched_rows']} rows didn't match a territory in the cost profile -- Distance/Freight columns will be blank for those, and truck priority will fall back to shorter-haul ordering for them.")
        forecast = add_truck_requirement_columns(
            forecast,
            date_col="day",
            qty_col="forecast_qty",
            group_cols=["region_code", "territory_name"],
            distance_band_col="Distance_Band",
        )
        st.dataframe(forecast, use_container_width=True)
        st.caption(
            "Truck type PRIORITY is distance-driven: longer-haul territories (281-340km / "
            "Above 341k) fill T2 (42t) trucks first, shorter-haul territories fill T1 (35t) "
            "first -- fewer, larger loads matter more the farther a truck travels. Whatever "
            "doesn't fill a full truck of either type that day carries forward into the next "
            "day's volume for that territory (Carry_Forward). Total_Trucks = T1_trucks + "
            "T2_trucks, a real combined daily plan. Estimated_Daily_Freight_Cost = that "
            "territory's historical Freight_Rate_per_Tonne x this day's forecast volume."
        )
        st.download_button(
            "Download Aug-26 daily territory forecast (CSV)",
            forecast.to_csv(index=False),
            file_name="aug26_daily_territory_forecast.csv",
        )
 
        st.divider()
        st.subheader("STO (NT) Forecast -- trend + spine, no external target needed")
        st.caption(
            "Uses TR/NT == 'NT' rows straight off the TDC extract uploaded above for the "
            "customer-territory map -- same file, no Order Register join needed since TDC "
            "already carries Territory/Branch/Region natively for NT rows. Each territory's "
            "own trend is projected forward (log-linear fit on monthly totals, pooled fallback "
            "for thin history) and spread across days with a territory -> branch -> region -> "
            "national fallback spine, same design as the SO side but self-contained -- no "
            "hand-entered monthly target required."
        )
 
        tdc_for_nt = clean_columns(tdc_raw.copy())
        nt_df = nt_forecast.filter_nt(tdc_for_nt)
 
        if nt_df.empty:
            st.warning("No NT rows found in this TDC extract -- check the TR/NT column values.")
        else:
            nt_c1, nt_c2 = st.columns(2)
            nt_target_year = nt_c1.number_input("Target year", min_value=2020, max_value=2100, value=2026, key="nt_target_year")
            nt_target_month = nt_c2.number_input("Target month", min_value=1, max_value=12, value=8, key="nt_target_month")
 
            with st.spinner("Building STO spine and trend..."):
                nt_spine = nt_forecast.build_spine(nt_df)
                nt_trend = nt_forecast.fit_trend(nt_df)
                nt_forecast_df = nt_forecast.forecast_month(
                    nt_df, nt_spine, nt_trend, int(nt_target_year), int(nt_target_month)
                )
                nt_forecast_df = attach_cost_profile(
                    nt_forecast_df, cost_profile, territory_col_in_forecast=COL_TERRITORY_NAME, qty_col="predicted_nt_qty"
                )
                if nt_forecast_df.attrs.get("unmatched_rows"):
                    st.warning(f"{nt_forecast_df.attrs['unmatched_rows']} rows didn't match a territory in the cost profile -- Distance/Freight columns will be blank for those, and truck priority will fall back to shorter-haul ordering for them.")
                nt_forecast_df = add_truck_requirement_columns(
                    nt_forecast_df,
                    date_col="date",
                    qty_col="predicted_nt_qty",
                    group_cols=[COL_TERRITORY_NAME],
                    distance_band_col="Distance_Band",
                )
 
            n1, n2, n3 = st.columns(3)
            n1.metric("Territories", nt_df[COL_TERRITORY_NAME].nunique())
            n2.metric("Months of history", nt_trend.attrs.get("n_months_available", 0))
            n3.metric("Pooled growth rate (fallback)", f"{nt_trend.attrs.get('pooled_growth_rate', 0.0):.2%}")
 
            st.caption("Confidence breakdown -- LOW means trust that territory's number less, not that it's wrong.")
            st.dataframe(nt_trend["confidence"].value_counts().rename("territories"), use_container_width=True)
 
            nt_territories = sorted(nt_forecast_df[COL_TERRITORY_NAME].unique())
            nt_selected = st.multiselect(
                "Select territories to compare (STO/NT)", nt_territories,
                default=nt_territories[:3], key="nt_selected"
            )
 
            if nt_selected:
                nt_chart_df = nt_forecast_df[nt_forecast_df[COL_TERRITORY_NAME].isin(nt_selected)]
                fig4 = px.line(
                    nt_chart_df, x="date", y="predicted_nt_qty", color=COL_TERRITORY_NAME,
                    markers=True,
                    title=f"Predicted daily STO (NT) qty -- {int(nt_target_year)}-{int(nt_target_month):02d}",
                )
                st.plotly_chart(fig4, use_container_width=True)
            else:
                st.caption("Select at least one territory to see its forecast curve.")
 
            with st.expander("STO (NT) forecast table"):
                st.dataframe(nt_forecast_df, use_container_width=True)
                st.download_button(
                    "Download STO (NT) daily forecast (CSV)",
                    nt_forecast_df.to_csv(index=False),
                    file_name=f"sto_nt_forecast_{int(nt_target_year)}_{int(nt_target_month):02d}.csv",
                )
 
            with st.expander("STO (NT) spine + trend detail"):
                st.dataframe(nt_spine, use_container_width=True)
                st.dataframe(nt_trend, use_container_width=True)