"""
territory_cost_profile.py
NEW FILE -- src/, alongside truck_requirements.py.

Builds a per-territory distance/freight profile from a TDC extract (e.g.
tdc_april_to_june_file.xlsx), then attaches it to an already-built SO or
STO daily forecast table (the same tables truck_requirements.py adds
T1/T2/Total_Trucks columns to).

WHY 'Freight Rate' and not 'Primaryfrt_amount' as the cost source (checked
2026-08-18): Primaryfrt_amount is 0 on ~2,700 rows where Freight Rate is
still populated and Distance/Qty exist normally -- Primaryfrt_amount looks
like a settled/invoiced amount that isn't always filled in yet, while
Freight Rate is populated whenever Distance/Qty are. Freight Rate / Qty
gives a real per-tonne rate (checked: e.g. Qty=42, Distance=351,
Freight Rate=48594 -> 48594/42=1157/tonne), NOT a flat per-tonne lookup
value itself -- despite the column name, it's a per-consignment TOTAL.

Distance/Distance from plant/Dist(prim+sec) are identical for this file's
scope (Satna-direct, single-leg dispatch, secondary-leg columns are ~0
throughout) -- Distance is used as the canonical distance field.
"""

import pandas as pd

TDC_COL_TERRITORY_NAME = "Territory Name"
TDC_COL_DISTANCE = "Distance"
TDC_COL_DISTANCE_CRITERIA = "Distance Criteria"
TDC_COL_FREIGHT_RATE = "Freight Rate"
TDC_COL_QTY = "Qty"


def build_territory_cost_profile(tdc_df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per territory:
        avg_distance_km          -- mean Distance across that territory's rows
        dominant_distance_band   -- most common Distance Criteria value
        freight_rate_per_tonne   -- volume-weighted avg (SUM(Freight Rate) /
                                     SUM(Qty), not a plain mean-of-ratios,
                                     so a few small/odd consignments don't
                                     skew the rate)
        n_rows_used              -- sample size backing this territory's
                                     profile, for a confidence check
    """
    d = tdc_df.dropna(subset=[TDC_COL_DISTANCE, TDC_COL_FREIGHT_RATE, TDC_COL_QTY]).copy()

    grouped = d.groupby(TDC_COL_TERRITORY_NAME)
    profile = grouped.agg(
        avg_distance_km=(TDC_COL_DISTANCE, "mean"),
        total_freight=(TDC_COL_FREIGHT_RATE, "sum"),
        total_qty=(TDC_COL_QTY, "sum"),
        n_rows_used=(TDC_COL_QTY, "count"),
    ).reset_index()

    profile["freight_rate_per_tonne"] = (profile["total_freight"] / profile["total_qty"]).round(1)
    profile["avg_distance_km"] = profile["avg_distance_km"].round(1)

    dominant_band = grouped[TDC_COL_DISTANCE_CRITERIA].agg(lambda s: s.mode().iloc[0] if len(s.mode()) else None)
    profile = profile.merge(dominant_band.rename("dominant_distance_band"), on=TDC_COL_TERRITORY_NAME)

    return profile[[TDC_COL_TERRITORY_NAME, "avg_distance_km", "dominant_distance_band",
                     "freight_rate_per_tonne", "n_rows_used"]]


def attach_cost_profile(
    forecast_df: pd.DataFrame,
    cost_profile: pd.DataFrame,
    territory_col_in_forecast: str,
    qty_col: str,
) -> pd.DataFrame:
    """
    Left-joins the territory cost profile onto a daily forecast table
    (matching on territory name -- confirm territory_col_in_forecast holds
    the SAME territory-name values as Territory Name in cost_profile before
    trusting the join; run a quick unmatched-territory check if unsure) and
    adds:
        Avg_Distance_km, Distance_Band, Freight_Rate_per_Tonne  -- carried
            straight from the profile, unchanged per territory
        Estimated_Daily_Freight_Cost = Freight_Rate_per_Tonne x that
            row's forecasted qty -- the cost implication of THIS day's
            volume, not a fixed number
    """
    d = forecast_df.merge(
        cost_profile,
        left_on=territory_col_in_forecast,
        right_on=TDC_COL_TERRITORY_NAME,
        how="left",
    )
    d = d.rename(columns={
        "avg_distance_km": "Avg_Distance_km",
        "dominant_distance_band": "Distance_Band",
        "freight_rate_per_tonne": "Freight_Rate_per_Tonne",
    })
    d["Estimated_Daily_Freight_Cost"] = (d["Freight_Rate_per_Tonne"] * d[qty_col]).round(0)

    unmatched = d["Freight_Rate_per_Tonne"].isna().sum()
    if unmatched:
        d.attrs["unmatched_rows"] = unmatched  # surfaced in app.py as a warning, not hidden

    return d