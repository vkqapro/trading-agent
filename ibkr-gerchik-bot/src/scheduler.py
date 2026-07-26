"""Helpers for wiring the bot into Windows Task Scheduler."""

from __future__ import annotations

from pathlib import Path
from typing import Dict


JOB_MODULES: Dict[str, str] = {
    "premarket": "src.main --job premarket --client-id 31",
    "open": "src.main --job open --client-id 41",
    "intraday": "src.main --job intraday --client-id 21",
    "market_data": "src.main --job market_data --client-id 17",
    "irs_scan": "src.main --job irs_scan --irs-mode FIFTEEN_MIN_CONFIRMATION_SCAN --client-id 71",
    "eod": "src.main --job eod --client-id 51",
    "weekly": "src.main --job weekly --client-id 61",
}


def build_task_scheduler_command(
    project_root: Path,
    python_executable: str,
    job_name: str,
    dry_run: bool = False,
) -> str:
    """Return a Task Scheduler command line for a specific job."""
    if job_name not in JOB_MODULES:
        raise ValueError(f"Unknown job '{job_name}'. Valid jobs: {', '.join(JOB_MODULES)}")
    module_args = JOB_MODULES[job_name]
    dry_run_arg = " --dry-run" if dry_run else ""
    return f'"{python_executable}" -m {module_args}{dry_run_arg}'


def build_all_task_scheduler_commands(project_root: Path, python_executable: str, dry_run: bool = False) -> Dict[str, str]:
    """Return concrete Task Scheduler commands for every job."""
    return {
        job: build_task_scheduler_command(project_root, python_executable, job, dry_run=dry_run)
        for job in JOB_MODULES
    }


def get_recommended_task_names() -> Dict[str, str]:
    """Map jobs to recommended Task Scheduler task names."""
    return {job: f"IBKR Gerchik Bot - {job.title()}" for job in JOB_MODULES}
