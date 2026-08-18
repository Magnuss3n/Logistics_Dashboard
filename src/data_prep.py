"""
data_prep.py
Loading + cleaning for the two data tracks that feed this dashboard:

  TRACK 1 -- Total ROAD volume baseline (STO-side, "how much moves by road at all")
    base_tdc_file_jan-july.xlsx -> Road only -> Trade(TR) only -> Grade U1/U2 only
    -> [Truck type T1/T2/T3, TOGGLE -- see TRUCK_TYPE_SCOPE, still unresolved]

  TRACK 2 -- SO (Sale Order) territory tagging (order-history side)
    Order Register (order history, no territory grain) is tagged with
    Territory/Branch/Region by joining to a TDC extract on Customer Code --
    TDC is dispatch-driven so it natively carries the real territory master.
    Any TDC extract works here (base_tdc_file_jan-july.xlsx OR a narrower
    file like tdc_april_to_june_file.xlsx) as long as it has CUSTOMER CODE,
    Territory Name, Territory, Branch, Region columns -- same export schema.

Both tracks share load_base_tdc()/clean_columns() as the entry point for any
TDC-shaped file.
"""

import pandas as pd
from pathlib import Path

# ---- column names as they appear in the TDC export (base_tdc_file_jan-july.xlsx
# and tdc_april_to_june_file.xlsx share this schema) -----------------------------
COL_DATE = "DATE"
COL_ROAD_RAKE = "RAKE/ROAD"
COL_TRNT = "TR/NT"
COL_VARIANT = "Variant"          # contains grade (U1/U2) OR TU code (TAM/TCH/TRD/TJB) depending on row
COL_TRUCK_TYPE = "Truck Type Clean"
COL_QTY = "Qty"
COL_REGION = "Region"
COL_BRANCH = "Branch"
COL_TERRITORY = "Territory"
COL_TERRITORY_NAME = "Territory Name"
COL_TU_SATNA = "TU/SATNA"
COL_CUSTOMER_CODE = "CUSTOMER CODE"
# confirmed against actual file header row on 2026-08-17 -- 62/63 cols, headers verified

GRADES_IN_SCOPE = {"U1", "U2"}

# TRUCK_TYPE_SCOPE options (Track 1 only -- does NOT apply to Order Register, which
# has no Truck Type field):
#   "strict" -> only rows explicitly tagged T1/T2/T3  (only ~18% of Road+TR+U1/U2 qty)
#   "broad"  -> T1/T2/T3 + DEPOT + MKT + DIRDE + DIV   (needs sign-off before use)
# UNRESOLVED as of last chat -- do not hardcode a default without checking with the team.
TRUCK_TYPE_STRICT = {"T1", "T2", "T3"}
TRUCK_TYPE_BROAD = {"T1", "T2", "T3", "DEPOT", "MKT", "DIRDE", "DIV"}

# ---- column names as they appear in the Order Register file --------------------
OREG_COL_DATE = "SALE ORDER DATE"
OREG_COL_CUST_CODE = "CUST CODE"
OREG_COL_CUST_NAME = "CUST NAME"
OREG_COL_QTY = "SALE ORDER QTY"
OREG_COL_TRADE_TYPE = "Trade_Type"
OREG_COL_SUPPLY_POINT = "SUPPLY POINT"  # normalized -- see load_order_register, which
# strips whitespace from every column name on load so this matches regardless of
# how the source file happened to pad the header (seen both "SUPPLY POINT " and
# "SUPPLY POINT" across different Order Register exports).
OREG_COL_ORDER_STATUS = "ORDER STATUS"
OREG_COL_REGION = "Region"  # sales-org region native to Order Register -- NOT the
# same thing as the TDC-derived Region; dropped before merging so it can't collide

SUPPLY_POINT_FILTER = "SATNAPLANT"
SO_VALID_TRADE_TYPES = {"NT", "TR"}


# =================================================================================
# TRACK 1 -- shared TDC loader (used by both tracks) + Road/Trade/Grade/Truck scope
# =================================================================================
def load_base_tdc(path: str | Path, sheet_name: str = "Sheet1") -> pd.DataFrame:
    """Load a raw TDC-schema export. No filtering yet. Works for any TDC extract
    (Jan-Jul base file, or a narrower Apr-Jun file), as long as headers match."""
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
    truck_type_scope: str | None = "strict",
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


# =================================================================================
# TRACK 2 -- SO (Order Register) territory tagging
# =================================================================================
def build_customer_territory_map(tdc_df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per customer code -> Territory Name/Territory/Branch/Region, built off
    a TDC extract (dispatch-driven, so it carries the real territory master).
    If a customer genuinely maps to >1 territory, the FIRST one encountered wins --
    run find_territory_conflicts() first to check whether that's a real risk for
    your file (it's been zero on every TDC extract checked so far).
    """
    cols = [COL_CUSTOMER_CODE, COL_TERRITORY_NAME, COL_TERRITORY, COL_BRANCH, COL_REGION]
    cust_map = tdc_df[cols].drop_duplicates(subset=COL_CUSTOMER_CODE)
    return cust_map.reset_index(drop=True)


def find_territory_conflicts(tdc_df: pd.DataFrame) -> pd.DataFrame:
    """Customers that map to more than one territory in the TDC file -- should be empty."""
    grp = tdc_df.groupby(COL_CUSTOMER_CODE)[COL_TERRITORY].nunique()
    conflict_codes = grp[grp > 1].index
    return tdc_df[tdc_df[COL_CUSTOMER_CODE].isin(conflict_codes)]


def load_order_register(path: str | Path, sheet_name: int | str = 0) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet_name)

    # Normalize column names so exports with accidental
    # leading/trailing spaces still work.
    df.columns = df.columns.astype(str).str.strip()

    return df

def clean_order_register(
    oreg_df: pd.DataFrame,
    supply_point: str = SUPPLY_POINT_FILTER,
    trade_types: set[str] = SO_VALID_TRADE_TYPES,
    date_start: str | None = None,
    date_end: str | None = None,
) -> pd.DataFrame:
    """
    Filters Order Register to:
      - Supply Point == given plant (default Satna) only
      - Trade_Type in the given set (default {NT, TR} -- both kept, tagged
        separately downstream so you can slice by trade type later)
      - optional date window (e.g. Apr-Jun to match a TDC history window)
    """
    df = oreg_df.copy()
    df[OREG_COL_SUPPLY_POINT] = df[OREG_COL_SUPPLY_POINT].str.strip()
    df[OREG_COL_DATE] = pd.to_datetime(df[OREG_COL_DATE])

    df = df[df[OREG_COL_SUPPLY_POINT] == supply_point]
    df = df[df[OREG_COL_TRADE_TYPE].isin(trade_types)]

    if date_start:
        df = df[df[OREG_COL_DATE] >= pd.Timestamp(date_start)]
    if date_end:
        df = df[df[OREG_COL_DATE] <= pd.Timestamp(date_end)]

    return df.reset_index(drop=True)


def tag_so_territory(so_df: pd.DataFrame, cust_map: pd.DataFrame) -> pd.DataFrame:
    """
    Order Register carries its own 'Region' column (a sales-org region, NOT the
    TDC geography) -- drop it before merging so only the TDC-derived
    Region/Branch/Territory carries through and we don't get a silent
    Region_x/Region_y split.
    """
    so_df = so_df.drop(columns=[OREG_COL_REGION], errors="ignore")
    tagged = so_df.merge(
        cust_map,
        left_on=OREG_COL_CUST_CODE,
        right_on=COL_CUSTOMER_CODE,
        how="left",
    )
    return tagged


def so_match_summary(tagged_df: pd.DataFrame) -> dict:
    """QA numbers -- how much SO qty matched vs fell through, by trade type."""
    matched_mask = tagged_df[COL_TERRITORY].notna()
    total_qty = tagged_df[OREG_COL_QTY].sum()
    unmatched_qty = tagged_df.loc[~matched_mask, OREG_COL_QTY].sum()

    by_trade = (
        tagged_df.loc[~matched_mask]
        .groupby(OREG_COL_TRADE_TYPE)[OREG_COL_QTY]
        .sum()
        .to_dict()
    )

    return {
        "total_rows": len(tagged_df),
        "matched_rows": int(matched_mask.sum()),
        "unmatched_rows": int((~matched_mask).sum()),
        "total_qty": round(total_qty, 1),
        "unmatched_qty": round(unmatched_qty, 1),
        "unmatched_pct": round(unmatched_qty / total_qty * 100, 2) if total_qty else 0.0,
        "unmatched_qty_by_trade_type": {k: round(v, 1) for k, v in by_trade.items()},
    }


if __name__ == "__main__":
    # quick manual check when run directly: python src/data_prep.py

    # Track 1 -- total road volume baseline
    raw = load_base_tdc("data/base_tdc_file_jan-july.xlsx")
    raw = clean_columns(raw)
    filtered, funnel = apply_scope_filters(raw, truck_type_scope=None)  # pre-truck-type, safe default
    print(funnel)
    daily = aggregate_daily(filtered)
    print(daily.head(20))

    # Track 2 -- SO territory tagging
    cust_map = build_customer_territory_map(raw)
    oreg = load_order_register("data/Order_Register_-_Apr_26_to_July_26.xlsx")
    oreg_clean = clean_order_register(oreg, date_start="2026-04-01", date_end="2026-06-30")
    tagged = tag_so_territory(oreg_clean, cust_map)
    print(so_match_summary(tagged))