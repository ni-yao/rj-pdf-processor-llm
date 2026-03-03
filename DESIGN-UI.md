# Streamlit Dashboard — UI Design Document

**Project:** rj-pdf-processor-llm  
**Date:** March 3, 2026  
**Status:** Draft  
**Parent Document:** [DESIGN.md](DESIGN.md)

---

## 1. Overview

A view-only Streamlit web dashboard for browsing pipeline inputs, outputs, and logs produced by the extraction pipeline described in [DESIGN.md](DESIGN.md). The dashboard does **not** trigger or control the pipeline — it is a post-run review tool that reads local files from the `input/`, `output/`, and project root directories.

**Launch command:**

```bash
streamlit run dashboard/app.py --server.port 8501
```

**URL:** `http://localhost:8501`

---

## 2. Page Structure

The dashboard uses Streamlit's multi-page layout. Each page is a standalone `.py` file under `dashboard/pages/`.

| Page | File | Purpose |
|---|---|---|
| **Home** | `dashboard/app.py` | Folder browser — shows input/output file listings |
| **PCU Summary** | `dashboard/pages/2_PCU_Summary.py` | Interactive table of PCU results from `_summary_pcu.csv` |
| **Bank Summary** | `dashboard/pages/3_Bank_Summary.py` | Interactive table of Bank/FCU/Other results from `_summary_bank_fcu_other.csv` |
| **Institution Detail** | `dashboard/pages/4_Institution_Detail.py` | Field-by-field drill-down with PDF page rendering |
| **Log Viewer** | `dashboard/pages/5_Log_Viewer.py` | Pipeline log viewer with tail/refresh controls |

---

## 3. Page Details

### 3.1 Home (`app.py`)

Displays the current state of the file system:

- **Input Folders** — three-column layout showing PDFs in `input/` (root), `input/pcu/`, and `input/bank_fcu_other/`.
- **Output Folder** — two-column layout showing individual institution JSONs and summary files (`_summary*.json`, `_summary*.csv`).

No interactive controls beyond navigation. This page answers: "What files exist before and after a pipeline run?"

### 3.2 PCU Summary (`2_PCU_Summary.py`)

Reads `output/_summary_pcu.csv` and displays:

- **Institution count** metric.
- **Formatted data table** with columns: Institution, Province, RIA Member, Deposit Insurance fields, Capital Ratio (%), Assets/Deposits/Loans (formatted as `$X.XXXB`), ACL/Write-Offs (formatted as `$X.XM`), Extraction Quality.
- **Drill-down links** — one per institution, linking to the Institution Detail page.

Formatting helpers:
- Billions: `$1,234.567B`
- Millions: `$32.5M`
- Percentages: `14.50%`
- Missing values: `—`

### 3.3 Bank Summary (`3_Bank_Summary.py`)

Same structure as PCU Summary but reads `output/_summary_bank_fcu_other.csv`. Columns include the six credit rating fields (ST/LT DBRS, S&P, Moody's) instead of the deposit insurance fields.

### 3.4 Institution Detail (`4_Institution_Detail.py`)

The primary review page. Provides field-by-field navigation through a single institution's extracted data, with side-by-side PDF page rendering.

**Layout:**

```
┌─────────────────────────────────────────────────────────┐
│ Institution Selector (dropdown)                         │
│ Category: PCU | Source: 2024 - First Ontario.pdf | ...  │
├─────────────────────────────────────────────────────────┤
│ [⏮ First] [◀ Prev]  Field 3 of 18  [Next ▶]           │
│ [Jump to field dropdown]                                │
├────────────────────┬────────────────────────────────────┤
│ 📝 Field Details   │ 📄 PDF Page Preview                │
│                    │                                    │
│ Value: **14.5**    │ ┌──────────────────────────┐       │
│ Unit: %            │ │                          │       │
│ Confidence: 92.0%  │ │  Rendered PDF page image  │       │
│ Source Page: 8     │ │  (PyMuPDF @ 150 DPI)     │       │
│                    │ │                          │       │
│ ✏️ Corrections     │ └──────────────────────────┘       │
│ ⚠️ Warnings        │                                    │
├────────────────────┴────────────────────────────────────┤
│ ▸ Full Output JSON (expander)                           │
│ ▸ All Corrections (expander)                            │
│ ▸ All Warnings (expander)                               │
└─────────────────────────────────────────────────────────┘
```

**Field categories handled:**

| Category | Examples | Metadata Displayed |
|---|---|---|
| Plain text | `institution_name`, `member_of_ria`, `province` | Value only |
| `StringConfidenceValue` | Credit ratings, deposit insurance fields | Value, confidence, source_page |
| `ConfidenceValue` | `capital_ratio`, `allowance_for_credit_losses`, `loans_written_off` | Value, unit, confidence, source_page |
| `YearPairValue` | `assets`, `deposits`, `total_loans` (× 2023, 2024) | Value, unit, confidence, source_page |

**Backward compatibility:** `StringConfidenceValue` fields handle both the new `{value, confidence, source_page}` dict format and the legacy plain-string format (from older pipeline runs).

**PDF rendering:** Uses **PyMuPDF** (`fitz`) to render the `source_page` at 150 DPI as a PNG image. Results are cached via `@st.cache_data`. If PyMuPDF is not installed or the PDF is not found, a graceful fallback message is shown.

**Confidence color coding:**
- ≥ 75%: :green[green]
- ≥ 50%: :orange[orange]
- < 50%: :red[red]

### 3.5 Log Viewer (`5_Log_Viewer.py`)

Reads `run_output.log` from the project root.

- **Refresh button** — manually re-reads the file.
- **Tail control** — numeric input to show the last N lines (default 200, 0 = show all).
- Displays log content in a code block with line numbers.

---

## 4. Data Flow

The dashboard is **read-only**. All data is produced by the pipeline (`python -m src.main`) and consumed by the dashboard from the local file system.

```
Pipeline (src/main.py)                      Dashboard (dashboard/)
─────────────────────                       ──────────────────────
                                            
input/*.pdf ─────► Agent 0 ─► input/pcu/    ──► Home page (folder listing)
                              input/bank/
                                            
                   Agent 1-3 ─► output/     
                     │          ├── {name}.json ──► Institution Detail
                     │          ├── _summary_pcu.csv ──► PCU Summary
                     │          ├── _summary_pcu.json
                     │          ├── _summary_bank_fcu_other.csv ──► Bank Summary
                     │          └── _summary_bank_fcu_other.json
                     │
                     └──────► run_output.log ──► Log Viewer
```

---

## 5. Dependencies

| Package | Version | Purpose |
|---|---|---|
| `streamlit` | ≥ 1.40.0 | Web framework |
| `pymupdf` | ≥ 1.24.0 | PDF page rendering (Institution Detail) |
| `pandas` | ≥ 2.0.0 | CSV reading and table formatting (Summary pages) |

All three are listed in `requirements.txt`.

---

## 6. File Structure

```
dashboard/
├── app.py                             # Home page — folder browser
└── pages/
    ├── 2_PCU_Summary.py               # PCU CSV summary table
    ├── 3_Bank_Summary.py              # Bank/FCU/Other CSV summary table
    ├── 4_Institution_Detail.py        # Field-by-field viewer + PDF page preview
    └── 5_Log_Viewer.py                # Pipeline log viewer
```

---

## 7. Design Decisions

| Decision | Rationale |
|---|---|
| **View-only (no pipeline trigger)** | The pipeline requires Azure CLI authentication (`az login`) and runs 15–20 minutes. Subprocess management in a web UI adds complexity and auth-passthrough issues. CLI execution is simpler and more reliable. |
| **Local file access only** | The dashboard runs alongside the pipeline on the same machine. No API layer or database needed — just reads JSON/CSV files from `output/`. |
| **PyMuPDF for PDF rendering** | Renders individual pages as PNG images without needing a browser PDF viewer. Works with `source_page` metadata to show exactly which page a field was extracted from. |
| **Backward-compatible field parsing** | `StringConfidenceValue` fields check `isinstance(raw, dict)` to handle both new structured format and legacy plain-string format from older runs. |
| **Streamlit multi-page app** | Built-in sidebar navigation, no routing setup needed. Each page is a standalone script that can be developed/tested independently. |
| **150 DPI rendering** | Balances readability and performance. Higher DPI increases memory and render time for large bank report pages (300+ pages). |

---

## 8. Future Enhancements

1. **Search / filter** on summary tables (institution name, quality, confidence thresholds).
2. **Side-by-side institution comparison** — select two institutions and compare field values.
3. **Confidence heatmap** — color-coded overview of all fields' confidence scores across institutions.
4. **Export** — download filtered summary tables as Excel/CSV from the UI.
5. **Auto-refresh** — poll for new output files while pipeline is running.
