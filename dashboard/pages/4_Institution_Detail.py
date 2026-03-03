"""Institution Detail page — drill-down with field-by-field PDF page viewer."""

from __future__ import annotations

import json
import io
import streamlit as st
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT / "output"
INPUT_PCU = ROOT / "input" / "pcu"
INPUT_BANK = ROOT / "input" / "bank_fcu_other"

st.set_page_config(page_title="Institution Detail", layout="wide")

# ── Helpers ──

def _find_output_jsons() -> dict[str, Path]:
    """Return {institution_name: path} for all non-summary JSONs."""
    if not OUTPUT_DIR.exists():
        return {}
    return {
        p.stem: p
        for p in sorted(OUTPUT_DIR.glob("*.json"))
        if not p.name.startswith("_")
    }


def _find_pdf(source_file: str | None, category: str) -> Path | None:
    """Locate the source PDF in the classified input sub-folder."""
    if not source_file:
        return None
    folder = INPUT_PCU if category == "pcu" else INPUT_BANK
    candidate = folder / source_file
    if candidate.exists():
        return candidate
    # Also try root input/
    root_candidate = ROOT / "input" / source_file
    return root_candidate if root_candidate.exists() else None


@st.cache_data(show_spinner=False)
def _render_page(pdf_path: str, page_num: int, dpi: int = 150) -> bytes:
    """Render a single PDF page to PNG bytes using PyMuPDF.

    *page_num* is 1-based (matching source_page from Content Understanding).
    """
    doc = fitz.open(pdf_path)
    # Content Understanding pages are 1-based
    page = doc[page_num - 1]
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")


def _build_fields(data: dict) -> list[dict]:
    """Build a flat list of field dicts from the output JSON.

    Each entry: {name, value, unit, confidence, source_page, corrections, warnings}
    """
    fields: list[dict] = []

    category = data.get("category", "pcu")

    # ── Scalar text fields ──
    text_fields: list[tuple[str, str]] = [
        ("institution_name", "Institution Name"),
        ("member_of_ria", "Member of RIA"),
    ]

    if category == "pcu":
        text_fields += [
            ("province", "Province"),
        ]
    
    for key, label in text_fields:
        fields.append({
            "name": label,
            "key": key,
            "value": data.get(key),
            "unit": None,
            "confidence": None,
            "source_page": None,
        })

    # ── StringConfidenceValue fields (ratings / deposit insurance) ──
    scv_fields: list[tuple[str, str]] = []
    if category == "pcu":
        scv_fields += [
            ("deposit_insurance_amount_guaranteed", "Deposit Insurance Amount"),
            ("deposit_insurance_dbrs", "Deposit Insurance DBRS"),
            ("deposit_insurance_guarantee_corporation", "Guarantee Corporation"),
        ]
    else:
        scv_fields += [
            ("short_term_dbrs", "Short-Term DBRS"),
            ("short_term_sp", "Short-Term S&P"),
            ("short_term_moodys", "Short-Term Moody's"),
            ("long_term_dbrs", "Long-Term DBRS"),
            ("long_term_sp", "Long-Term S&P"),
            ("long_term_moodys", "Long-Term Moody's"),
        ]

    for key, label in scv_fields:
        raw = data.get(key)
        # Handle both old plain-string format and new dict format
        if isinstance(raw, dict):
            fields.append({
                "name": label,
                "key": key,
                "value": raw.get("value"),
                "unit": None,
                "confidence": raw.get("confidence"),
                "source_page": raw.get("source_page"),
            })
        else:
            fields.append({
                "name": label,
                "key": key,
                "value": raw,
                "unit": None,
                "confidence": None,
                "source_page": None,
            })

    # ── ConfidenceValue fields ──
    cv_fields: list[tuple[str, str]] = [
        ("capital_ratio", "Capital Ratio"),
        ("allowance_for_credit_losses", "Allowance for Credit Losses"),
        ("loans_written_off", "Loans Written-Off"),
    ]
    for key, label in cv_fields:
        obj = data.get(key) or {}
        fields.append({
            "name": label,
            "key": key,
            "value": obj.get("value"),
            "unit": obj.get("unit"),
            "confidence": obj.get("confidence"),
            "source_page": obj.get("source_page"),
        })

    # ── YearPair fields ──
    yp_fields: list[tuple[str, str]] = [
        ("assets", "Assets"),
        ("deposits", "Deposits"),
        ("total_loans", "Total Loans"),
    ]
    for key, label in yp_fields:
        pair = data.get(key) or {}
        for year in ("2023", "2024"):
            obj = pair.get(year) or {}
            fields.append({
                "name": f"{label} {year}",
                "key": f"{key}_{year}",
                "value": obj.get("value"),
                "unit": obj.get("unit"),
                "confidence": obj.get("confidence"),
                "source_page": obj.get("source_page"),
            })

    # Attach quality / metadata at the end
    fields.append({
        "name": "Extraction Quality",
        "key": "extraction_quality",
        "value": data.get("extraction_quality"),
        "unit": None,
        "confidence": None,
        "source_page": None,
    })
    fields.append({
        "name": "Source File",
        "key": "source_file",
        "value": data.get("source_file"),
        "unit": None,
        "confidence": None,
        "source_page": None,
    })
    fields.append({
        "name": "Extracted At",
        "key": "extracted_at",
        "value": data.get("extracted_at"),
        "unit": None,
        "confidence": None,
        "source_page": None,
    })

    return fields


def _find_corrections(data: dict, key: str) -> list[dict]:
    """Return corrections matching *key* (partial match on field name)."""
    return [c for c in data.get("corrections", []) if key in c.get("field", "")]


def _find_warnings(data: dict, key: str) -> list[dict]:
    """Return warnings matching *key*."""
    return [w for w in data.get("warnings", []) if key in w.get("field", "")]


# ── Main ──

st.title("🔎 Institution Detail")

institutions = _find_output_jsons()
if not institutions:
    st.warning("No institution output files found.  Run the pipeline first.")
    st.stop()

# ── Institution selector ──
# Check if an institution was passed via query params
names = list(institutions.keys())
default_idx = 0

selected = st.selectbox("Select institution", names, index=default_idx)

json_path = institutions[selected]
with open(json_path) as f:
    data = json.load(f)

category = data.get("category", "pcu")
source_file = data.get("source_file")
pdf_path = _find_pdf(source_file, category)

st.caption(
    f"**Category:** {category.upper()}  |  "
    f"**Source:** {source_file}  |  "
    f"**Quality:** {data.get('extraction_quality', '?')}"
)
st.markdown("---")

# ── Field navigation ──
fields = _build_fields(data)
n_fields = len(fields)

if "field_idx" not in st.session_state:
    st.session_state.field_idx = 0

# Reset index when institution changes
if st.session_state.get("_last_institution") != selected:
    st.session_state.field_idx = 0
    st.session_state._last_institution = selected

idx = st.session_state.field_idx

# Navigation buttons
nav_col1, nav_col2, nav_col3, nav_col4 = st.columns([1, 1, 2, 1])
with nav_col1:
    if st.button("⏮ First", disabled=idx == 0):
        st.session_state.field_idx = 0
        st.rerun()
with nav_col2:
    if st.button("◀ Previous", disabled=idx == 0):
        st.session_state.field_idx = max(0, idx - 1)
        st.rerun()
with nav_col3:
    st.markdown(f"**Field {idx + 1} of {n_fields}**")
with nav_col4:
    if st.button("Next ▶", disabled=idx >= n_fields - 1):
        st.session_state.field_idx = min(n_fields - 1, idx + 1)
        st.rerun()

# Also add a field selector dropdown for quick jumping
field_labels = [f"{i+1}. {fl['name']}" for i, fl in enumerate(fields)]
jump_selection = st.selectbox(
    "Jump to field",
    field_labels,
    index=idx,
    label_visibility="collapsed",
)
jump_idx = field_labels.index(jump_selection)
if jump_idx != idx:
    st.session_state.field_idx = jump_idx
    st.rerun()

st.markdown("---")

# ── Side-by-side: field details (left) + PDF page (right) ──
field = fields[idx]

left_col, right_col = st.columns([1, 1])

with left_col:
    st.subheader(f"📝 {field['name']}")

    # Value
    val = field["value"]
    unit = field["unit"]
    if val is not None:
        display_val = f"**{val}**"
        if unit:
            display_val += f"  ({unit})"
        st.markdown(f"**Value:** {display_val}")
    else:
        st.markdown("**Value:** _null / not extracted_")

    # Confidence
    conf = field["confidence"]
    if conf is not None:
        pct = conf * 100
        if pct >= 75:
            st.markdown(f"**Confidence:** :green[{pct:.1f}%]")
        elif pct >= 50:
            st.markdown(f"**Confidence:** :orange[{pct:.1f}%]")
        else:
            st.markdown(f"**Confidence:** :red[{pct:.1f}%]")
    else:
        st.markdown("**Confidence:** —")

    # Source page
    sp = field["source_page"]
    if sp is not None:
        st.markdown(f"**Source Page:** {sp}")
    else:
        st.markdown("**Source Page:** —")

    # Corrections
    corrections = _find_corrections(data, field["key"])
    if corrections:
        st.markdown("#### ✏️ Corrections")
        for c in corrections:
            st.warning(
                f"**{c.get('field', '')}**: "
                f"`{c.get('original_value')}` → `{c.get('corrected_value')}`  \n"
                f"_{c.get('reason', '')}_"
            )
    else:
        st.caption("No corrections for this field.")

    # Warnings
    warnings = _find_warnings(data, field["key"])
    if warnings:
        st.markdown("#### ⚠️ Warnings")
        for w in warnings:
            st.info(f"**{w.get('field', '')}**: {w.get('message', '')}")
    else:
        st.caption("No warnings for this field.")

with right_col:
    if sp is not None and pdf_path is not None:
        if fitz is None:
            st.error(
                "**PyMuPDF** is not installed.  "
                "Run `pip install pymupdf` to enable PDF page rendering."
            )
        else:
            st.subheader(f"📄 PDF — Page {sp}")
            try:
                png_bytes = _render_page(str(pdf_path), sp)
                st.image(png_bytes, use_container_width=True)
            except Exception as exc:
                st.error(f"Could not render page {sp}: {exc}")
    elif sp is not None and pdf_path is None:
        st.warning(
            f"Source PDF `{source_file}` not found in input folders.  "
            "PDF page preview unavailable."
        )
    else:
        st.caption("No source page associated with this field.")

# ── Full JSON (collapsed) ──
st.markdown("---")
with st.expander("📋 Full Output JSON"):
    st.json(data)

# ── All corrections & warnings ──
with st.expander(f"📝 All Corrections ({len(data.get('corrections', []))})"):
    for c in data.get("corrections", []):
        st.write(c)

with st.expander(f"⚠️ All Warnings ({len(data.get('warnings', []))})"):
    for w in data.get("warnings", []):
        st.write(w)
