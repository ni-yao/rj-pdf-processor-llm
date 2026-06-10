"""Log Viewer page — shows run_output.log with auto-refresh during pipeline runs."""

from __future__ import annotations

import sys
import streamlit as st
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dashboard.job_state import load_state, is_process_alive

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_FILE = ROOT / "run_output.log"

st.set_page_config(page_title="Log Viewer", layout="wide")
st.title("📜 Pipeline Log Viewer")

st.caption(f"Log file: `{LOG_FILE}`")

# ── Controls ──
state = load_state()
is_running = state.status == "running" and is_process_alive(state.pid)

col1, col2, col3 = st.columns([1, 1, 3])
with col1:
    if st.button("🔄 Refresh", type="primary"):
        st.rerun()

with col2:
    auto_refresh = st.toggle("Auto-refresh", value=is_running, help="Auto-refresh every 3 seconds while the pipeline is running.")

with col3:
    tail_lines = st.number_input(
        "Show last N lines (0 = all)",
        min_value=0,
        max_value=10000,
        value=200,
        step=50,
    )

if is_running:
    st.info("🔄 Pipeline is currently running…")

if not LOG_FILE.exists():
    st.info("No log file found yet. Run the pipeline to generate logs.")
    st.stop()

# Read log content
try:
    text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
except Exception as exc:
    st.error(f"Could not read log file: {exc}")
    st.stop()

lines = text.splitlines()
total = len(lines)
st.caption(f"Total lines: **{total}**")

if tail_lines > 0 and total > tail_lines:
    lines = lines[-tail_lines:]
    st.caption(f"Showing last {tail_lines} lines")

display_text = "\n".join(lines)

st.code(display_text, language="log", line_numbers=True)

# Auto-refresh via timed rerun
if auto_refresh:
    import time
    time.sleep(3)
    st.rerun()
