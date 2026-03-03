"""Bank / FCU / Other Summary page — displays _summary_bank_fcu_other.csv."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT / "output"
CSV_PATH = OUTPUT_DIR / "_summary_bank_fcu_other.csv"

st.set_page_config(page_title="Bank Summary", layout="wide")
st.title("🏛️ Bank / FCU / Other — Summary")

if not CSV_PATH.exists():
    st.warning("No Bank/FCU/Other summary file found.  Run the pipeline first.")
    st.stop()

df = pd.read_csv(CSV_PATH)

# ── Quick stats ──
st.metric("Institutions", len(df))
st.markdown("---")

# ── Formatting helpers ──
def _fmt_billions(v):
    if pd.isna(v):
        return "—"
    return f"${v:,.3f}B"

def _fmt_millions(v):
    if pd.isna(v):
        return "—"
    return f"${v:,.1f}M"

def _fmt_pct(v):
    if pd.isna(v):
        return "—"
    return f"{v:.2f}%"

# ── Build display table ──
display = pd.DataFrame()
display["Institution"] = df["institution_name"]
display["RIA Member"] = df["member_of_ria"]
display["ST DBRS"] = df["short_term_dbrs"]
display["ST S&P"] = df["short_term_sp"]
display["ST Moody's"] = df["short_term_moodys"]
display["LT DBRS"] = df["long_term_dbrs"]
display["LT S&P"] = df["long_term_sp"]
display["LT Moody's"] = df["long_term_moodys"]
display["Capital Ratio"] = df["capital_ratio"].apply(_fmt_pct)
display["Assets 2023"] = df["assets_2023_billion"].apply(_fmt_billions)
display["Assets 2024"] = df["assets_2024_billion"].apply(_fmt_billions)
display["Deposits 2023"] = df["deposits_2023_billion"].apply(_fmt_billions)
display["Deposits 2024"] = df["deposits_2024_billion"].apply(_fmt_billions)
display["Loans 2023"] = df["total_loans_2023_billion"].apply(_fmt_billions)
display["Loans 2024"] = df["total_loans_2024_billion"].apply(_fmt_billions)
display["ACL (MM)"] = df["allowance_for_credit_losses_mm"].apply(_fmt_millions)
display["Write-Offs (MM)"] = df["loans_written_off_mm"].apply(_fmt_millions)
display["Quality"] = df["extraction_quality"]

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    height=400,
)

st.markdown("---")
st.subheader("🔍 Drill Down")
st.markdown(
    "Click an institution name below to see its full extracted data and PDF page images."
)

for _, row in df.iterrows():
    name = row["institution_name"]
    json_file = OUTPUT_DIR / f"{name}.json"
    if json_file.exists():
        st.page_link(
            "pages/4_Institution_Detail.py",
            label=f"📋 {name}",
            icon="🔎",
        )
    else:
        st.text(f"  {name}  (output JSON not found)")

st.info("💡 **Tip:** Select the institution on the detail page using the dropdown.")
