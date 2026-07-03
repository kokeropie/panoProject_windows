# Kafka-to-Excel Data Pipeline (KEDP) + Transform Stage

[![Build Status](https://github.com/kokeropie/testProject/actions/workflows/ci.yml/badge.svg)](https://github.com/kokeropie/testProject/actions/workflows/ci.yml)

This repo holds two independent pipelines:

1. **KEDP ingest/compile** — consumes JSON messages from a Kafka topic, saves them as individual files, and compiles them into a daily Excel report at 01:00 AM Jakarta time. Covered below.
2. **[Transform stage](#transform-stage)** — takes a raw export and derives 10 business columns via a rule engine, splitting active/void rows into 4 report-ready workbooks, with a Streamlit UI for editing rules and scheduling unattended runs.

```
[ Kafka Broker ]
      │
      ▼  consumer.py  (always-on)
[ Raw_JSON\  *.json files ]
      │
      ▼  compiler.py  (daily 01:00 AM)
[ Daily_Reports\  Compiled_Report_YYYY-MM-DD.xlsx ]
```

---

## Requirements

- Windows 10 Home / Pro
- Python 3.9+
- System timezone set to **(UTC+07:00) Bangkok, Hanoi, Jakarta**

---

## Setup

**1. Install dependencies**

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**2. Create your data folders**

```bat
mkdir C:\KafkaData\Raw_JSON
mkdir C:\KafkaData\Daily_Reports
```

**3. Edit `config.json`**

```json
{
  "kafka": {
    "bootstrap_servers": "192.168.1.50:9092",
    "topic": "update-client",
    "group_id": "kedp-consumer-01",
    "security_protocol": "PLAINTEXT",
    "sasl_mechanism": "",
    "sasl_username": "",
    "sasl_password": ""
  },
  "paths": {
    "json_ingestion_dir": "C:\\KafkaData\\Raw_JSON",
    "excel_output_dir":   "C:\\KafkaData\\Daily_Reports"
  },
  "log": {
    "max_bytes": 5242880,
    "backup_count": 3
  }
}
```

Set `security_protocol` to `SASL_PLAINTEXT` and fill the `sasl_*` fields if your broker requires authentication. Leave them blank for unauthenticated connections.

**4. Register the scheduled tasks** (run each once as Administrator)

```bat
register_consumer_task.bat    # starts consumer.py on every login
register_compiler_task.bat    # runs compiler.py daily at 01:00 AM
```

Both scripts validate that Python and the script are found before registering. Re-running is safe — `/F` overwrites the existing task.

---

## File Structure

```
KEDP\
├── consumer.py                  # Kafka consumer — writes .json files
├── compiler.py                  # Excel compiler — one-shot, run by Task Scheduler
├── utils.py                     # Shared config loading and logging
├── config.json                  # All user settings — edit this, not the scripts
├── pipeline.log                 # Rolling log (max 5 MB × 3 backups)
├── requirements.txt
├── register_consumer_task.bat   # Registers consumer Task Scheduler job
└── register_compiler_task.bat   # Registers compiler Task Scheduler job

C:\KafkaData\
├── Raw_JSON\                    # Incoming .json files land here
│   └── Archive\                 # Processed files moved here after each compile
│       └── Errors\              # Corrupt files that failed to parse
└── Daily_Reports\               # Output .xlsx files
```

---

## How It Works

### consumer.py

Polls the Kafka topic continuously. For each message:

1. Writes the payload to `Raw_JSON\msg_YYYYMMDD_HHmmss_<offset>.json`
2. Commits the Kafka offset only after the file is flushed — no data loss on crash

Starts automatically on login via Task Scheduler (30-second delay for network readiness). Shuts down cleanly on `Ctrl+C` or `SIGTERM`.

### compiler.py

Runs once at 01:00 AM. For each `.json` file in `Raw_JSON\`:

1. Parses the JSON — corrupt files are skipped, logged, and moved to `Errors\`
2. Flattens all messages into a single DataFrame — missing keys become `NaN`
3. Sorts columns alphabetically for consistent layout across days
4. Writes `Daily_Reports\Compiled_Report_YYYY-MM-DD.xlsx`
5. Moves all successfully parsed files to `Archive\`

If the folder is empty at 01:00 AM, the script exits without creating an empty workbook.

---

## Logs

Both scripts append to `pipeline.log` in the project folder.

```
2026-06-23 00:58:11 INFO  [CONSUMER] Connected to 192.168.1.50:9092, topic=update-client, group=kedp-consumer-01
2026-06-23 00:58:12 INFO  [CONSUMER] Written msg_20260623_005812_10040.json (offset=10040)
2026-06-23 01:00:01 INFO  [COMPILER] Started. Found 847 JSON file(s).
2026-06-23 01:00:03 ERROR [COMPILER] Skipped msg_20260622_144301_9981.json — invalid JSON
2026-06-23 01:00:09 INFO  [COMPILER] Done. Files processed: 846, errors: 1, rows: 846, output: Compiled_Report_2026-06-23.xlsx, elapsed: 8.2s
```

The log rotates at 5 MB and keeps 3 backups. Both limits are configurable in `config.json`.

---

## Useful Task Scheduler Commands

```bat
:: Consumer
schtasks /Query  /TN "KEDP_KafkaConsumer" /FO LIST /V
schtasks /Run    /TN "KEDP_KafkaConsumer"
schtasks /End    /TN "KEDP_KafkaConsumer"
schtasks /Delete /TN "KEDP_KafkaConsumer" /F

:: Compiler
schtasks /Query  /TN "KEDP_DailyCompiler" /FO LIST /V
schtasks /Run    /TN "KEDP_DailyCompiler"
schtasks /Delete /TN "KEDP_DailyCompiler" /F
```

---

## Running Manually

```bat
:: Activate venv first
venv\Scripts\activate

:: Start consumer (Ctrl+C to stop)
python consumer.py

:: Run compiler immediately (processes whatever is in Raw_JSON\ right now)
python compiler.py
```

---

## Transform Stage

Takes a raw export (originally a manual, undocumented Qlik process) and derives 10 business columns via a first-match-wins rule engine, splits active/void rows, and produces 4 report-ready workbooks. Has a Streamlit UI for editing the rules, running the transform, and scheduling unattended runs via Windows Task Scheduler.

See `spec.txt` for the full, authoritative spec (8-step derivation, rule file formats, reference row/column counts). `prd.txt` and `process.txt` are historical design notes that predate the rule editor and Schedule feature.

### What it does

```
raw export (.csv/.xls/.xlsx)
      │
      ▼  pipeline.py — Steps 1-6: derive businessUnit, subBusinessUnit,
      │                 productNew, subProduct, Channels, subMarket,
      │                 reportDate, mgmtRpt (first-match-wins rules)
      ▼  Step 7: split into active / void by voidStatus
      ▼  Step 8: union active + void
[ output.xlsx / output_active.xlsx / output_void.xlsx / output_all.xlsx ]
```

Each derived column is decided by conditions stored in `rules/*.json` (canonical DNF), evaluated by `rules_engine.py` — not hardcoded in Python. Edit rules through the Streamlit rule editor, not by hand-editing `rules/*.json` directly.

### Setup

Same venv as KEDP above (`requirements.txt` covers both pipelines):

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Running — UI

```bat
streamlit run app.py
```

Opens a browser tab with one page per derived step (Business Unit, Sub Business Unit, Product, Channel, Market, Report Date, Management Report) plus:

- **Run Pipeline** — upload a CSV/XLS/XLSX (or pick a path via the file browser), run the transform, download the 4 output files
- **Schedule** — configure a recurring `pipeline.py` run (daily/weekly/monthly/annually, start date, optional end date) and generate `register_scheduled_pipeline_task.bat`
- **Config** — edit `rules/config.json` (nonzero-encoding toggle, Step 7 negation columns, etc.)

Rule edits save straight to `rules/*.json`, which `pipeline.py` reads the next time it runs.

### Running — CLI (no UI)

```bat
python pipeline.py <input.xlsx> --outdir output
```

Writes `output.xlsx`, `output_active.xlsx`, `output_void.xlsx`, `output_all.xlsx` to `--outdir` (defaults to `output\`). Every write goes through `write_excel_overwrite()`: an existing file at the target path is deleted before writing, so a re-run always fully replaces stale output. If the target file is open in Excel, the write fails with a clear "close it and try again" error instead of silently failing.

### Scheduling unattended runs

The Schedule page in `app.py` writes `schedule_config.json` and generates `register_scheduled_pipeline_task.bat` next to `pipeline.py`. Streamlit has no background job runner, so activating the schedule still requires manually running that `.bat` as Administrator on the Windows host — same pattern as `register_consumer_task.bat` / `register_compiler_task.bat` above:

```bat
register_scheduled_pipeline_task.bat
```

Query/run/remove the task the same way as the KEDP tasks (task name `KEDP_ScheduledPipeline`):

```bat
schtasks /Query  /TN "KEDP_ScheduledPipeline" /FO LIST /V
schtasks /Run    /TN "KEDP_ScheduledPipeline"
schtasks /Delete /TN "KEDP_ScheduledPipeline" /F
```

### File Structure

```
pipeline.py           Transform stage: 8-step derivation + CLI entry point
rules_engine.py       Shared rule model (canonical DNF) + evaluator
rule_importer.py      One-time parser: dataFilter/*.txt -> rules/*.json
                      (re-running it overwrites rules/*.json, discarding
                      any edits made through the app)
app.py                Streamlit UI: rule editor, Run Pipeline, Schedule, Config
scheduler.py          Schedule config I/O + schtasks/.bat generation
path_picker.py        Cross-platform file/folder browser widget
build_mgmt_report.py  One-off script: adds mgmtRpt to an existing output_all.xlsx
rules/*.json          Canonical rule storage pipeline.py reads at run time
```
