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

#' Build one daily table by joining the daily-grain files on `day`.
#'
#' Only the files that exist are joined, so this is robust to Oura adding or
#' renaming files. Scores are kept as-is; we add convenience duration columns.
build_daily <- function(tables) {
  # Candidate daily-grain tables, most-useful first. Missing ones are skipped.
  wanted <- c("dailysleep", "dailyreadiness", "dailyactivity",
              "dailyspo2", "dailystress", "dailyresilience")
  present <- wanted[wanted %in% names(tables)]
  if (length(present) == 0L) stop("No recognised daily tables in the export.")

  # Suffix non-key columns with the source name to avoid collisions on join
  # (e.g. every file has its own `score`).
  prepped <- lapply(present, function(nm) {
    dt <- copy(tables[[nm]])
    if (!"day" %in% names(dt)) return(NULL)
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
