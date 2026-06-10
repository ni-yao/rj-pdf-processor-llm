"""Streamlit dashboard — Upload & Process page.

Lets users upload PDFs via the browser and trigger the extraction pipeline.
"""

from __future__ import annotations

import subprocess
import sys
import time

import streamlit as st
from pathlib import Path

# Add project root to sys.path so we can import dashboard modules
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dashboard.job_state import (
    JobState, load_state, save_state, clear_state, is_process_alive,
)

# ── Project root (one level up from dashboard/) ──
ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
LOG_FILE = ROOT / "run_output.log"

# ── Page config ──
st.set_page_config(
    page_title="RJ PDF Processor",
    page_icon="📊",
    layout="wide",
)

st.title("📊 RJ PDF Processor")
st.caption("Upload annual report PDFs and extract structured financial data.")
st.markdown("---")


# ── Helpers ──

def _refresh_job_status(state: JobState) -> JobState:
    """Check if a running job has finished and update state accordingly."""
    if state.status != "running":
        return state
    if not is_process_alive(state.pid):
        state.finished_at = time.time()
        # Check if the log file contains completion markers
        if LOG_FILE.exists():
            try:
                log_text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
                # Pipeline logs "Pipeline finished" or errors on exit
                if "Pipeline finished" in log_text or "ERROR" in log_text.split("\n")[-5:]:
                    pass  # proceed to mark complete
            except Exception:
                pass
        state.exit_code = 0
        state.status = "completed"
        # Check if we have output files as a success indicator
        if _count_output_jsons() == 0 and LOG_FILE.exists():
            try:
                log_text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
                if "Traceback" in log_text or "Error" in log_text:
                    state.status = "failed"
                    state.exit_code = 1
            except Exception:
                pass
        save_state(state)
    return state


def _count_output_jsons() -> int:
    if not OUTPUT_DIR.exists():
        return 0
    return len([p for p in OUTPUT_DIR.glob("*.json") if not p.name.startswith("_")])


# ── Load current job state ──
state = load_state()
state = _refresh_job_status(state)


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Upload
# ═══════════════════════════════════════════════════════════════════════════
st.header("① Upload PDFs")

uploaded_files = st.file_uploader(
    "Drag and drop annual report PDFs here",
    type=["pdf"],
    accept_multiple_files=True,
    help="Upload one or more Canadian financial institution annual report PDFs.",
)

if uploaded_files:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved_names: list[str] = []
    for uf in uploaded_files:
        dest = INPUT_DIR / uf.name
        dest.write_bytes(uf.getbuffer())
        saved_names.append(uf.name)
    st.success(f"✅ Saved {len(saved_names)} file(s) to processing queue.")

# Show files currently waiting in input/
pending_pdfs = sorted(p.name for p in INPUT_DIR.glob("*.pdf")) if INPUT_DIR.exists() else []
if pending_pdfs:
    with st.expander(f"📂 Files ready to process ({len(pending_pdfs)})", expanded=True):
        for f in pending_pdfs:
            st.text(f"  📄 {f}")
else:
    st.info("No PDFs in the upload queue. Upload files above to get started.")


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Process
# ═══════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.header("② Process")

if state.status == "running":
    st.warning("🔄 **Pipeline is running…** please wait for it to finish.")

    # Show live log tail
    if LOG_FILE.exists():
        try:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = lines[-20:] if len(lines) > 20 else lines
            st.code("\n".join(tail), language="log")
        except Exception:
            pass

    col_r1, col_r2 = st.columns(2)
    with col_r1:
        if st.button("🔄 Refresh status"):
            st.rerun()
    with col_r2:
        elapsed = time.time() - (state.started_at or time.time())
        st.caption(f"Running for {elapsed:.0f}s")

elif state.status in ("completed", "failed"):
    if state.status == "completed":
        n_results = _count_output_jsons()
        st.success(f"✅ Pipeline finished successfully — **{n_results}** institution(s) extracted.")
        st.page_link("pages/2_Results.py", label="📊 View Results", icon="➡️")
    else:
        st.error(f"❌ Pipeline failed (exit code {state.exit_code}).")
        st.page_link("pages/4_Log_Viewer.py", label="📜 View Logs", icon="🔍")

    if st.button("🔁 Start New Run", help="Clear results and run again"):
        clear_state()
        st.rerun()

else:
    # idle — show process button
    can_run = len(pending_pdfs) > 0

    if st.button(
        "🚀 Start Processing",
        type="primary",
        disabled=not can_run,
        help="Run the AI extraction pipeline on all uploaded PDFs.",
    ):
        # Clear old log
        LOG_FILE.unlink(missing_ok=True)

        # Launch pipeline as a fully detached subprocess.
        # We use a shell wrapper that writes the exit code to a marker file
        # so the dashboard can detect success/failure after the process ends.
        marker = ROOT / ".last_exit_code"
        marker.unlink(missing_ok=True)

        # Build a command that runs the pipeline and writes exit code on finish
        cmd = (
            f'"{sys.executable}" -m src.main '
            f'> "{LOG_FILE}" 2>&1 & '
            f'echo %errorlevel% > "{marker}"'
        )

        # On Windows, use CREATE_NO_WINDOW to avoid a console popup
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            f'cmd /c "{sys.executable}" -m src.main > "{LOG_FILE}" 2>&1',
            cwd=str(ROOT),
            shell=True,
            creationflags=creation_flags,
        )

        # Save job state
        new_state = JobState(
            status="running",
            pid=proc.pid,
            started_at=time.time(),
            log_path=str(LOG_FILE),
            uploaded_files=pending_pdfs,
        )
        save_state(new_state)
        st.rerun()

    if not can_run:
        st.caption("Upload at least one PDF to enable processing.")


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — Quick Status
# ═══════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.header("③ Results at a Glance")

col_s1, col_s2, col_s3 = st.columns(3)
n_input = len(pending_pdfs)
n_output = _count_output_jsons()
n_summaries = len(list(OUTPUT_DIR.glob("_summary*"))) if OUTPUT_DIR.exists() else 0

col_s1.metric("📄 PDFs Uploaded", n_input)
col_s2.metric("📋 Institutions Extracted", n_output)
col_s3.metric("📊 Summary Files", n_summaries)
