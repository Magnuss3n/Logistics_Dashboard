"""
forecasting.py
Day-by-day demand forecast for August, built from the Jan-Jul historical pattern.

STATUS: skeleton only. Wire this up once:
  1) truck_type_scope question is resolved in data_prep.py
  2) Final DD targets Region-wise (Aug-26 column) is ready to bring in
     (per chat: "august dd numbers can be incorporated later" -- so this
     currently only does the historical-pattern half, not the target-scaling half)
"""

import pandas as pd


def day_of_month_share(daily: pd.DataFrame, date_col: str, qty_col: str, group_cols: list[str]) -> pd.DataFrame:
    """
    For each group (e.g. Region), compute what % of a month's volume typically
    falls on each day-of-month, averaged across the historical months available.
    """
    d = daily.copy()
    d["month"] = d[date_col].dt.to_period("M")
    d["day"] = d[date_col].dt.day

    month_totals = d.groupby(group_cols + ["month"])[qty_col].transform("sum")
    d["day_share_of_month"] = d[qty_col] / month_totals

    pattern = (
        d.groupby(group_cols + ["day"])["day_share_of_month"]
        .mean()
        .reset_index()
    )
    return pattern


def day_of_week_share(daily: pd.DataFrame, date_col: str, qty_col: str, group_cols: list[str]) -> pd.DataFrame:
    """Same idea, but by day-of-week (0=Mon..6=Sun) -- useful for Sunday dip effects etc."""
    d = daily.copy()
    d["dow"] = d[date_col].dt.dayofweek
    d["week"] = d[date_col].dt.to_period("W")

    week_totals = d.groupby(group_cols + ["week"])[qty_col].transform("sum")
    d["dow_share_of_week"] = d[qty_col] / week_totals

    pattern = (
        d.groupby(group_cols + ["dow"])["dow_share_of_week"]
        .mean()
        .reset_index()
    )
    return pattern


def distribute_monthly_target_to_days(
    monthly_target: float,
    day_pattern: pd.DataFrame,
    day_col: str = "day",
    share_col: str = "day_share_of_month",
    n_days: int = 31,
) -> pd.DataFrame:
    """
    TODO (blocked on Aug DD numbers per chat -- 'can be incorporated later'):
    Once Final DD targets Region-wise Aug-26 target is ready to pull in, this
    takes that single monthly number and spreads it across days 1..n_days
    using the historical day_pattern computed above.
    """
    pattern = day_pattern[day_pattern[day_col] <= n_days].copy()
    # re-normalize in case pattern doesn't sum to exactly 1 after truncation
    pattern[share_col] = pattern[share_col] / pattern[share_col].sum()
    pattern["forecast_qty"] = pattern[share_col] * monthly_target
    return pattern[[day_col, "forecast_qty"]]


def validate_against_actuals(forecast: pd.DataFrame, actual: pd.DataFrame, join_cols: list[str], value_col: str):
    """
    Sanity check: for any historical month where we have both a target/actual
    (from Final DD targets Region-wise) and our own aggregated actual (from
    base_tdc), compare them. Use this before trusting the pipeline for August.
    """
    merged = forecast.merge(actual, on=join_cols, suffixes=("_forecast", "_actual"))
    merged["delta_pct"] = (
        (merged[f"{value_col}_forecast"] - merged[f"{value_col}_actual"])
        / merged[f"{value_col}_actual"]
    ) * 100
    return merged
