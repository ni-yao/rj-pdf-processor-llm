"""Streamlit dashboard — Home page.

Shows input/output folder contents (view-only).
"""

from __future__ import annotations

import streamlit as st
from pathlib import Path

# ── Project root (one level up from dashboard/) ──
ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
LOG_FILE = ROOT / "run_output.log"


def _list_pdfs(folder: Path) -> list[str]:
    """Return sorted PDF names in *folder* (non-recursive)."""
    if not folder.exists():
        return []
    return sorted(p.name for p in folder.glob("*.pdf"))


def _list_jsons(folder: Path) -> list[str]:
    """Return sorted JSON filenames in *folder*, excluding _summary* files."""
    if not folder.exists():
        return []
    return sorted(
        p.name for p in folder.glob("*.json") if not p.name.startswith("_")
    )


def _list_summaries(folder: Path) -> list[str]:
    """Return summary files (_summary*.json / .csv)."""
    if not folder.exists():
        return []
    return sorted(
        p.name
        for p in folder.iterdir()
        if p.name.startswith("_summary") and p.suffix in (".json", ".csv")
    )


# ── Page config ──
st.set_page_config(page_title="PDF Processor Dashboard", layout="wide")

st.title("📊 PDF Processor Dashboard")
st.markdown("---")

# ── Input folders ──
st.header("📂 Input Folders")

col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("input/ (root)")
    root_pdfs = _list_pdfs(INPUT_DIR)
    if root_pdfs:
        for f in root_pdfs:
            st.text(f"  📄 {f}")
    else:
        st.caption("No PDFs (files are moved to sub-folders after classification)")

with col2:
    st.subheader("input/pcu/")
    pcu_pdfs = _list_pdfs(INPUT_DIR / "pcu")
    if pcu_pdfs:
        for f in pcu_pdfs:
            st.text(f"  📄 {f}")
    else:
        st.caption("Empty")

with col3:
    st.subheader("input/bank_fcu_other/")
    bank_pdfs = _list_pdfs(INPUT_DIR / "bank_fcu_other")
    if bank_pdfs:
        for f in bank_pdfs:
            st.text(f"  📄 {f}")
    else:
        st.caption("Empty")

st.markdown("---")

# ── Output folder ──
st.header("📁 Output Folder")

out_col1, out_col2 = st.columns(2)

with out_col1:
    st.subheader("Institution JSONs")
    inst_jsons = _list_jsons(OUTPUT_DIR)
    if inst_jsons:
        for f in inst_jsons:
            st.text(f"  📋 {f}")
    else:
        st.caption("No output files yet")

with out_col2:
    st.subheader("Summary Files")
    summaries = _list_summaries(OUTPUT_DIR)
    if summaries:
        for f in summaries:
            st.text(f"  📊 {f}")
    else:
        st.caption("No summary files yet")
