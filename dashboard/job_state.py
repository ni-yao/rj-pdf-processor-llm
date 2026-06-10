"""Lightweight job-state manager for the dashboard.

Persists a single JSON file (``dashboard/.job_state.json``) so that job
status survives Streamlit reruns, page switches, and server restarts.
Only one pipeline run is allowed at a time (single-run lock).
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

_STATE_FILE = Path(__file__).resolve().parent / ".job_state.json"


@dataclass
class JobState:
    status: str = "idle"  # idle | running | completed | failed
    pid: Optional[int] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    exit_code: Optional[int] = None
    log_path: Optional[str] = None
    uploaded_files: list[str] = field(default_factory=list)
    error_message: Optional[str] = None


def load_state() -> JobState:
    """Load job state from disk, or return a fresh idle state."""
    if _STATE_FILE.exists():
        try:
            raw = json.loads(_STATE_FILE.read_text())
            return JobState(**{k: v for k, v in raw.items() if k in JobState.__dataclass_fields__})
        except Exception:
            return JobState()
    return JobState()


def save_state(state: JobState) -> None:
    """Persist job state to disk."""
    _STATE_FILE.write_text(json.dumps(asdict(state), indent=2))


def clear_state() -> None:
    """Reset to idle."""
    save_state(JobState())


def is_process_alive(pid: int | None) -> bool:
    """Check whether a process with *pid* is still running."""
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            # On Windows, os.kill(pid, 0) is unreliable.
            # Use ctypes to call OpenProcess and check if it exists.
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, SystemError):
        return False
