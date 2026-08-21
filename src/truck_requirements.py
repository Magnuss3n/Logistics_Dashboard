"""
truck_requirements.py
src/, alongside forecasting.py.

REVERTED 2026-08-18 back to the carry-forward design (an optimizer variant
was tried in between and rejected -- carry-forward is the real-world
behavior wanted: a truck only counts as "needed" once its size is fully
met; whatever tonnage doesn't fill a truck that day rolls into the next
day's volume for that same territory, same as the reference Dispatch
Planning sheet's INT(qty/size) + MOD(qty,size) formulas).

    trucks_needed_today = INT(volume_today / truck_size)
    leftover_today      = MOD(volume_today, truck_size)
    volume_tomorrow_adjusted = volume_tomorrow + leftover_today

T1 (35t) and T2 (42t) are two INDEPENDENT carry-forward chains -- each
computed as if that truck type alone were used for the whole series, with
its own leftover rolling forward day to day. Total_Trucks is a THIRD,
separate carry-forward chain using LCM(35,42)=210 as its own divisor --
NOT T1_trucks + T2_trucks.

Requires the input table to already be one row per (group, day) with a
forecasted qty column, and MUST be sorted chronologically within each
group before the carry-forward loop runs (this function sorts it).
"""

from math import gcd

import pandas as pd

DEFAULT_TRUCK_SIZES = {"T1": 35, "T2": 42}


def _lcm(a: int, b: int) -> int:
    return a * b // gcd(a, b)


def add_truck_requirement_columns(
    df: pd.DataFrame,
    date_col: str,
    qty_col: str,
    group_cols: list[str],
    truck_sizes: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Adds, per truck type in truck_sizes (default T1=35t, T2=42t):
        <label>_trucks          -- INT(volume / size) for that day, after
                                    absorbing yesterday's leftover
        <label>_carry_forward   -- leftover tonnage rolled into the next
                                    day within the same group (kept as its
                                    own column for audit -- not hidden)

    Plus, using LCM(all truck sizes) as an independent divisor with its
    own independent carry-forward chain:
        Total_Trucks
        Total_Trucks_carry_forward

    group_cols: identifies one forecast series to carry remainder within
    (e.g. ["region_code","territory_name"] for the SO table,
    [COL_TERRITORY_NAME] for the STO/NT table). Carry-forward never
    crosses a group boundary.

    date_col: used to sort each group into chronological order before the
    sequential carry-forward loop runs.
    """
    if truck_sizes is None:
        truck_sizes = DEFAULT_TRUCK_SIZES

    d = df.sort_values(group_cols + [date_col]).reset_index(drop=True).copy()

    def _run_carry_forward(divisor: float, trucks_col: str, carry_col: str):
        d[trucks_col] = 0
        d[carry_col] = 0.0
        for _, idx in d.groupby(group_cols, sort=False).groups.items():
            carry = 0.0
            for i in idx:
                available = d.at[i, qty_col] + carry
                trucks = int(available // divisor)
                carry = available - trucks * divisor
                d.at[i, trucks_col] = trucks
                d.at[i, carry_col] = round(carry, 2)

    for label, size in truck_sizes.items():
        _run_carry_forward(size, f"{label}_trucks", f"{label}_carry_forward")

    sizes = list(truck_sizes.values())
    combined_lcm = sizes[0]
    for s in sizes[1:]:
        combined_lcm = _lcm(int(combined_lcm), int(s))

    _run_carry_forward(combined_lcm, "Total_Trucks", "Total_Trucks_carry_forward")
    d.attrs["lcm_used"] = combined_lcm

    return d
