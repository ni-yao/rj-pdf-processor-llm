"""Results page — combined PCU + Bank summary with tabs and download buttons."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT / "output"

PCU_CSV = OUTPUT_DIR / "_summary_pcu.csv"
BANK_CSV = OUTPUT_DIR / "_summary_bank_fcu_other.csv"

st.set_page_config(page_title="Results", layout="wide")
st.title("📊 Extraction Results")


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


def _download_btn(file_path: Path, label: str):
    """Render a download button for a file if it exists."""
    if file_path.exists():
        data = file_path.read_bytes()
        suffix = file_path.suffix.lstrip(".")
        mime = "text/csv" if suffix == "csv" else "application/json"
        st.download_button(
            label=label,
            data=data,
            file_name=file_path.name,
            mime=mime,
        )


# ── Check if any results exist ──
has_pcu = PCU_CSV.exists()
has_bank = BANK_CSV.exists()

if not has_pcu and not has_bank:
    st.warning("No results found yet. Upload PDFs and run the pipeline from the **Home** page.")
    st.page_link("app.py", label="⬅️ Go to Upload & Process", icon="📤")
    st.stop()


# ── Tabs ──
tab_names = []
if has_pcu:
    tab_names.append("🏦 Credit Unions (PCU)")
if has_bank:
    tab_names.append("🏛️ Banks / FCU / Other")

tabs = st.tabs(tab_names)
tab_idx = 0


# ── PCU Tab ──
if has_pcu:
    with tabs[tab_idx]:
        df = pd.read_csv(PCU_CSV)

        col_m, col_d1, col_d2 = st.columns([1, 1, 1])
        with col_m:
            st.metric("Institutions", len(df))
        with col_d1:
            _download_btn(PCU_CSV, "⬇️ Download CSV")
        with col_d2:
            pcu_json = OUTPUT_DIR / "_summary_pcu.json"
            _download_btn(pcu_json, "⬇️ Download JSON")

        st.markdown("---")

        display = pd.DataFrame()
        display["Institution"] = df["institution_name"]
        display["Province"] = df["province"]
        display["RIA Member"] = df["member_of_ria"]
        display["Deposit Ins."] = df["deposit_insurance_amount_guaranteed"]
        display["Deposit Ins. DBRS"] = df["deposit_insurance_dbrs"]
        display["Guarantee Corp."] = df["deposit_insurance_guarantee_corporation"]
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

        st.dataframe(display, use_container_width=True, hide_index=True, height=400)

        st.markdown("---")
        st.subheader("🔍 Drill Down")
        for _, row in df.iterrows():
            name = row["institution_name"]
            json_file = OUTPUT_DIR / f"{name}.json"
            if json_file.exists():
                st.page_link(
                    "pages/3_Institution_Detail.py",
                    label=f"📋 {name}",
                    icon="🔎",
                )
            else:
                st.text(f"  {name}  (output JSON not found)")

    tab_idx += 1


# ── Bank Tab ──
if has_bank:
    with tabs[tab_idx]:
        df = pd.read_csv(BANK_CSV)

        col_m, col_d1, col_d2 = st.columns([1, 1, 1])
        with col_m:
            st.metric("Institutions", len(df))
        with col_d1:
            _download_btn(BANK_CSV, "⬇️ Download CSV")
        with col_d2:
            bank_json = OUTPUT_DIR / "_summary_bank_fcu_other.json"
            _download_btn(bank_json, "⬇️ Download JSON")

        st.markdown("---")

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

        st.dataframe(display, use_container_width=True, hide_index=True, height=400)

        st.markdown("---")
        st.subheader("🔍 Drill Down")
        for _, row in df.iterrows():
            name = row["institution_name"]
            json_file = OUTPUT_DIR / f"{name}.json"
            if json_file.exists():
                st.page_link(
                    "pages/3_Institution_Detail.py",
                    label=f"📋 {name}",
                    icon="🔎",
                )
            else:
                st.text(f"  {name}  (output JSON not found)")

st.markdown("---")
st.info("💡 **Tip:** Select an institution on the detail page to see field-by-field data with PDF page images.")
