"""
forecasting.py
Day-by-day demand forecast, built from historical patterns.

Works for BOTH tracks in this dashboard:
  - Track 1 (total road volume, from aggregate_daily()): pass
    group_cols=["Region","Branch","Territory"]
  - Track 2 (SO, from data_prep.tag_so_territory()): pass the same
    group_cols -- SO-tagged rows carry Region/Branch/Territory from the
    TDC join, so no separate SO-specific function is needed.

STATUS:
  - day_of_month_share / day_of_week_share: LIVE, validated against the
    SO Order Register -> TDC territory join (65 territories, every
    distribution confirmed to sum to 1.0).
  - distribute_monthly_target_to_days / validate_against_actuals: still
    blocked on the Final DD targets Region-wise (Aug-26 column) input --
    "august dd numbers can be incorporated later" per chat. Wire these up
    once that target file is ready; until then this only produces the
    historical-pattern half (the day_share_of_month curve itself), not a
    scaled Aug-26 forecast.
"""

import pandas as pd


def day_of_month_share(daily: pd.DataFrame, date_col: str, qty_col: str, group_cols: list[str]) -> pd.DataFrame:
    """
    For each group (e.g. Region/Branch/Territory), compute what % of the
    historical window's volume typically falls on each day-of-month --
    POOLED across the whole window: sum(qty on day d, all months) /
    sum(qty in group, all months). Fractions sum to exactly 1.0 per group.

    This is the validated version (matches the SO territory pipeline check:
    65 territories, every distribution confirmed to sum to 1.0). Use this
    one unless you specifically want day_of_month_share_avg_monthly instead.

    NOTE: an earlier draft of this function averaged each day's *monthly*
    fraction across months instead of pooling first. That silently breaks
    the sum-to-1.0 guarantee whenever a group doesn't have data on the same
    day in every historical month -- true for most territories here (894 of
    1,403 territory-day combos only appear in 1-2 of the 3 months checked).
    Kept below as day_of_month_share_avg_monthly for cases where damping one
    anomalous month matters more than the fractions summing to 1.0.
    """
    d = daily.copy()
    d["day"] = d[date_col].dt.day

    group_totals = d.groupby(group_cols)[qty_col].transform("sum")
    d["_qty_share"] = d[qty_col] / group_totals

    pattern = (
        d.groupby(group_cols + ["day"])["_qty_share"]
        .sum()
        .reset_index()
        .rename(columns={"_qty_share": "day_share_of_month"})
    )
    return pattern


def day_of_month_share_avg_monthly(daily: pd.DataFrame, date_col: str, qty_col: str, group_cols: list[str]) -> pd.DataFrame:
    """
    Alternate method: for each group, compute what % of EACH month's volume
    falls on each day-of-month, then average that fraction across the
    historical months available. Does NOT sum to 1.0 per group when a group
    lacks data on the same day in every month (common with sparse order
    data) -- only use this if you specifically want to damp one anomalous
    month's influence rather than pool the raw totals.
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


def allocate_target_to_territories(
    branch_target_qty: float,
    branch: str,
    historical_tagged_df: pd.DataFrame,
    branch_col: str,
    territory_col: str,
    territory_name_col: str,
    qty_col: str,
) -> pd.DataFrame:
    """
    Splits a branch-level Aug-26 monthly target down to each territory under
    it, weighted by that territory's share of the branch's HISTORICAL qty
    (from the same Order Register -> TDC tagged data used to build the
    day-of-month curves). Weights sum to 1.0 within the branch, so the
    territory targets sum back to exactly branch_target_qty.

    territory_col (the code) is still used as the grouping/weighting key --
    that logic is unchanged. territory_name_col is looked up per code purely
    for display and carried through as an extra "territory_name" column.
    """
    branch_rows = historical_tagged_df[historical_tagged_df[branch_col] == branch]
    if branch_rows.empty:
        raise ValueError(f"No historical rows found for branch '{branch}' -- check the branch name/mapping.")

    terr_totals = branch_rows.groupby(territory_col)[qty_col].sum()
    weights = terr_totals / terr_totals.sum()
    name_map = branch_rows.groupby(territory_col)[territory_name_col].first()

    return pd.DataFrame({
        territory_col: weights.index,
        "territory_name": weights.index.map(name_map),
        "territory_share_of_branch": weights.values,
        "territory_target_qty": weights.values * branch_target_qty,
    })


def build_daily_territory_forecast(
    aug26_targets: dict,
    region_code_to_branch: dict,
    historical_tagged_df: pd.DataFrame,
    branch_col: str,
    territory_col: str,
    territory_name_col: str,
    date_col: str,
    qty_col: str,
) -> pd.DataFrame:
    """
    End-to-end: for every region_code in aug26_targets, split its monthly
    target across territories (allocate_target_to_territories), then spread
    each territory's target across days using its own day_of_month_share
    curve (distribute_monthly_target_to_days). Returns one row per
    territory x day with a forecast_qty column.

    territory_col (the code) is still used internally to match each
    territory's day-of-month pattern -- that lookup is unchanged. The
    returned table carries "territory_name" instead of the code column,
    since the code is only needed for the internal join, not for display.
    """
    day_pattern = day_of_month_share(
        historical_tagged_df, date_col=date_col, qty_col=qty_col,
        group_cols=[branch_col, territory_col],
    )

    all_forecasts = []
    for region_code, target_info in aug26_targets.items():
        branch = region_code_to_branch.get(region_code)
        if branch is None:
            continue  # unmapped region_code -- excluded, same as the Branch-match check

        terr_split = allocate_target_to_territories(
            branch_target_qty=target_info["target_qty"],
            branch=branch,
            historical_tagged_df=historical_tagged_df,
            branch_col=branch_col, territory_col=territory_col,
            territory_name_col=territory_name_col, qty_col=qty_col,
        )

        for _, row in terr_split.iterrows():
            terr_pattern = day_pattern[
                (day_pattern[branch_col] == branch) & (day_pattern[territory_col] == row[territory_col])
            ]
            daily = distribute_monthly_target_to_days(row["territory_target_qty"], terr_pattern)
            daily[branch_col] = branch
            daily["territory_name"] = row["territory_name"]
            daily["region_code"] = region_code
            all_forecasts.append(daily)

    return pd.concat(all_forecasts, ignore_index=True)


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