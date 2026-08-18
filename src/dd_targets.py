"""
dd_targets.py
Aug-26 monthly targets, hand-transcribed from the "Final DD targets
Region-wise" sheet (screenshot, checked 2026-08-18).

Rule applied per region, per user confirmation:
  - STNA, JBLP  -> no Road breakout on that sheet, so take the "DD" row's
                   Aug-26 Target
  - all others  -> take the "Road" row's Aug-26 Target

These are MONTHLY totals for Aug-26, at region-code grain (STNA/JBLP/GWLR/
KNPR/PRYG/VRNS/GKHP) -- NOT yet split down to individual territories. To
turn this into a daily, per-territory number you still need two more steps
(see distribute_target_by_territory_and_day below):
  1. Split the region-code target down to each territory under it, weighted
     by that territory's historical share of the region-code's volume.
  2. Apply forecasting.day_of_month_share's per-territory curve to spread
     each territory's monthly share across days.

If the source sheet updates, only this dict needs to change -- nothing else
in the pipeline references raw numbers directly.
"""

import pandas as pd

# region_code -> (Zone, Aug-26 monthly target qty, source_row_used)
AUG26_DD_TARGETS = {
    "STNA": {"zone": "MP", "target_qty": 21674, "source_row": "DD"},
    "JBLP": {"zone": "MP", "target_qty": 8266, "source_row": "DD"},
    "GWLR": {"zone": "CUP", "target_qty": 16149, "source_row": "Road"},
    "KNPR": {"zone": "CUP", "target_qty": 18038, "source_row": "Road"},
    "PRYG": {"zone": "CUP", "target_qty": 52251, "source_row": "Road"},
    "VRNS": {"zone": "EUP", "target_qty": 25416, "source_row": "Road"},
    "GKHP": {"zone": "EUP", "target_qty": 4813, "source_row": "Road"},
}


# region_code (as it appears in the DD targets sheet) -> Branch (as it
# appears in TDC/Order Register data). Checked against tdc_april_to_june_file.xlsx
# Branch column on 2026-08-18 -- all 7 matched, none excluded.
REGION_CODE_TO_BRANCH = {
    "STNA": "SATNA",
    "JBLP": "JABALPUR",
    "GWLR": "GWALIOR",
    "KNPR": "KANPUR",
    "PRYG": "PRAYAGRAJ",
    "VRNS": "VARANASI",
    "GKHP": "GORAKHPUR",
}


def get_aug26_target(region_code: str) -> float:
    """Aug-26 monthly target qty for a given region code (e.g. 'STNA')."""
    entry = AUG26_DD_TARGETS.get(region_code.strip().upper())
    if entry is None:
        raise KeyError(f"No Aug-26 DD target found for region code '{region_code}'")
    return entry["target_qty"]


def aug26_targets_as_df() -> pd.DataFrame:
    """Same data as a DataFrame, for display/merge convenience in the dashboard."""
    rows = [
        {"region_code": code, **vals}
        for code, vals in AUG26_DD_TARGETS.items()
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# RESOLVED 2026-08-18 -- region_code maps 1:1 onto Branch (REGION_CODE_TO_BRANCH
# above). All 7 codes matched an existing Branch value in the TDC file, no
# exclusions needed.
# ---------------------------------------------------------------------------
