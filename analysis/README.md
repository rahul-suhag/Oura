# Analysis layer (R / data.table)

The downloader keeps `…/OuraData/latest/` pointing at the newest export. This
folder turns those CSVs into tidy tables for analysis.

```r
source("analysis/load_oura.R")

tables <- read_oura("~/OuraData/latest")   # named list, one data.table per CSV
daily  <- build_daily(tables)              # one row per day, joined on `day`
```

Or from the shell:

```bash
Rscript analysis/load_oura.R ~/OuraData/latest
# writes ~/OuraData/latest/daily_merged.csv
```

Notes
- Oura CSVs are **semicolon**-separated; `read_oura()` handles that.
- Durations are in **total seconds**; `build_daily()` adds `*_hours` columns
  alongside (never overwriting) the originals.
- This is a clean-room layer built from the data itself. Oura's own scores are
  read straight from the export; nothing here reverse-engineers the Oura app.

Planned next (ask Claude to build): EDA report (missingness, distributions,
outliers), trend charts with `ggplot2`, and correlations between sleep,
readiness and activity — all prepared with `data.table` before plotting.
```
