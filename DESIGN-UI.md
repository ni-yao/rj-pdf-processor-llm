# Streamlit Dashboard — UI Design Document

**Project:** rj-pdf-processor-llm  
**Date:** March 3, 2026  
**Updated:** April 28, 2026  
**Status:** Draft  
**Parent Document:** [DESIGN.md](DESIGN.md)

---

## 1. Overview

An interactive Streamlit web dashboard for uploading annual report PDFs, triggering the extraction pipeline, and reviewing results. Users can drag-and-drop PDF files directly into the browser, start processing with one click, and browse extracted data — all without touching the file system or CLI.

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
| **Upload & Process** | `dashboard/app.py` | Upload PDFs, trigger pipeline, view status |
| **Results** | `dashboard/pages/2_Results.py` | Combined PCU + Bank summary tables with download buttons |
| **Institution Detail** | `dashboard/pages/3_Institution_Detail.py` | Field-by-field drill-down with PDF page rendering |
| **Log Viewer** | `dashboard/pages/4_Log_Viewer.py` | Pipeline log viewer with auto-refresh |

---

## 3. Page Details

### 3.1 Upload & Process (`app.py`)

The main entry point — three sections:

- **① Upload PDFs** — drag-and-drop `st.file_uploader` (multi-file, PDF-only). Files are saved to `input/` on upload. Expandable list shows files ready to process.
- **② Process** — "Start Processing" button launches `python -m src.main` as a background subprocess. Shows real-time log tail while running, completion status with link to results, or error status with link to logs.
- **③ Results at a Glance** — three metrics: PDFs uploaded, institutions extracted, summary files generated.

Pipeline runs are tracked via a lightweight JSON state file (`dashboard/.job_state.json`) that persists across reruns and page switches. Only one run at a time is allowed.

### 3.2 Results (`2_Results.py`)

Combines the PCU and Bank summary views into a single tabbed page:

- **Tabs:** "Credit Unions (PCU)" and "Banks / FCU / Other" — each tab loads its respective CSV.
- **Download buttons** for CSV and JSON summary files per category.
- **Institution count** metric per tab.
- **Formatted data table** with currency/percentage formatting.
- **Drill-down links** to Institution Detail page.

### 3.3 Institution Detail (`3_Institution_Detail.py`)

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

### 3.4 Log Viewer (`4_Log_Viewer.py`)

Reads `run_output.log` from the project root.

- **Refresh button** — manually re-reads the file.
- **Auto-refresh toggle** — enabled by default when pipeline is running; polls every 3 seconds.
- **Tail control** — numeric input to show the last N lines (default 200, 0 = show all).
- Displays log content in a code block with line numbers.

---

## 4. Data Flow

```
User (browser)                          Dashboard (dashboard/)
──────────────                          ──────────────────────

Upload PDFs ──► st.file_uploader ──► input/*.pdf
                                        │
Click "Process" ──► subprocess ──► python -m src.main
                                        │
                     ┌──────────────────┘
                     │
                     ├──► input/pcu/          (classified)
                     ├──► input/bank_fcu_other/
                     │
                     ├──► output/{name}.json  ──► Institution Detail
                     ├──► output/_summary_pcu.csv ──► Results (PCU tab)
                     ├──► output/_summary_bank_fcu_other.csv ──► Results (Bank tab)
                     │
                     └──► run_output.log      ──► Log Viewer
```

---

## 5. Job State Management

Pipeline runs are tracked via `dashboard/.job_state.json` (gitignored):

| Field | Description |
|---|---|
| `status` | `idle` / `running` / `completed` / `failed` |
| `pid` | OS process ID of the pipeline subprocess |
| `started_at` | Unix timestamp |
| `finished_at` | Unix timestamp |
| `exit_code` | Process exit code (0 = success) |
| `log_path` | Path to log file |
| `uploaded_files` | List of filenames in the batch |

This allows the dashboard to survive Streamlit reruns, page switches, and browser refreshes while maintaining accurate job status.

---

## 6. Dependencies

| Package | Version | Purpose |
|---|---|---|
| `streamlit` | ≥ 1.40.0 | Web framework |
| `pymupdf` | ≥ 1.24.0 | PDF page rendering (Institution Detail) |
| `pandas` | ≥ 2.0.0 | CSV reading and table formatting (Results page) |

All three are listed in `requirements.txt`.

---

## 7. File Structure

```
dashboard/
├── app.py                             # Upload & Process (home page)
├── job_state.py                       # Lightweight job-state persistence
└── pages/
    ├── 2_Results.py                   # Combined PCU + Bank summary with tabs
    ├── 3_Institution_Detail.py        # Field-by-field viewer + PDF page preview
    └── 4_Log_Viewer.py                # Pipeline log viewer with auto-refresh
```

---

## 8. Design Decisions

| Decision | Rationale |
|---|---|
| **In-browser file upload** | Eliminates the need for users to access the server file system. `st.file_uploader` supports drag-and-drop, multi-file, and file-type filtering natively. |
| **Subprocess pipeline trigger** | Runs the existing `python -m src.main` as-is with stdout/stderr redirected to log file. No changes to pipeline code required. |
| **Single-run lock** | The pipeline uses shared input/output folders. Allowing concurrent runs would cause file conflicts. A JSON state file enforces one-at-a-time. |
| **JSON state file over session_state** | `st.session_state` is browser-session-scoped and doesn't survive page refreshes or new tabs. A disk file is more durable. |
| **Combined Results page with tabs** | PCU and Bank summaries share the same layout pattern but different column schemas. Tabs keep them together without forcing a premature schema merge. |
| **Auto-refresh in Log Viewer** | Polls every 3 seconds via `time.sleep()` + `st.rerun()` while pipeline is running. Stops automatically when the job completes. |
| **PyMuPDF for PDF rendering** | Renders individual pages as PNG images without needing a browser PDF viewer. Works with `source_page` metadata to show exactly which page a field was extracted from. |
| **Backward-compatible field parsing** | `StringConfidenceValue` fields check `isinstance(raw, dict)` to handle both new structured format and legacy plain-string format from older runs. |

---

## 9. Future Enhancements

1. **Per-job isolation** — job-specific input/output directories to support run history and concurrent processing.
2. **Search / filter** on summary tables (institution name, quality, confidence thresholds).
3. **Side-by-side institution comparison** — select two institutions and compare field values.
4. **Confidence heatmap** — color-coded overview of all fields' confidence scores across institutions.
5. **Export** — download filtered summary tables as Excel from the UI.
6. **Authentication** — add login to restrict access in shared deployments.
