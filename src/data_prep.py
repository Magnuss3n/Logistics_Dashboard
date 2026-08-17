"""
data_prep.py
Loading + cleaning + scope-filtering for the base TDC file (Jan-Jul 2026).

This mirrors exactly the filter funnel we validated in chat:
  Raw -> Road only -> Trade(TR) only -> Grade U1/U2 only -> [Truck type T1/T2/T3]

The last step (truck type) is left as a TOGGLE, not hard-coded, because we have
NOT yet confirmed whether DEPOT/MKT/DIRDE/DIV rows are in scope for truck-sizing.
See TRUCK_TYPE_SCOPE below. Do not ship a board number using 'strict' scope
until that question is answered.
"""

import pandas as pd
from pathlib import Path

# ---- column names as they appear in base_tdc_file_jan-july.xlsx, Sheet1 ----
COL_DATE = "DATE"
COL_ROAD_RAKE = "RAKE/ROAD"
COL_TRNT = "TR/NT"
COL_VARIANT = "Variant"          # contains grade (U1/U2) OR TU code (TAM/TCH/TRD/TJB) depending on row
COL_TRUCK_TYPE = "Truck Type Clean"
COL_QTY = "Qty"
COL_REGION = "Region"
COL_BRANCH = "Branch"
COL_TERRITORY = "Territory"
COL_TU_SATNA = "TU/SATNA"
# confirmed against actual file header row on 2026-08-17 -- 62 cols, headers verified

GRADES_IN_SCOPE = {"U1", "U2"}

# TRUCK_TYPE_SCOPE options:
#   "strict" -> only rows explicitly tagged T1/T2/T3  (only ~18% of Road+TR+U1/U2 qty)
#   "broad"  -> T1/T2/T3 + DEPOT + MKT + DIRDE + DIV   (needs sign-off before use)
# UNRESOLVED as of last chat -- do not hardcode a default without checking with the team.
TRUCK_TYPE_STRICT = {"T1", "T2", "T3"}
TRUCK_TYPE_BROAD = {"T1", "T2", "T3", "DEPOT", "MKT", "DIRDE", "DIV"}


def load_base_tdc(path: str | Path, sheet_name: str = "Sheet1") -> pd.DataFrame:
    """Load the raw base TDC file. No filtering yet."""
    df = pd.read_excel(path, sheet_name=sheet_name)
    return df


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Trim whitespace / standardize case on the key text columns used for filtering."""
    df = df.copy()
    text_cols = [COL_ROAD_RAKE, COL_TRNT, COL_VARIANT, COL_TRUCK_TYPE, COL_REGION, COL_TU_SATNA]
    for c in text_cols:
        if c in df.columns:
            df[c] = df[c].astype("string").str.strip().str.upper()
    return df


def apply_scope_filters(
    df: pd.DataFrame,
    truck_type_scope: str = "strict",
    return_funnel: bool = True,
):
    """
    Apply the confirmed filter funnel. Returns (filtered_df, funnel_report_df).

    truck_type_scope: "strict" | "broad" | None
        None skips the truck-type filter entirely (useful for the board-meeting
        stopgap: Road+TR+U1/U2 only, pre-truck-type, which we already validated).
    """
    steps = []
    steps.append(("0_raw", df))

    f1 = df[df[COL_ROAD_RAKE] == "ROAD"]
    steps.append(("1_road_only", f1))

    f2 = f1[f1[COL_TRNT] == "TR"]
    steps.append(("2_trade_only", f2))

    f3 = f2[f2[COL_VARIANT].isin(GRADES_IN_SCOPE)]
    steps.append(("3_grade_u1_u2", f3))

    if truck_type_scope is None:
        final = f3
    else:
        allowed = TRUCK_TYPE_STRICT if truck_type_scope == "strict" else TRUCK_TYPE_BROAD
        f4 = f3[f3[COL_TRUCK_TYPE].isin(allowed)]
        steps.append((f"4_truck_type_{truck_type_scope}", f4))
        final = f4

    if not return_funnel:
        return final

    funnel = pd.DataFrame(
        [{"step": name, "rows": len(d), "qty": d[COL_QTY].sum()} for name, d in steps]
    )
    return final, funnel


def aggregate_daily(
    df: pd.DataFrame,
    group_cols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Aggregate the filtered dataset to a daily volume table.
    Default grouping: Date x Region x Branch x Territory.
    Adjust group_cols to match confirmed header names once verified against the file.
    """
    if group_cols is None:
        group_cols = [COL_DATE, COL_REGION, COL_BRANCH, COL_TERRITORY]
    daily = (
        df.groupby(group_cols, dropna=False)[COL_QTY]
        .sum()
        .reset_index()
        .sort_values(group_cols)
    )
    return daily


if __name__ == "__main__":
    # quick manual check when run directly: python src/data_prep.py
    raw = load_base_tdc("data/base_tdc_file_jan-july.xlsx")
    raw = clean_columns(raw)
    filtered, funnel = apply_scope_filters(raw, truck_type_scope=None)  # pre-truck-type, safe default
    print(funnel)
    daily = aggregate_daily(filtered)
    print(daily.head(20))
