# ---------------------------------------------------------------------------
# load_oura.R  --  Read the Oura export CSVs into tidy data.tables.
#
# Design per house style: data.table throughout, fread/fwrite for I/O,
# reference semantics (:=), vectorised ops, no setwd()/rm(list=ls()).
# Point LATEST at data_dir/latest (the folder the downloader keeps current).
#
# Oura quirks this handles:
#   * fields are SEMICOLON-separated (sep = ";")
#   * durations are in TOTAL SECONDS (we add *_hours helpers, never overwrite)
#   * a "day" column keys the daily tables; we parse it to Date once
# ---------------------------------------------------------------------------

suppressPackageStartupMessages({
  library(data.table)
})

#' Read every CSV in an Oura export folder into a named list of data.tables.
#'
#' @param latest_dir folder containing the flattened CSVs (…/OuraData/latest).
#' @return named list keyed by file stem, e.g. result$dailysleep.
read_oura <- function(latest_dir) {
  stopifnot(dir.exists(latest_dir))
  files <- list.files(latest_dir, pattern = "\\.csv$", full.names = TRUE)
  if (length(files) == 0L) stop("No CSVs found in ", latest_dir)

  tables <- lapply(files, function(f) {
    # Oura uses ';'. fill = TRUE guards against ragged trailing columns,
    # which Oura's export occasionally emits.
    dt <- fread(f, sep = ";", fill = TRUE, showProgress = FALSE,
                na.strings = c("", "NA", "null"))
    # Parse the daily key once, where present, so joins are type-safe.
    if ("day" %in% names(dt)) dt[, day := as.IDate(day)]
    dt
  })
  names(tables) <- tools::file_path_sans_ext(basename(files))
  tables
}

#' Daily sleep metrics from sleepmodel.csv, which has one row per sleep
#' SESSION (naps included). We keep the `long_sleep` session per day, falling
#' back to the longest session, so naps never masquerade as the night.
sleep_sessions_daily <- function(sm) {
  if (is.null(sm) || !"day" %in% names(sm)) return(NULL)
  sm <- copy(sm)
  # Sort key: preferred session first within each day (long_sleep, then longest).
  sm[, pref := if ("type" %in% names(sm)) as.integer(type == "long_sleep") else 1L]
  if ("total_sleep_duration" %in% names(sm)) {
    sm[, dur := suppressWarnings(as.numeric(total_sleep_duration))]
  } else sm[, dur := 0]
  setorder(sm, day, -pref, -dur, na.last = TRUE)
  daily <- sm[, .SD[1L], by = day]
  keep <- intersect(c("total_sleep_duration", "deep_sleep_duration", "rem_sleep_duration",
                      "light_sleep_duration", "awake_time", "efficiency", "latency",
                      "average_heart_rate", "average_hrv", "lowest_heart_rate",
                      "time_in_bed", "restless_periods"), names(daily))
  daily[, c("day", keep), with = FALSE]
}

#' dailyspo2.csv stores spo2_percentage as JSON like {"average": 96.5}.
#' Pull the number out (no JSON package needed) into spo2_average.
spo2_average <- function(sp) {
  if (is.null(sp) || !"spo2_percentage" %in% names(sp) || is.numeric(sp$spo2_percentage)) return(sp)
  sp <- copy(sp)
  # fread keeps CSV-escaped quotes ({""average"": 97}), hence "* not "?.
  sp[, spo2_average := suppressWarnings(as.numeric(
        sub('.*average"*[[:space:]]*:[[:space:]]*(-?[0-9.]+).*', "\\1", spo2_percentage)))]
  sp[!grepl("average", spo2_percentage, fixed = TRUE), spo2_average := NA_real_]
  sp[, spo2_percentage := NULL]   # the raw JSON string is not analysable
  sp
}

#' Build one daily table by joining the daily-grain files on `day`.
#'
#' Only the files that exist are joined, so this is robust to Oura adding or
#' renaming files. Scores are kept as-is; we add convenience duration columns.
build_daily <- function(tables) {
  # Derived daily tables from the non-daily or JSON-bearing files.
  tables$sleepmodel_daily <- sleep_sessions_daily(tables$sleepmodel)
  tables$dailyspo2 <- spo2_average(tables$dailyspo2)
  # Candidate daily-grain tables, most-useful first. Missing ones are skipped.
  wanted <- c("dailysleep", "sleepmodel_daily", "dailyreadiness", "dailyactivity",
              "dailyspo2", "dailystress", "dailyresilience", "dailycardiovascularage")
  present <- wanted[wanted %in% names(tables)]
  if (length(present) == 0L) stop("No recognised daily tables in the export.")

  # Suffix non-key columns with the source name to avoid collisions on join
  # (e.g. every file has its own `score`).
  prepped <- lapply(present, function(nm) {
    dt <- copy(tables[[nm]])
    if (is.null(dt) || !"day" %in% names(dt)) return(NULL)
    # Drop bookkeeping columns that carry no analytical signal.
    drop <- intersect(c("id", "timestamp", "contributors"), names(dt))
    if (length(drop)) dt[, (drop) := NULL]
    # One row per day: a duplicated day would multiply rows in the joins.
    dt <- unique(dt, by = "day")
    keep <- setdiff(names(dt), "day")
    setnames(dt, keep, paste0(nm, "_", keep))
    dt
  })
  prepped <- Filter(Negate(is.null), prepped)

  # Full outer join across all daily tables on `day`, via chained merges.
  daily <- Reduce(function(x, y) merge(x, y, by = "day", all = TRUE), prepped)
  setkey(daily, day)

  # Add hour versions of any *_seconds / *_duration columns (keep originals).
  sec_cols <- grep("(seconds|duration)$", names(daily), value = TRUE, ignore.case = TRUE)
  for (col in sec_cols) {
    hrs <- sub("(seconds|duration)$", "hours", col, ignore.case = TRUE)
    daily[, (hrs) := as.numeric(get(col)) / 3600]
  }
  daily[]
}

# --- Run as a script: Rscript analysis/load_oura.R [latest_dir] ------------
if (sys.nframe() == 0L) {
  args <- commandArgs(trailingOnly = TRUE)
  latest_dir <- if (length(args) >= 1L) args[[1]] else path.expand("~/OuraData/latest")

  tables <- read_oura(latest_dir)
  message(sprintf("Loaded %d CSV files: %s",
                  length(tables), paste(names(tables), collapse = ", ")))

  daily <- build_daily(tables)
  message(sprintf("Daily table: %d days x %d columns (range %s to %s)",
                  nrow(daily), ncol(daily),
                  as.character(min(daily$day, na.rm = TRUE)),
                  as.character(max(daily$day, na.rm = TRUE))))

  out <- file.path(latest_dir, "daily_merged.csv")
  fwrite(daily, out)
  message("Wrote ", out)
}
