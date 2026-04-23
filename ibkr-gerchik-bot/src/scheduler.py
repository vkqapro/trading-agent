"""Helpers for wiring the bot into Windows Task Scheduler."""

from __future__ import annotations

from pathlib import Path
from typing import Dict


JOB_MODULES: Dict[str, str] = {
    "premarket": "src.main --job premarket",
    "open": "src.main --job open",
    "intraday": "src.main --job intraday",
    "eod": "src.main --job eod",
    "weekly": "src.main --job weekly",
}


def build_task_scheduler_command(project_root: Path, python_executable: str, job_name: str) -> str:
    """Return a Task Scheduler command line for a specific job."""
    if job_name not in JOB_MODULES:
        raise ValueError(f"Unknown job '{job_name}'. Valid jobs: {', '.join(JOB_MODULES)}")
    module_args = JOB_MODULES[job_name]
    return f'"{python_executable}" -m {module_args}'


def get_recommended_task_names() -> Dict[str, str]:
    """Map jobs to recommended Task Scheduler task names."""
    return {job: f"IBKR Gerchik Bot - {job.title()}" for job in JOB_MODULES}
