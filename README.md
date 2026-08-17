# Cement Logistics Dashboard -- Setup & Status

## Setup (do this once, on your own machine)

1. Install a code editor -- VS Code recommended.
2. Create a virtual environment inside this folder:
   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # Mac/Linux
   pip install -r requirements.txt
   ```
3. Put `base_tdc_file_jan-july.xlsx` inside `data/` (already there if you unzipped this as-is).
4. Run the app:
   ```bash
   streamlit run app.py
   ```
   Opens at `localhost:8501`, live-reloads on save.

Keep using Jupyter/a notebook to prototype any *new* logic first (e.g. testing
a new aggregation, checking a formula against a handful of rows) -- once it
works, move it into `src/data_prep.py` or `src/forecasting.py` as a proper
function, same as everything currently in those two files.

## What's actually working right now

- `src/data_prep.py`: loads the base TDC file, applies the confirmed filter
  funnel (Road only -> Trade only -> Grade U1/U2 only), and aggregates to
  daily volume by Region/Branch/Territory. **Verified against the real file**:
  264,508 raw rows -> 96,819 rows / 1,393,442 qty after Road+TR+U1/U2.
- `app.py`: working Streamlit dashboard -- upload the file, see the funnel
  table, see daily volume charts (total and per-region).

## What's intentionally NOT done yet

1. **Truck-type scope is unresolved.** Filtering further to `Truck Type Clean`
   in `{T1,T2,T3}` drops to just 17,436 rows / 337,624 qty -- a 76% volume
   drop from the Road+TR+U1/U2 base, because most of that volume is tagged
   `DEPOT`/`MKT`/`DIRDE`/`DIV` instead. The app has a sidebar toggle
   (`None` / `strict` / `broad`) so you can see all three views, but **do not
   present a truck-count number to the board until this is confirmed** with
   your senior -- whether DEPOT/MKT/DIRDE/DIV volume needs truck-sizing too,
   or is genuinely a separate channel.
2. **August DD targets not wired in.** `src/forecasting.py` has the
   day-of-month / day-of-week pattern functions and a
   `distribute_monthly_target_to_days()` function ready to take the Aug-26
   region target from `Final DD targets Region-wise` once you're ready to
   bring that in -- per our chat, that's deliberately deferred for now.
3. **Truck-count-from-distance** isn't built yet -- that's the step after
   demand forecasting is finalized (needs distance, already available in this
   file as `Distance from plant`, plus a turnaround-time assumption and the
   no-entry time-slot windows, which are still outstanding).
