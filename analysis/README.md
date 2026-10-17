# Analysis layer (R / data.table)

The downloader keeps `…/OuraData/latest/` pointing at the newest export. This
layer turns those CSVs into tidy tables and an EDA report.

## Requirements

R with `data.table` and `ggplot2`:

```r
install.packages(c("data.table", "ggplot2"))
```

## Load the data

```r
source("analysis/load_oura.R")
tables <- read_oura("~/OuraData/latest")   # named list, one data.table per CSV
daily  <- build_daily(tables)              # one row per day, joined on `day`
```

Or from the shell (writes `daily_merged.csv` into the folder):

```bash
Rscript analysis/load_oura.R ~/OuraData/latest
```

## EDA report (EDA first, before any modelling)

```bash
Rscript analysis/eda_report.R ~/OuraData/latest            # -> ~/OuraData/latest/eda/
Rscript analysis/eda_report.R ~/OuraData/latest ./eda_out  # or choose an out dir
```

Produces, in the output folder:

| File | What |
|------|------|
| `REPORT.md` | run log + index of everything produced |
| `numeric_summary.csv` | n, mean, sd, quartiles, IQR, skewness per metric |
| `missingness.csv`, `missingness_over_time.png` | per-column + over-time coverage (ring-not-worn gaps) |
| `normality_shapiro.csv` | Shapiro–Wilk per metric (assumptions stated inside) |
| `outliers.csv` | Tukey-IQR **and** robust MAD-z flags, per day |
| `distributions.png`, `score_trends.png` | histograms; score trends with a loess smoother |
| `correlation_{pearson,spearman}.csv`, `correlation_heatmap.png` | associations |
| `correlation_caveats.txt` | **read this** before trusting any correlation |

The report is **schema-adaptive**: it discovers whatever numeric daily columns
exist rather than hard-coding Oura's field names, so it survives Oura adding or
renaming fields.

### Statistical honesty built in
- Durations are reported in **seconds**; `build_daily()` adds `*_hours` helpers
  alongside (never overwriting) the originals.
- `sleepmodel.csv` has one row per sleep *session* (naps too); `build_daily()`
  keeps the `long_sleep` session per day as `sleepmodel_daily_*` columns
  (duration, HRV, heart rate, efficiency, ...). `dailyspo2`'s JSON cell is
  unpacked into `dailyspo2_spo2_average`.
- Normality is an exploratory flag; the test's i.i.d. assumption is already
  shaky for an autocorrelated daily series.
- Correlations are **exploratory, not confirmatory** — autocorrelation,
  multiple-comparison / p-hacking risk, and mechanically-redundant scores are
  all spelled out in `correlation_caveats.txt`.

## Try it without real data (synthetic fixture)

Before your first real export arrives, you can see exactly what the report looks
like using seeded fake data:

```bash
python tests/fixtures/make_synthetic_export.py /tmp/syn --days 180
Rscript analysis/eda_report.R /tmp/syn /tmp/syn_eda
```

This is clean-room analysis built from the data itself; nothing here
reverse-engineers the Oura app.

## Planned next
Correlations handled with autocorrelation-aware methods, weekday/weekend and
rolling-window breakdowns, and — once you choose — a Shiny or Quarto dashboard
on top of these same tables.
