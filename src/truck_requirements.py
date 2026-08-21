"""
truck_requirements.py
src/, alongside forecasting.py.

REVISED 2026-08-18 -- T1/T2 are no longer two independent carry-forward
chains. Truck TYPE PRIORITY is now driven by that territory's distance band
(from territory_cost_profile.py's Distance_Band column, which MUST already
be attached to the input df -- run attach_cost_profile() BEFORE this
function, not after):

    Longer haul (Distance_Band in {'281-340','Above 341k'})
        -> prefer T2 (42t) first: fewer, larger loads matter more the
           farther a truck has to travel, since each trip consumes more
           time/turnaround capacity.
    Shorter haul (Distance_Band in {'0-220','221-280'})
        -> prefer T1 (35t) first: faster cycle times, no need to wait to
           accumulate a big load when the round trip is quick anyway.

Still a REAL carry-forward, same as before: whatever doesn't fill a full
truck of EITHER type that day rolls into the next day's volume for that
territory. T1_trucks + T2_trucks now = Total_Trucks exactly, since this is
one combined plan per day, not two independent what-ifs plus a separate
LCM-based total.

Requires the input table to already have a Distance_Band column (from
territory_cost_profile.attach_cost_profile) and to be one row per
(group, day) with a forecasted qty column.
"""

import pandas as pd

DEFAULT_TRUCK_SIZES = {"T1": 35, "T2": 42}
LONG_HAUL_BANDS = {"281-340", "Above 341k"}


def _priority_order(distance_band: str, truck_sizes: dict[str, float]) -> list[str]:
    """Which truck type to fill first, for a given territory's distance band."""
    labels_by_size_desc = sorted(truck_sizes, key=lambda k: truck_sizes[k], reverse=True)
    labels_by_size_asc = list(reversed(labels_by_size_desc))
    if distance_band in LONG_HAUL_BANDS:
        return labels_by_size_desc   # bigger truck first
    return labels_by_size_asc        # smaller truck first (also the fallback for an unknown/missing band)


def add_truck_requirement_columns(
    df: pd.DataFrame,
    date_col: str,
    qty_col: str,
    group_cols: list[str],
    distance_band_col: str = "Distance_Band",
    truck_sizes: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Adds, per truck type in truck_sizes (default T1=35t, T2=42t):
        <label>_trucks   -- count for that day, filled in DISTANCE-DRIVEN
                             PRIORITY ORDER (see module docstring), after
                             absorbing yesterday's leftover
    Plus:
        Total_Trucks              -- T1_trucks + T2_trucks (a real combined
                                      total now, since both types are part
                                      of ONE plan per day)
        Carry_Forward              -- the single leftover after both truck
                                      types have been filled as far as
                                      possible, rolled into tomorrow's
                                      volume for that territory
        Truck_Priority_Used        -- which type was tried first that day,
                                      for transparency/audit

    group_cols must resolve to a SINGLE distance_band per group (one
    territory = one band) -- this is checked; a group with more than one
    distinct band raises, since priority order would be ambiguous.
    date_col: sorts each group into chronological order before the
    sequential carry-forward loop runs.
    """
    if truck_sizes is None:
        truck_sizes = DEFAULT_TRUCK_SIZES

    d = df.sort_values(group_cols + [date_col]).reset_index(drop=True).copy()

    for label in truck_sizes:
        d[f"{label}_trucks"] = 0
    d["Total_Trucks"] = 0
    d["Carry_Forward"] = 0.0
    d["Truck_Priority_Used"] = ""

    for _, idx in d.groupby(group_cols, sort=False).groups.items():
        bands = d.loc[idx, distance_band_col].dropna().unique()
        if len(bands) > 1:
            raise ValueError(f"Group {group_cols} has multiple distance bands {bands} -- ambiguous priority order.")
        band = bands[0] if len(bands) else None
        order = _priority_order(band, truck_sizes)

        carry = 0.0
        for i in idx:
            available = d.at[i, qty_col] + carry
            total_trucks_today = 0
            for label in order:
                size = truck_sizes[label]
                count = int(available // size)
                available -= count * size
                d.at[i, f"{label}_trucks"] = count
                total_trucks_today += count
            carry = available
            d.at[i, "Total_Trucks"] = total_trucks_today
            d.at[i, "Carry_Forward"] = round(carry, 2)
            d.at[i, "Truck_Priority_Used"] = " then ".join(order)

    return d