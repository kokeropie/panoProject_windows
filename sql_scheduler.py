"""
Config + Windows Task Scheduler (schtasks) plumbing for running sql_import.py
on a recurring schedule, chosen from the Streamlit "Schedule SQL Import" page
(app.py). Mirrors scheduler.py's pattern for pipeline.py exactly, as a
separate, additive module — scheduler.py itself is untouched; shared pieces
(recurrence choices, weekday/month codes, the /SC flag builder, the
mm/dd/yyyy formatter) are imported from it rather than duplicated.

This module only builds config/commands — it never calls schtasks itself.
Registering the task still requires running the generated .bat as
Administrator, same as the other register_*.bat scripts in this repo.

Security note: schtasks command lines are visible to any local user via
`schtasks /Query /TN ... /FO LIST /V` and in the Task Scheduler UI, so a SQL
auth password must never be embedded in one. Unattended/scheduled SQL
imports should use Windows Authentication (the default) instead, where the
task simply runs as a service/user account SQL Server already trusts. If SQL
auth is genuinely required for a schedule, set MSSQL_PASSWORD as a *system*
environment variable yourself (`setx MSSQL_PASSWORD ... /M`, as Administrator)
before registering — this module refuses to write a password into the
generated .bat.

Usage:
    from sql_scheduler import load_sql_schedule_config, save_sql_schedule_config, \
        format_sql_schtasks_command, render_sql_register_bat
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scheduler import (
    MONTHS,
    RECURRENCE_CHOICES,
    WEEKDAYS,
    _mmddyyyy,
    _recurrence_flag_lines,
    validate_schedule_config,
)
from sql_import import SQL_CONFIG_PATH, load_sql_config, validate_sql_config

PROJECT_DIR = Path(__file__).parent
SQL_SCHEDULE_CONFIG_PATH = PROJECT_DIR / "sql_schedule_config.json"
REGISTER_SQL_BAT_PATH = PROJECT_DIR / "register_scheduled_sql_import_task.bat"
SQL_IMPORT_SCRIPT = PROJECT_DIR / "sql_import.py"

TASK_NAME = "KEDP_ScheduledSQLImport"


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def default_sql_schedule_config() -> dict:
    return {
        "recurrence": "Daily",
        "interval": 1,
        "weekdays": ["Monday"],
        "day_of_month": 1,
        "month": "January",
        "time": "02:00",
        "start_date": date.today().isoformat(),
        "end_date": None,
        "xlsx_path": "output/output_all.xlsx",
        "sql_config_path": str(SQL_CONFIG_PATH.name),
    }


def load_sql_schedule_config() -> dict:
    if not SQL_SCHEDULE_CONFIG_PATH.exists():
        return default_sql_schedule_config()
    return {**default_sql_schedule_config(), **json.loads(SQL_SCHEDULE_CONFIG_PATH.read_text())}


def save_sql_schedule_config(config: dict) -> None:
    SQL_SCHEDULE_CONFIG_PATH.write_text(json.dumps(config, indent=2))


def delete_sql_schedule_config() -> None:
    if SQL_SCHEDULE_CONFIG_PATH.exists():
        SQL_SCHEDULE_CONFIG_PATH.unlink()


# ---------------------------------------------------------------------------
# Config -> schtasks
# ---------------------------------------------------------------------------

def validate_sql_schedule_config(config: dict) -> list[str]:
    """Returns a list of human-readable problems; empty means OK to save/register."""
    errors = validate_schedule_config(config)
    if not config.get("xlsx_path"):
        errors.append("An input .xlsx file is required.")
    sql_config_path = Path(config.get("sql_config_path") or SQL_CONFIG_PATH)
    if not sql_config_path.exists():
        errors.append(f"{sql_config_path} doesn't exist yet — set up the SQL "
                       f"connection on the Import to SQL Server page first.")
    else:
        sql_config = {**load_sql_config(), **json.loads(sql_config_path.read_text())}
        sql_errors = validate_sql_config(sql_config)
        if sql_errors:
            errors.append("SQL connection config is incomplete: " + "; ".join(sql_errors))
        if sql_config.get("auth") == "sql":
            errors.append(
                "SQL Authentication can't be scheduled safely from here — a "
                "password can't be embedded in a Task Scheduler command line. "
                "Switch the connection to Windows Authentication, or set "
                "MSSQL_PASSWORD as a system environment variable yourself "
                "before registering (see this module's docstring)."
            )
    return errors


def _sql_schtasks_flag_lines(config: dict, python_exe: str, sql_import_script: str,
                              task_name: str = TASK_NAME) -> list[str]:
    errors = validate_sql_schedule_config(config)
    if errors:
        raise ValueError("invalid SQL schedule config: " + "; ".join(errors))

    tr_value = (f'"\\"{python_exe}\\" \\"{sql_import_script}\\" '
                f'\\"{config["xlsx_path"]}\\" --config \\"{config["sql_config_path"]}\\""')

    lines = [f'/TN "{task_name}"', f"/TR {tr_value}"]
    lines += _recurrence_flag_lines(config)
    lines += [f'/ST {config["time"]}', f'/SD {_mmddyyyy(config["start_date"])}']
    if config.get("end_date"):
        lines.append(f'/ED {_mmddyyyy(config["end_date"])}')
    lines += ["/RL HIGHEST", "/F"]
    return lines


def format_sql_schtasks_command(config: dict, python_exe: str = "python",
                                 sql_import_script: Path = SQL_IMPORT_SCRIPT,
                                 task_name: str = TASK_NAME) -> str:
    """Single-line schtasks /Create command, for read-only display in the UI."""
    lines = _sql_schtasks_flag_lines(config, python_exe, str(sql_import_script), task_name)
    return "schtasks /Create " + " ".join(lines)


def summarize_sql_schedule(config: dict) -> str:
    recurrence = config["recurrence"]
    if recurrence == "Daily":
        summary = f"every {config.get('interval', 1)} day(s) at {config['time']}"
    elif recurrence == "Weekly":
        days = ", ".join(config["weekdays"])
        summary = f"every {config.get('interval', 1)} week(s) on {days} at {config['time']}"
    elif recurrence == "Monthly":
        summary = f"monthly on day {config['day_of_month']} at {config['time']}"
    else:
        summary = f"annually on {config['month']} {config['day_of_month']} at {config['time']}"
    end_note = f", ending {config['end_date']}" if config.get("end_date") else ", no end date"
    return summary + end_note


# ---------------------------------------------------------------------------
# .bat generation — same admin-check / python-detect / schtasks shape as
# register_compiler_task.bat / register_consumer_task.bat / the pipeline's
# register_scheduled_pipeline_task.bat (scheduler.py's render_register_bat).
# ---------------------------------------------------------------------------

def render_sql_register_bat(config: dict, task_name: str = TASK_NAME) -> str:
    schtasks_lines = _sql_schtasks_flag_lines(config, "%PYTHON%", "%SQLIMPORT%", "%TASK_NAME%")
    schtasks_block = "schtasks /Create ^\n    " + " ^\n    ".join(schtasks_lines)
    summary = summarize_sql_schedule(config)

    return f"""@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  KEDP - Scheduled SQL Import Task Registration
::  Generated by app.py's Schedule SQL Import page from
::  sql_schedule_config.json. Run once as Administrator:
::    {summary}
::  Safe to re-run - /F overwrites the existing task.
:: ============================================================

:: -- Admin check ------------------------------------------------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script must be run as Administrator.
    echo.
    echo Right-click {REGISTER_SQL_BAT_PATH.name} ^> "Run as administrator"
    pause
    exit /b 1
)

:: -- Resolve sql_import.py path -----------------------------------
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "SQLIMPORT=%SCRIPT_DIR%\\sql_import.py"

if not exist "%SQLIMPORT%" (
    echo [ERROR] sql_import.py not found at: %SQLIMPORT%
    pause
    exit /b 1
)

:: -- Detect Python ------------------------------------------------
for /f "usebackq delims=" %%i in (`where python 2^>nul`) do (
    set "PYTHON=%%i"
    goto :python_found
)
echo [ERROR] Python not found in PATH.
echo Install Python and ensure it is added to PATH, then re-run.
pause
exit /b 1

:python_found

:: -- Timezone reminder ---------------------------------------------
echo.
echo [INFO] Task Scheduler uses the system clock with no timezone conversion.
echo        Confirm your system timezone before continuing:
echo.
tzutil /g
echo.
echo        Expected: (UTC+07:00) Bangkok, Hanoi, Jakarta
echo        If wrong, fix it in Settings ^> Time ^& Language before proceeding.
echo.
echo [INFO] This schedule assumes Windows Authentication to SQL Server.
echo        If the connection uses SQL auth instead, MSSQL_PASSWORD must
echo        already be set as a SYSTEM environment variable (setx /M) -
echo        it is never embedded in this script or the scheduled task.
echo.
set /p "CONFIRM=Continue with registration? (Y/N): "
if /i not "%CONFIRM%"=="Y" (
    echo Aborted.
    pause
    exit /b 0
)

:: -- Register Task Scheduler job ------------------------------------
set "TASK_NAME={task_name}"

{schtasks_block}

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] schtasks failed. See message above for details.
    pause
    exit /b 1
)

:: -- Confirm ----------------------------------------------------------
echo.
echo [OK] Task "%TASK_NAME%" registered.
echo      Trigger    : {summary}
echo      Script     : %SQLIMPORT%
echo      Python     : %PYTHON%
echo      Input xlsx : {config['xlsx_path']}
echo      SQL config : {config['sql_config_path']}
echo.
echo Useful commands:
echo   Query  : schtasks /Query /TN "%TASK_NAME%" /FO LIST /V
echo   Run now: schtasks /Run   /TN "%TASK_NAME%"
echo   Remove : schtasks /Delete /TN "%TASK_NAME%" /F
echo.
pause
endlocal
"""


def write_sql_register_bat(config: dict, task_name: str = TASK_NAME) -> Path:
    REGISTER_SQL_BAT_PATH.write_text(render_sql_register_bat(config, task_name))
    return REGISTER_SQL_BAT_PATH


__all__ = [
    "RECURRENCE_CHOICES", "WEEKDAYS", "MONTHS",
    "default_sql_schedule_config", "load_sql_schedule_config", "save_sql_schedule_config",
    "delete_sql_schedule_config", "validate_sql_schedule_config", "format_sql_schtasks_command",
    "summarize_sql_schedule", "render_sql_register_bat", "write_sql_register_bat",
    "REGISTER_SQL_BAT_PATH", "SQL_SCHEDULE_CONFIG_PATH",
]
