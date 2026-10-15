# ---------------------------------------------------------------------------
# eda_report.R  --  Exploratory Data Analysis of the Oura export.
#
# EDA FIRST, by design: this characterises the data (coverage, missingness,
# distributions, outliers, trends, associations) BEFORE any modelling. It fits
# no models and makes no causal claims.
#
# House style: data.table for all preparation (fread/fwrite, :=, chaining,
# vectorised), ggplot2 only for rendering prepared tables.
#
# It is schema-ADAPTIVE: it discovers whatever numeric daily columns exist
# rather than hard-coding Oura's column names, so it keeps working if Oura adds
# or renames fields (NO ASSUMPTIONS about the exact schema).
#
# Usage:
#   Rscript analysis/eda_report.R <latest_dir> [out_dir]
#   # default out_dir = <latest_dir>/eda
# ---------------------------------------------------------------------------

suppressPackageStartupMessages({
  library(data.table)
  library(ggplot2)
})

# load_oura.R lives beside this file; source it whether run from repo root or here.
local({
  here <- tryCatch(dirname(sys.frame(1)$ofile), error = function(e) NA_character_)
  cand <- c(if (!is.na(here)) file.path(here, "load_oura.R"),
            "analysis/load_oura.R", "load_oura.R")
  src <- cand[file.exists(cand)][1]
  if (is.na(src)) stop("Cannot locate load_oura.R")
  sys.source(src, envir = topenv())
})

# --- small helpers ---------------------------------------------------------

#' Population skewness (no extra package). Returns NA for n<3 or zero variance.
.skewness <- function(x) {
  x <- x[is.finite(x)]
  n <- length(x)
  if (n < 3L) return(NA_real_)
  m <- mean(x); s <- sqrt(mean((x - m)^2))
  if (s == 0) return(NA_real_)
  mean(((x - m) / s)^3)
}

#' Numeric metric columns in the daily table (exclude the key, keep only
#' columns that are genuinely numeric and not constant).
.numeric_metrics <- function(daily) {
  cols <- setdiff(names(daily), "day")
  cols <- cols[vapply(daily[, ..cols], is.numeric, logical(1))]
  # Coerce integer metrics to double in place, so later melt() calls don't warn
  # about mixing integer and double measure.vars (purely cosmetic otherwise).
  int_cols <- cols[vapply(daily[, ..cols], is.integer, logical(1))]
  if (length(int_cols)) daily[, (int_cols) := lapply(.SD, as.numeric), .SDcols = int_cols]
  # Drop all-NA or constant columns: they carry no EDA signal and break cor().
  keep <- vapply(cols, function(c) {
    v <- daily[[c]]; sum(is.finite(v)) > 0 && data.table::uniqueN(v[is.finite(v)]) > 1
  }, logical(1))
  cols[keep]
}

# --- report sections -------------------------------------------------------

.section_coverage <- function(daily, log) {
  rng <- range(daily$day, na.rm = TRUE)
  n_obs <- nrow(daily)
  n_cal <- as.integer(diff(rng)) + 1L           # calendar span in days
  gaps  <- n_cal - n_obs                         # days with no row at all
  log(sprintf("Coverage: %d observed days spanning %s to %s (%d calendar days; %d missing days / %.1f%%).",
              n_obs, rng[1], rng[2], n_cal, gaps, 100 * gaps / n_cal))
  data.table(metric = c("observed_days", "calendar_days", "missing_days"),
             value  = c(n_obs, n_cal, gaps))
}

.section_missingness <- function(daily, metrics, out_dir, log) {
  # Per-column missingness on the observed rows.
  miss <- rbindlist(lapply(metrics, function(c) {
    v <- daily[[c]]
    data.table(column = c, n = length(v),
               n_missing = sum(!is.finite(v)),
               pct_missing = round(100 * mean(!is.finite(v)), 2))
  }))[order(-pct_missing)]
  fwrite(miss, file.path(out_dir, "missingness.csv"))
  log(sprintf("Missingness: worst column is %s at %.1f%% missing.",
              miss$column[1], miss$pct_missing[1]))

  # Missingness over time (reveals ring-not-worn gaps). Prep long in data.table.
  long <- melt(daily[, c("day", metrics), with = FALSE], id.vars = "day",
               variable.name = "metric", value.name = "val")
  long[, missing := !is.finite(val)]
  p <- ggplot(long, aes(day, metric, fill = missing)) +
    geom_tile() +
    scale_fill_manual(values = c(`FALSE` = "grey85", `TRUE` = "#c0392b")) +
    labs(title = "Missingness over time", x = NULL, y = NULL, fill = "missing") +
    theme_minimal(base_size = 11)
  ggsave(file.path(out_dir, "missingness_over_time.png"), p, width = 10, height = 4, dpi = 120)
  miss
}

.section_summaries <- function(daily, metrics, out_dir, log) {
  summ <- rbindlist(lapply(metrics, function(c) {
    v <- daily[[c]]; v <- v[is.finite(v)]
    data.table(column = c, n = length(v),
               mean = mean(v), sd = sd(v),
               min = min(v), q25 = quantile(v, .25), median = median(v),
               q75 = quantile(v, .75), max = max(v),
               IQR = IQR(v), skewness = .skewness(v))
  }))
  num <- names(summ)[sapply(summ, is.numeric)]
  summ[, (num) := lapply(.SD, round, 3), .SDcols = num]
  fwrite(summ, file.path(out_dir, "numeric_summary.csv"))
  log(sprintf("Summaries: %d numeric metrics profiled -> numeric_summary.csv", length(metrics)))

  # Faceted histograms. One panel per metric, free scales.
  long <- melt(daily[, c("day", metrics), with = FALSE], id.vars = "day",
               variable.name = "metric", value.name = "val")[is.finite(val)]
  p <- ggplot(long, aes(val)) +
    geom_histogram(bins = 30, fill = "#2c7fb8", colour = "white", linewidth = 0.1) +
    facet_wrap(~ metric, scales = "free") +
    labs(title = "Distributions", x = NULL, y = "count") +
    theme_minimal(base_size = 10)
  ggsave(file.path(out_dir, "distributions.png"), p, width = 11, height = 7, dpi = 120)
  summ
}

.section_normality <- function(daily, metrics, out_dir, log) {
  # Shapiro-Wilk. ASSUMPTIONS/CAVEATS stated for the reader:
  #  - H0 = "sample is from a normal distribution"; small p => reject normality.
  #  - Valid for 3 <= n <= 5000; we skip columns outside that range.
  #  - With daily wearable data, points are an AUTOCORRELATED time series, so the
  #    i.i.d. assumption behind the test is already questionable; treat the result
  #    as a rough flag, and prefer the Q-Q/histograms for judgement.
  res <- rbindlist(lapply(metrics, function(c) {
    v <- daily[[c]]; v <- v[is.finite(v)]
    if (length(v) < 3L || length(v) > 5000L)
      return(data.table(column = c, n = length(v), W = NA_real_, p_value = NA_real_,
                        normal_at_0.05 = NA))
    t <- shapiro.test(v)
    data.table(column = c, n = length(v), W = round(unname(t$statistic), 4),
               p_value = signif(t$p.value, 4),
               normal_at_0.05 = t$p.value >= 0.05)
  }))
  fwrite(res, file.path(out_dir, "normality_shapiro.csv"))
  n_non <- sum(res$normal_at_0.05 == FALSE, na.rm = TRUE)
  log(sprintf("Normality: %d/%d metrics reject normality at 0.05 (exploratory flag only).",
              n_non, nrow(res)))
  res
}

.section_outliers <- function(daily, metrics, out_dir, log) {
  # Two complementary rules, both robust-ish and non-parametric:
  #  - Tukey IQR fence (1.5*IQR): classic, distribution-light.
  #  - Modified z via MAD (>3.5): robust to the very outliers we hunt.
  out <- rbindlist(lapply(metrics, function(c) {
    dt <- daily[is.finite(get(c)), .(day, val = get(c))]
    q <- quantile(dt$val, c(.25, .75)); iqr <- q[2] - q[1]
    lo <- q[1] - 1.5 * iqr; hi <- q[2] + 1.5 * iqr
    med <- median(dt$val); mad <- stats::mad(dt$val, constant = 1.4826)
    dt[, `:=`(column = c,
              iqr_outlier = val < lo | val > hi,
              mad_z = if (mad > 0) 0.6745 * (val - med) / mad else 0)]
    dt[iqr_outlier | abs(mad_z) > 3.5,
       .(column, day, value = val, iqr_outlier, mad_z = round(mad_z, 2))]
  }))
  fwrite(out, file.path(out_dir, "outliers.csv"))
  log(sprintf("Outliers: %d flagged across all metrics -> outliers.csv", nrow(out)))
  out
}

.section_trends <- function(daily, metrics, out_dir, log) {
  # Trend lines for score-like metrics (bounded 0-100) plus a smoother.
  score_cols <- metrics[grepl("score", metrics)]
  if (length(score_cols) == 0L) score_cols <- head(metrics, 3L)
  long <- melt(daily[, c("day", score_cols), with = FALSE], id.vars = "day",
               variable.name = "metric", value.name = "val")[is.finite(val)]
  p <- ggplot(long, aes(day, val, colour = metric)) +
    geom_line(alpha = 0.35, linewidth = 0.3) +
    geom_smooth(se = FALSE, method = "loess", formula = y ~ x, span = 0.4, linewidth = 0.8) +
    labs(title = "Score trends over time", x = NULL, y = "score", colour = NULL) +
    theme_minimal(base_size = 11)
  ggsave(file.path(out_dir, "score_trends.png"), p, width = 10, height = 5, dpi = 120)
  log(sprintf("Trends: plotted %d score series -> score_trends.png", length(score_cols)))
  invisible(NULL)
}

.section_correlations <- function(daily, metrics, out_dir, log) {
  # Pairwise-complete Pearson AND Spearman on numeric metrics.
  M <- as.matrix(daily[, ..metrics])
  pear <- cor(M, use = "pairwise.complete.obs", method = "pearson")
  spear <- cor(M, use = "pairwise.complete.obs", method = "spearman")
  fwrite(as.data.table(round(pear, 3), keep.rownames = "metric"),
         file.path(out_dir, "correlation_pearson.csv"))
  fwrite(as.data.table(round(spear, 3), keep.rownames = "metric"),
         file.path(out_dir, "correlation_spearman.csv"))

  # Heatmap of Spearman (monotonic, less sensitive to the outliers above).
  long <- as.data.table(spear, keep.rownames = "x")
  long <- melt(long, id.vars = "x", variable.name = "y", value.name = "rho")
  p <- ggplot(long, aes(x, y, fill = rho)) +
    geom_tile() +
    geom_text(aes(label = sprintf("%.2f", rho)), size = 2.6) +
    scale_fill_gradient2(limits = c(-1, 1), low = "#b2182b", mid = "white", high = "#2166ac") +
    labs(title = "Spearman correlation (exploratory)", x = NULL, y = NULL) +
    theme_minimal(base_size = 9) +
    theme(axis.text.x = element_text(angle = 45, hjust = 1))
  ggsave(file.path(out_dir, "correlation_heatmap.png"), p, width = 8, height = 7, dpi = 120)

  # CAVEATS the reader must carry forward (printed and written):
  caveats <- c(
    "Correlations are EXPLORATORY, not confirmatory. Three reasons to distrust naive p-values here:",
    "1. Autocorrelation: daily metrics form a time series; successive days are not independent,",
    "   so classical correlation p-values/CIs understate uncertainty. Use block/HAC methods before inferring.",
    "2. Multiple comparisons: scanning every metric pair invites p-hacking. Any 'significant' pair found",
    "   this way needs pre-registration or a correction (e.g. Benjamini-Hochberg) and out-of-sample confirmation.",
    "3. Redundancy: several Oura 'scores' are deterministic functions of the same raw inputs, so high",
    "   correlation between a score and its components is mechanical, not a finding.")
  writeLines(caveats, file.path(out_dir, "correlation_caveats.txt"))
  log("Correlations: wrote Pearson+Spearman matrices, heatmap, and caveats.")
  invisible(NULL)
}

# --- orchestrator ----------------------------------------------------------

eda_report <- function(latest_dir, out_dir = file.path(latest_dir, "eda")) {
  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  log_lines <- character(0)
  log <- function(msg) { message(msg); log_lines[[length(log_lines) + 1L]] <<- msg }

  tables <- read_oura(latest_dir)
  daily  <- build_daily(tables)
  metrics <- .numeric_metrics(daily)
  if (length(metrics) == 0L) stop("No numeric metrics found to analyse.")

  log(sprintf("Oura EDA — %s", format(Sys.time(), "%Y-%m-%d %H:%M")))
  .section_coverage(daily, log)
  .section_missingness(daily, metrics, out_dir, log)
  .section_summaries(daily, metrics, out_dir, log)
  .section_normality(daily, metrics, out_dir, log)
  .section_outliers(daily, metrics, out_dir, log)
  .section_trends(daily, metrics, out_dir, log)
  .section_correlations(daily, metrics, out_dir, log)

  # A readable index of what was produced.
  writeLines(c("# Oura EDA report", "",
               paste0("- generated: ", format(Sys.time())),
               paste0("- source: ", latest_dir),
               paste0("- metrics analysed: ", paste(metrics, collapse = ", ")),
               "", "## Log", paste0("- ", log_lines),
               "", "## Files",
               "- numeric_summary.csv, missingness.csv, normality_shapiro.csv, outliers.csv",
               "- correlation_pearson.csv, correlation_spearman.csv, correlation_caveats.txt",
               "- distributions.png, missingness_over_time.png, score_trends.png, correlation_heatmap.png"),
             file.path(out_dir, "REPORT.md"))
  message("\nWrote EDA outputs to ", out_dir)
  invisible(out_dir)
}

if (sys.nframe() == 0L) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) < 1L) stop("Usage: Rscript analysis/eda_report.R <latest_dir> [out_dir]")
  out <- if (length(args) >= 2L) args[[2]] else file.path(args[[1]], "eda")
  eda_report(args[[1]], out)
}
