"""
src/nt_forecast.py

STO forecasting, using TR/NT == 'NT' as the STO proxy -- straight off the
same TDC extract already uploaded for the SO tab's customer-territory map.
No Order Register join needed here: TDC carries Territory Name/Branch/Region
natively for NT rows, same as it does for TR.

Design carried over from the standalone nt_forecast.py prototype, wired to
data_prep's shared column constants so it drops into the existing pipeline
instead of hardcoding its own column names:

  SPINE   day-of-month % shape per territory, with a territory -> branch ->
          region -> national fallback ladder for territories under
          min_records rows. Same fallback idea as the SO tab, just resolved
          at build time instead of leaving thin territories unspined.

  TREND   log-linear fit on monthly totals per territory. Gets more stable
          automatically as more months are fed in -- territories under
          min_months get a trimmed pooled growth rate instead, and every
          territory carries an explicit LOW/MEDIUM/HIGH confidence tag
          rather than a silently-noisy number.

  FORECAST  projects each territory's last known month forward by
            (1 + growth) ** steps to hit the target month, then spreads
            that total across days using the spine.

Usage (mirrors app.py's tab2 SO flow):
    nt = filter_nt(tdc_raw)                       # tdc_raw already loaded
    spine = build_spine(nt, min_records=30)
    trend = fit_trend(nt, min_months=4)
    forecast = forecast_month(nt, spine, trend, target_year=2026, target_month=8)
"""

import calendar

import numpy as np
import pandas as pd

from src.data_prep import (
    COL_BRANCH,
    COL_DATE,
    COL_QTY,
    COL_REGION,
    COL_TERRITORY_NAME,
    COL_TRNT,
)


def filter_nt(df: pd.DataFrame) -> pd.DataFrame:
    """Isolate NT (STO proxy) rows and add day-of-month / month helper columns."""
    out = df[df[COL_TRNT] == "NT"].copy()
    out[COL_DATE] = pd.to_datetime(out[COL_DATE])
    out[COL_QTY] = pd.to_numeric(out[COL_QTY], errors="coerce")
    out["dom"] = out[COL_DATE].dt.day
    out["month"] = out[COL_DATE].dt.to_period("M")
    return out


# ---------------------------------------------------------------------
# SPINE -- territory -> branch -> region -> national fallback ladder,
# same fallback logic the prototype validated, just on shared columns.
# ---------------------------------------------------------------------

def _day_pct_table(frame: pd.DataFrame, keycol: str) -> pd.Series:
    g = frame.groupby([keycol, "dom"])[COL_QTY].sum().reset_index()
    tot = frame.groupby(keycol)[COL_QTY].sum().rename("total")
    g = g.merge(tot, on=keycol)
    g["pct"] = g[COL_QTY] / g["total"] * 100
    return g.set_index([keycol, "dom"])["pct"]


def build_spine(nt_df: pd.DataFrame, min_records: int = 30) -> pd.DataFrame:
    rec_counts = nt_df.groupby(COL_TERRITORY_NAME).size().rename("n_records")
    terr_curve = _day_pct_table(nt_df, COL_TERRITORY_NAME)
    branch_curve = _day_pct_table(nt_df, COL_BRANCH)
    region_curve = _day_pct_table(nt_df, COL_REGION)
    national = nt_df.groupby("dom")[COL_QTY].sum()
    national_pct = national / national.sum() * 100

    terr_branch_map = nt_df.groupby(COL_TERRITORY_NAME)[COL_BRANCH].first()
    terr_region_map = nt_df.groupby(COL_TERRITORY_NAME)[COL_REGION].first()

    rows = []
    for terr in nt_df[COL_TERRITORY_NAME].unique():
        n = int(rec_counts.get(terr, 0))
        branch = terr_branch_map[terr]
        region = terr_region_map[terr]
        branch_n = nt_df[nt_df[COL_BRANCH] == branch].shape[0]
        region_n = nt_df[nt_df[COL_REGION] == region].shape[0]

        if n >= min_records:
            source, curve = "territory", terr_curve.loc[terr]
        elif branch_n >= min_records:
            source, curve = "branch_fallback", branch_curve.loc[branch]
        elif region_n >= min_records:
            source, curve = "region_fallback", region_curve.loc[region]
        else:
            source, curve = "national_fallback", national_pct

        for d in range(1, 32):
            rows.append({
                COL_TERRITORY_NAME: terr,
                "dom": d,
                "nt_pct": round(float(curve.get(d, 0.0)), 4),
                "spine_source": source,
                "n_records": n,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------
# TREND -- log-linear fit on monthly totals per territory. Explicit
# confidence tag instead of hiding thin-history risk.
# ---------------------------------------------------------------------

def fit_trend(nt_df: pd.DataFrame, min_months: int = 4) -> pd.DataFrame:
    monthly = nt_df.groupby([COL_TERRITORY_NAME, "month"])[COL_QTY].sum().reset_index()
    monthly = monthly.sort_values([COL_TERRITORY_NAME, "month"])

    results = []
    all_growth = []
    for terr, g in monthly.groupby(COL_TERRITORY_NAME):
        g = g[g[COL_QTY] > 0]
        n_months = len(g)
        if n_months >= 2:
            idx = np.arange(n_months)
            logq = np.log(g[COL_QTY].values)
            slope, intercept = np.polyfit(idx, logq, 1)
            pred = slope * idx + intercept
            ss_res = np.sum((logq - pred) ** 2)
            ss_tot = np.sum((logq - logq.mean()) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            growth_rate = float(np.exp(slope) - 1)
            all_growth.append(growth_rate)
        else:
            growth_rate, r2 = np.nan, np.nan
        results.append({
            COL_TERRITORY_NAME: terr,
            "n_months": n_months,
            "growth_rate": growth_rate,
            "r_squared": r2,
            "last_month_total": float(g[COL_QTY].iloc[-1]) if n_months else 0.0,
            "last_month": str(g["month"].iloc[-1]) if n_months else None,
        })

    trend_df = pd.DataFrame(results)
    trimmed = np.array(sorted(all_growth))
    if len(trimmed) >= 5:
        cut = max(1, int(len(trimmed) * 0.1))
        trimmed = trimmed[cut:-cut]
    pooled_growth = float(np.mean(trimmed)) if len(trimmed) else 0.0

    def confidence(row):
        if row["n_months"] < min_months:
            return "LOW (thin history)"
        if pd.isna(row["r_squared"]):
            return "LOW (thin history)"
        if row["r_squared"] >= 0.5:
            return "MEDIUM" if row["n_months"] < 6 else "HIGH"
        return "LOW (noisy trend)"

    trend_df["growth_source"] = np.where(trend_df["n_months"] >= min_months, "territory_fit", "pooled")
    trend_df["growth_rate"] = np.where(
        trend_df["n_months"] >= min_months, trend_df["growth_rate"], pooled_growth
    )
    trend_df["confidence"] = trend_df.apply(confidence, axis=1)
    trend_df.attrs["pooled_growth_rate"] = pooled_growth
    trend_df.attrs["n_months_available"] = int(monthly["month"].nunique())
    return trend_df


# ---------------------------------------------------------------------
# FORECAST -- project last known month x (1+growth)^steps, then spread
# by the spine. Territories with no usable trend row are skipped rather
# than silently zero-filled.
# ---------------------------------------------------------------------

def forecast_month(nt_df: pd.DataFrame, spine: pd.DataFrame, trend: pd.DataFrame,
                    target_year: int, target_month: int) -> pd.DataFrame:
    trend_i = trend.set_index(COL_TERRITORY_NAME)
    rows = []
    days_in_month = calendar.monthrange(target_year, target_month)[1]

    for terr in nt_df[COL_TERRITORY_NAME].unique():
        if terr not in trend_i.index or trend_i.loc[terr, "last_month"] is None:
            continue

        last_month_p = pd.Period(trend_i.loc[terr, "last_month"], freq="M")
        target_p = pd.Period(f"{target_year}-{target_month:02d}", freq="M")
        steps = (target_p.year - last_month_p.year) * 12 + (target_p.month - last_month_p.month)
        growth = trend_i.loc[terr, "growth_rate"]
        base = trend_i.loc[terr, "last_month_total"]
        projected_total = base * ((1 + growth) ** steps)
        conf = trend_i.loc[terr, "confidence"]

        terr_spine = spine[spine[COL_TERRITORY_NAME] == terr].set_index("dom")["nt_pct"]
        for d in range(1, days_in_month + 1):
            pct = terr_spine.get(d, 0.0)
            rows.append({
                COL_TERRITORY_NAME: terr,
                "date": f"{target_year}-{target_month:02d}-{d:02d}",
                "predicted_nt_qty": round(projected_total * pct / 100, 2),
                "confidence": conf,
                "projected_month_total": round(projected_total, 1),
            })
    return pd.DataFrame(rows)