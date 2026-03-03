# Banking Annual Report Financial Data Extraction — Design Document

**Project:** rj-pdf-processor-llm  
**Date:** March 2, 2026  
**Status:** Draft  

---

## 1. Problem Statement

Extract structured financial and institutional metadata from Canadian financial institution annual report PDFs and output them as JSON. The institutions fall into **two categories** with different field sets:

### 1.1 Institution Categories

Institutions are classified using a reference list in `config/institutions.json`. PDFs are matched against this list during a pre-processing classification step (see Agent 0 in Section 4.0).

**Category A — Provincial Credit Unions (PCU)**

| # | Field | Unit | Description |
|---|---|---|---|
| 1 | **Institution Name** | text | Name of the credit union |
| 2 | **Province** | text | Canadian province where the credit union operates |
| 3 | **Member of RIA** | text | Whether the institution is a member of the Registered Investment Advisors network (Yes/No) |
| 4 | **Deposit Insurance Amount Guaranteed** | text | Deposit guarantee coverage amount/description |
| 5 | **Deposit Insurance DBRS** | text | DBRS Morningstar credit rating of the deposit guarantee corporation |
| 6 | **Deposit Insurance Deposit Guarantee Corporation** | text | Name of the provincial deposit guarantee corporation |
| 7 | **Total Capital Ratio** | % | Total capital ratio (total capital as a percentage of risk-weighted assets) |
| 8 | **2023 Assets** | Billion | Total assets for fiscal year 2023 |
| 9 | **2024 Assets** | Billion | Total assets for fiscal year 2024 |
| 10 | **2023 Deposits** | Billion | Total deposits for fiscal year 2023 |
| 11 | **2024 Deposits** | Billion | Total deposits for fiscal year 2024 |
| 12 | **2023 Total Loans** | Billion | Total loans for fiscal year 2023 |
| 13 | **2024 Total Loans** | Billion | Total loans for fiscal year 2024 |
| 14 | **Allowance for Credit Losses** | MM (millions) | Allowance for credit losses |
| 15 | **Loans Written-Off** | MM (millions) | Loans written off / net charge-offs |

**Category B — Banks / Federal Credit Unions / Others**

| # | Field | Unit | Description |
|---|---|---|---|
| 1 | **Institution Name** | text | Name of the bank / institution |
| 2 | **Member of RIA** | text | Whether the institution is a member of the Registered Investment Advisors network (Yes/No) |
| 3 | **Short Term DBRS** | text | DBRS Morningstar short-term credit rating |
| 4 | **Short Term S&P** | text | S&P Global short-term credit rating |
| 5 | **Short Term Moody's** | text | Moody's short-term credit rating |
| 6 | **Long Term DBRS** | text | DBRS Morningstar long-term credit rating |
| 7 | **Long Term S&P** | text | S&P Global long-term credit rating |
| 8 | **Long Term Moody's** | text | Moody's long-term credit rating |
| 9 | **Total Capital Ratio** | % | Total capital ratio (total capital as a percentage of risk-weighted assets) |
| 10 | **2023 Assets** | Billion | Total assets for fiscal year 2023 |
| 11 | **2024 Assets** | Billion | Total assets for fiscal year 2024 |
| 12 | **2023 Deposits** | Billion | Total deposits for fiscal year 2023 |
| 13 | **2024 Deposits** | Billion | Total deposits for fiscal year 2024 |
| 14 | **2023 Total Loans** | Billion | Total loans for fiscal year 2023 |
| 15 | **2024 Total Loans** | Billion | Total loans for fiscal year 2024 |
| 16 | **Allowance for Credit Losses** | MM (millions) | Allowance for credit losses |
| 17 | **Loans Written-Off** | MM (millions) | Loans written off / net charge-offs |

Annual reports vary significantly in layout, length (50–300+ pages), terminology, table formatting, and reporting standards (IFRS vs. ASPE). Some metadata fields (Province, RIA membership, deposit guarantee corporation, credit ratings) may not appear directly in the PDF and may need to be inferred from the institution's identity or generated from domain knowledge. This design uses **four specialized LLM agents** orchestrated in a pipeline to ensure accuracy and auditability:

| Agent | Role |
|-------|------|
| **Agent 0** | Classification — match PDF to institution list, determine category |
| **Agent 1** | Document Extraction — OCR + field extraction via the appropriate analyzer |
| **Agent 2** | Validation & Normalization — business rule checks, unit conversion |
| **Agent 3** | Cross-Check & Output Assembly — final quality gate, JSON assembly |

---

## 2. Target Tech Stack

| Component | Technology |
|---|---|
| Cloud | **Microsoft Azure** |
| AI Platform | **Azure AI Foundry** (Microsoft Foundry Resource) |
| Document Processing | **Azure Content Understanding** (GA, API `2025-11-01`) |
| LLM Models | **GPT-4.1** (Content Understanding field extraction), **GPT-5** (validation & output assembly), **GPT-4.1-mini** (classification), **text-embedding-3-large** (training examples) |
| Storage | **Azure Blob Storage** (staging PDFs for Content Understanding) |
| Orchestration | **Python** with `azure-ai-projects` SDK + `azure-identity` |
| Local I/O | Local filesystem — input folder → output folder |

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          LOCAL MACHINE / VM                              │
│                                                                          │
│  ┌────────────┐      ┌──────────────────────────────────────────────┐   │
│  │ Input PDFs │      │         Python Orchestrator                  │   │
│  │  (folder)  │─────▶│                                              │   │
│  └────────────┘      │  0. Agent 0: Classify PDFs                   │   │
│                      │     → move to input/pcu/ or input/bank_…/    │   │
│                      │     → unclassified → skip with warning       │   │
│                      │                                              │   │
│                      │  for each classified PDF:                    │   │
│                      │    1. Upload to Blob Storage                 │   │
│                      │    2. Agent 1: Extract (analyzer per type)   │   │
│                      │    3. Agent 2: Validate & Normalise          │   │
│                      │    4. Agent 3: Cross-Check & Assemble        │   │
│                      │    5. Write JSON to output/                  │   │
│                      │                                              │   │
│  ┌────────────┐      │  6. Write summaries (JSON + CSV per category) │   │
│  │Output JSON │◀─────│     + combined _summary.json                 │   │
│  │ + CSV      │      └──────┬───────┬───────┬───────┬──────────────┘   │
│  └────────────┘             │       │       │       │                   │
└─────────────────────────────┼───────┼───────┼───────┼───────────────────┘
                              │       │       │       │
               ┌──────────────┘       │       │       └──────────────┐
               ▼                      ▼       ▼                      ▼
     ┌─────────────────┐  ┌────────────────────────┐  ┌─────────────────┐
     │  AGENT 0        │  │  AGENT 1               │  │  AGENT 2 / 3    │
     │  Classification │  │  Document Extraction    │  │  Validation &   │
     │                 │  │                         │  │  Output Assembly│
     │  GPT-4.1-mini   │  │  Azure Content          │  │                 │
     │  + institution  │  │  Understanding          │  │  GPT-5          │
     │  reference list │  │  ┌───────────────────┐  │  │  (temp=0.0)     │
     │                 │  │  │ pcu_annual_report  │  │  │                 │
     └─────────────────┘  │  │ bank_fcu_other_…  │  │  └─────────────────┘
                          │  └───────────────────┘  │
                          └─────────────────────────┘
```

---

## 4. Agent Design (4 Agents)

### 4.0 Agent 0 — Classification Agent

**Purpose:** Match each PDF filename to the institution reference list and determine whether it belongs to the **PCU** or **Bank/FCU/Other** category. Physically move the file into the appropriate intake subfolder.

**Technology:** GPT-4.1-mini via Azure Foundry (lightweight fuzzy matching).

**How it works:**

1. Loads the institution reference list from `config/institutions.json`.
2. For each PDF in `input/`, sends the filename to GPT-4.1-mini with the full institution list and asks the model to identify which institution it matches and which category it belongs to.
3. Based on the classification:
   - **PCU match** → moves the PDF to `input/pcu/`
   - **Bank/FCU/Other match** → moves the PDF to `input/bank_fcu_other/`
   - **No match** → moves the PDF to `input/unclassified/` and logs a warning (file is skipped for extraction)
4. Returns a classification manifest mapping each PDF to its category and matched institution name.

**Why use an LLM for classification?**
- PDF filenames are inconsistent — they may use abbreviations (e.g., "BMO" vs. "Bank of Montreal"), year prefixes (e.g., "2024 - TD.pdf"), or informal names (e.g., "WFCU" for "Windsor Family Credit Union").
- A simple string match would fail on these variations; GPT-4.1-mini handles fuzzy matching reliably at minimal cost (~$0.001 per classification).

**Input:** Flat list of PDF filenames + institution reference list (JSON)  
**Output:** Classification manifest (dict mapping filename → category + institution name)

---

### 4.1 Agent 1 — Document Extraction Agent

**Purpose:** Parse the PDF and extract raw financial field values with confidence scores and source grounding.

**Technology:** Azure Content Understanding with **two custom analyzers** built on `prebuilt-document` — one per institution category.

**How it works:**

1. The orchestrator uploads the PDF to Azure Blob Storage and generates a SAS URL.
2. Based on Agent 0's classification, it selects the appropriate analyzer:
   - **PCU** → `pcu_annual_report` (15 fields)
   - **Bank/FCU/Other** → `bank_fcu_other_annual_report` (17 fields)
3. It calls the Content Understanding **Analyze** API (`POST /contentunderstanding/analyzers/{analyzerId}:analyze`) with the SAS URL.
4. Content Understanding performs OCR, layout analysis, table detection, and then uses GPT-4.1 to extract the defined fields.
5. The result is a JSON payload with extracted values, confidence scores (0–1), and source grounding (page & bounding-box coordinates).

> **Note:** The two analyzer schemas are defined in `analyzers/pcu_annual_report.json` and `analyzers/bank_fcu_other_annual_report.json`. See Todo 3/4 for the full field definitions.

**Legacy Analyzer Schema (`banking-annual-report.json`) — to be replaced:**

```json
{
  "description": "Banking annual report financial metrics extractor",
  "baseAnalyzerId": "prebuilt-document",
  "models": {
    "completion": "gpt-4.1",
    "embedding": "text-embedding-3-large"
  },
  "config": {
    "returnDetails": true,
    "enableFormula": false,
    "disableContentFiltering": false,
    "estimateFieldSourceAndConfidence": true,
    "tableFormat": "html"
  },
  "fieldSchema": {
    "fields": {
      "ProvincialCreditUnion": {
        "type": "string",
        "method": "extract",
        "description": "The name of the provincial credit union as stated on the report cover or header"
      },
      "Province": {
        "type": "string",
        "method": "generate",
        "description": "The Canadian province where this credit union is headquartered and primarily operates (e.g., Ontario, British Columbia, Alberta). Determine from the address, charter information, or institution name."
      },
      "MemberOfRIA": {
        "type": "string",
        "method": "generate",
        "description": "Whether the credit union is a member of the Registered Investment Advisors network. Return 'Yes' or 'No'. Look for RIA references in the report or infer from the institution's known affiliations."
      },
      "AmountGuaranteed": {
        "type": "string",
        "method": "generate",
        "description": "The deposit guarantee coverage amount or description (e.g., '100% of eligible deposits', '$250,000 per depositor'). Look for deposit insurance/guarantee references in the notes or generate based on the provincial deposit guarantee corporation's known policy."
      },
      "DBRS": {
        "type": "string",
        "method": "extract",
        "description": "The DBRS Morningstar credit rating assigned to the credit union (e.g., 'R-1 (low)', 'BBB', 'A'). Look in the financial highlights, governance section, or notes to the financial statements."
      },
      "DepositGuaranteeCorporation": {
        "type": "string",
        "method": "generate",
        "description": "The name of the provincial deposit guarantee corporation that insures this credit union's deposits (e.g., 'DICO' for Ontario, 'CUDGC' for BC, 'CUDIC' for Alberta). Determine from the Province field or references in the report."
      },
      "CapitalRatio": {
        "type": "number",
        "method": "extract",
        "description": "The Total Capital Ratio as a percentage — total capital (Tier 1 + Tier 2) divided by risk-weighted assets. Extract ONLY from a structured table (capital adequacy table, financial highlights table, or regulatory capital schedule). Do NOT use CET1 ratio or leverage ratio. Do NOT extract from narrative paragraphs, executive summaries, or marketing text. Return as a percentage number (e.g., 14.5 for 14.5%)."
      },
      "Assets2023": {
        "type": "number",
        "method": "extract",
        "description": "Total assets for fiscal year 2023. Extract ONLY from a structured table (consolidated balance sheet, statement of financial position, or financial highlights table). This is the prior-year comparative figure. Do NOT extract from narrative paragraphs or marketing text. Return the raw number as reported."
      },
      "Assets2024": {
        "type": "number",
        "method": "extract",
        "description": "Total assets for fiscal year 2024. Extract ONLY from a structured table (consolidated balance sheet, statement of financial position, or financial highlights table). This is the current-year figure. Do NOT extract from narrative paragraphs or marketing text. Return the raw number as reported."
      },
      "Deposits2023": {
        "type": "number",
        "method": "extract",
        "description": "Total deposits for fiscal year 2023. Extract ONLY from a structured table (consolidated balance sheet or financial highlights table). May appear as 'Deposits', 'Member deposits', or 'Total deposits'. Do NOT extract from narrative paragraphs or marketing text. Return the raw number as reported."
      },
      "Deposits2024": {
        "type": "number",
        "method": "extract",
        "description": "Total deposits for fiscal year 2024. Extract ONLY from a structured table (consolidated balance sheet or financial highlights table). May appear as 'Deposits', 'Member deposits', or 'Total deposits'. Do NOT extract from narrative paragraphs or marketing text. Return the raw number as reported."
      },
      "TotalLoans2023": {
        "type": "number",
        "method": "extract",
        "description": "Total loans for fiscal year 2023. Extract ONLY from a structured table (consolidated balance sheet or financial highlights table). May appear as 'Loans', 'Loans to members', 'Net loans', or 'Total loans'. Do NOT extract from narrative paragraphs or marketing text. Return the raw number as reported."
      },
      "TotalLoans2024": {
        "type": "number",
        "method": "extract",
        "description": "Total loans for fiscal year 2024. Extract ONLY from a structured table (consolidated balance sheet or financial highlights table). May appear as 'Loans', 'Loans to members', 'Net loans', or 'Total loans'. Do NOT extract from narrative paragraphs or marketing text. Return the raw number as reported."
      },
      "AllowanceForCreditLosses": {
        "type": "number",
        "method": "extract",
        "description": "Allowance for credit losses (ACL) for the most recent period. Extract ONLY from a structured table (balance sheet, ACL rollforward table, loan schedule, or credit quality table). May appear as 'Allowance for credit losses', 'Allowance for loan losses', or 'Expected credit loss allowance'. Do NOT extract from narrative paragraphs or marketing text. Return the raw number as reported."
      },
      "LoansWrittenOff": {
        "type": "number",
        "method": "extract",
        "description": "Loans written off, net charge-offs, or net write-offs for the most recent fiscal year. Extract ONLY from a structured table (ACL rollforward table, credit quality table, or loan loss schedule). May appear as 'Write-offs', 'Charge-offs', 'Net write-offs', or 'Loans written off'. Do NOT extract from narrative paragraphs or marketing text. Return the raw number as reported."
      },
      "ReportingCurrencyUnit": {
        "type": "string",
        "method": "generate",
        "description": "The unit multiplier for reported figures (e.g., 'millions', 'thousands', 'billions', 'units'). Determine from table headers or notes like 'in thousands of Canadian dollars'. This is needed for unit conversion in post-processing."
      }
    }
  }
}
```

**Key design decisions:**
- `estimateFieldSourceAndConfidence: true` — enables confidence scores and source grounding for every extracted field, which feeds Agent 2.
- `tableFormat: "html"` — preserves table structure in the markdown representation, critical for balance sheet data.
- `method: "extract"` for numeric and factual fields — tells the model to locate the exact value in the document rather than generating/computing it.
- `method: "generate"` for institutional metadata fields (`Province`, `MemberOfRIA`, `AmountGuaranteed`, `DepositGuaranteeCorporation`, `ReportingCurrencyUnit`) — these are often implicit or require domain knowledge to determine (e.g., the deposit guarantee corporation is determined by province).
- **Table-only extraction** for all 9 financial numeric fields (`CapitalRatio`, `Assets2023/2024`, `Deposits2023/2024`, `TotalLoans2023/2024`, `AllowanceForCreditLosses`, `LoansWrittenOff`) — these must be sourced from structured financial tables (balance sheet, income statement, financial highlights table, capital adequacy table, ACL rollforward table, or audited schedule). Narrative paragraphs, executive summaries, and marketing text are explicitly excluded.
- Both **2023 and 2024** comparative figures are extracted for Assets, Deposits, and Total Loans to enable year-over-year analysis.
- Monetary fields are extracted as raw numbers; unit conversion (to Billions / MM) is handled by Agent 2 during normalization.

**Output:** Raw extracted JSON with confidence scores per field.

---

### 4.2 Agent 2 — Validation & Normalization Agent

**Purpose:** Validate the extracted values against business rules, normalize units to a common base, flag low-confidence or missing fields, and attempt re-extraction if needed.

**Technology:** GPT-5 via Azure Foundry (direct chat completion call — `temperature=0.0` for deterministic structured reasoning).

**How it works:**

1. Receives Agent 1's extraction result (fields + confidence scores + markdown content).
2. Applies validation rules:
   - **Confidence threshold check:** Any field with confidence < 0.60 is flagged for re-examination.
   - **Magnitude sanity check:** e.g., Assets >= Total Loans >= ACL >= Loans Written-Off; 2024 and 2023 values should be in a comparable range.
   - **Non-null check:** All 15 target fields must have values; missing fields are flagged.
   - **Unit normalization:** Converts Assets, Deposits, and Total Loans to **Billions CAD** and ACL/Loans Written-Off to **Millions CAD** using the extracted `ReportingCurrencyUnit`.
   - **Year-over-year sanity:** 2024 vs. 2023 values should not differ by more than ~30% (flags unusual swings for review).
   - **Metadata validation:** Province must be a valid Canadian province; DepositGuaranteeCorporation must match the Province.
   - **Structured data priority:** Values from structured financial tables are the highest-authority source. Never override a structured-table value with narrative or marketing text.
3. For flagged fields, the agent is given the raw markdown content from the document (provided by Content Understanding) and asked to re-examine the specific pages/tables to locate the correct value.
4. Returns a validated, normalized result with an audit trail of any corrections made.

**Prompt template (simplified):**

```
You are a financial data validation specialist. You have been given extracted 
financial data from a banking annual report along with the raw markdown content 
of the document.

EXTRACTED DATA:
{agent1_output}

RAW DOCUMENT MARKDOWN (relevant sections):
{markdown_content}

VALIDATION TASKS:
1. Check each field's confidence score. For any field below 0.60, search the 
   markdown content to verify or correct the value.
2. Ensure logical consistency:
   - Assets2024 >= TotalLoans2024 (and same for 2023)
   - TotalLoans2024 >= AllowanceForCreditLosses
   - AllowanceForCreditLosses >= LoansWrittenOff (typically)
   - 2024 vs 2023 values should be within ~30% of each other
3. Normalize monetary values:
   - Assets, Deposits, Total Loans → BILLIONS CAD
   - AllowanceForCreditLosses, LoansWrittenOff → MILLIONS CAD
   Use the ReportingCurrencyUnit field to determine conversion factor.
4. Validate metadata fields:
   - Province must be a valid Canadian province (PCU only)
   - DepositGuaranteeCorporation must match the province (PCU only)
   - CapitalRatio should be the Total Capital Ratio, a reasonable percentage (typically 10%–25%)
   - **Credit rating pass-through (Bank/FCU/Other only):** Pass through all
     credit rating fields (short_term_dbrs/sp/moodys, long_term_dbrs/sp/moodys)
     exactly as extracted by Agent 1. Do NOT re-interpret, correct, or override.
     If a rating is null, leave it null.
5. If a field is missing, search the markdown for the SAME metric in a
   structured financial table only. If no structured-table match exists,
   return null — do NOT infer from narrative or marketing text.
6. Structured data priority: ALWAYS prefer values from structured tables.
   Never replace a structured-table value with a number from narrative or
   marketing text. If narrative text contradicts a structured-table value,
   keep the structured value and add a warning.
7. Field source requirements: The following fields MUST come from structured
   tables only — capital_ratio, assets_2023/2024, deposits_2023/2024,
   total_loans_2023/2024, allowance_for_credit_losses, loans_written_off.
   If the extracted value came from narrative/marketing text, discard it and
   search the markdown for the same metric in a structured table.

Return a JSON object with:
- Validated and normalized values
- A "corrections" array listing any changes made and the reason
- A "warnings" array for any values you could not verify
```

**Why a separate agent?**
- Separation of concerns: extraction (Agent 1) is deterministic via Content Understanding; validation (Agent 2) applies reasoning.
- Using GPT-5 at `temperature=0.0` provides strong reasoning for detecting magnitude errors and cross-referencing financial figures while remaining deterministic.
- The “structured data preferred” rule prevents the model from overriding accurate table extractions with rounded marketing figures.
- The two-pass approach (extract → validate) catches errors that a single-pass extraction would miss, especially for large documents where financial statements span multiple pages/tables.

---

### 4.3 Agent 3 — Cross-Check & Output Assembly Agent

**Purpose:** Final quality gate. Cross-references the extracted and validated data, assembles the final JSON output, and generates an extraction confidence summary.

**Technology:** GPT-5 via Azure Foundry (same model as Agent 2, `temperature=0.0` for deterministic output).

**How it works:**

1. Receives Agent 2's validated output plus the original Content Understanding grounding metadata.
2. Performs a final cross-check:
   - Verifies the institution name matches the PDF filename.
   - Ensures both 2023 and 2024 fiscal year data is present.
   - Checks that figures are in a reasonable range for the institution type (PCU: $0.5B–$30B assets; Big 5 bank: $300B–$2T; smaller banks: $5B–$100B).
   - **PCU:** Validates Province ↔ Deposit Guarantee Corporation mapping.
   - **Bank/FCU/Other:** Verifies credit ratings are valid rating agency scales (note: this is a verification/flagging step — ratings are not modified from Agent 2's pass-through values unless clearly invalid).
   - Reviews Agent 2's corrections and warnings for any unresolved issues.
3. Assembles the final JSON in the target output schema.
4. Generates an `extractionQuality` summary (high/medium/low) based on overall confidence and the number of corrections applied.

**Final Output Schema (per PDF):**

*PCU example:*
```json
{
  "category": "pcu",
  "institution_name": "First Ontario Credit Union",
  "province": "Ontario",
  "member_of_ria": "No",
  "deposit_insurance_amount_guaranteed": {
    "value": "250,000 per depositor per category",
    "confidence": 0.88,
    "source_page": 5
  },
  "deposit_insurance_dbrs": {
    "value": "R-1 (low)",
    "confidence": 0.90,
    "source_page": 5
  },
  "deposit_insurance_guarantee_corporation": {
    "value": "DICO",
    "confidence": 0.92,
    "source_page": 5
  },
  "capital_ratio": {
    "value": 14.5,
    "unit": "%",
    "confidence": 0.92,
    "source_page": 8
  },
  "assets": {
    "2023": { "value": 6.8, "unit": "billion", "confidence": 0.95, "source_page": 45 },
    "2024": { "value": 7.2, "unit": "billion", "confidence": 0.96, "source_page": 45 }
  },
  "deposits": {
    "2023": { "value": 5.9, "unit": "billion", "confidence": 0.93, "source_page": 45 },
    "2024": { "value": 6.3, "unit": "billion", "confidence": 0.94, "source_page": 45 }
  },
  "total_loans": {
    "2023": { "value": 5.1, "unit": "billion", "confidence": 0.91, "source_page": 45 },
    "2024": { "value": 5.5, "unit": "billion", "confidence": 0.92, "source_page": 45 }
  },
  "allowance_for_credit_losses": {
    "value": 32.5,
    "unit": "millions",
    "confidence": 0.87,
    "source_page": 62
  },
  "loans_written_off": {
    "value": 18.2,
    "unit": "millions",
    "confidence": 0.80,
    "source_page": 64
  },
  "source_file": "2024 - First Ontario.pdf",
  "extracted_at": "2026-02-25T14:30:00Z",
  "extraction_quality": "high",
  "corrections": [],
  "warnings": []
}
```

*Bank/FCU/Other example:*
```json
{
  "category": "bank_fcu_other",
  "institution_name": "BMO Financial Group",
  "member_of_ria": "No",
  "short_term_dbrs": {
    "value": "R-1 (middle)",
    "confidence": 0.93,
    "source_page": 15
  },
  "short_term_sp": {
    "value": "A-1",
    "confidence": 0.91,
    "source_page": 15
  },
  "short_term_moodys": {
    "value": "P-1",
    "confidence": 0.90,
    "source_page": 15
  },
  "long_term_dbrs": {
    "value": "AA (low)",
    "confidence": 0.94,
    "source_page": 15
  },
  "long_term_sp": {
    "value": "A+",
    "confidence": 0.92,
    "source_page": 15
  },
  "long_term_moodys": {
    "value": "A1",
    "confidence": 0.91,
    "source_page": 15
  },
  "capital_ratio": {
    "value": 18.3,
    "unit": "%",
    "confidence": 0.91,
    "source_page": 12
  },
  "assets": {
    "2023": { "value": 1255.3, "unit": "billion", "confidence": 0.96, "source_page": 110 },
    "2024": { "value": 1411.0, "unit": "billion", "confidence": 0.97, "source_page": 110 }
  },
  "deposits": { "...same structure..." },
  "total_loans": { "...same structure..." },
  "allowance_for_credit_losses": { "...same structure..." },
  "loans_written_off": { "...same structure..." },
  "source_file": "2024 - BMO.pdf",
  "extracted_at": "2026-02-25T14:30:00Z",
  "extraction_quality": "high",
  "corrections": [],
  "warnings": []
}
```

---

## 5. Processing Pipeline — Step by Step

```
  ┌─ STEP 0: Agent 0 — Classify ────────────────────────────────────┐
  │  Load institution reference list from config/institutions.json  │
  │  For each PDF in input/, classify via GPT-4.1-mini              │
  │  Move files to input/pcu/, input/bank_fcu_other/,               │
  │    or input/unclassified/ (skipped)                             │
  │  Return classification manifest (filename → category)           │
  └─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
For each classified PDF (in pcu/ or bank_fcu_other/), with up to 3 retries (10s/20s backoff):

  ┌─ STEP 1: Upload ────────────────────────────────────────────────┐
  │  Upload PDF to Azure Blob Storage container                     │
  │  Generate SAS URL (read-only, 1-hour expiry)                    │
  └─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
  ┌─ STEP 2: Agent 1 — Extract ─────────────────────────────────────┐
  │  Select analyzer based on category:                             │
  │    PCU → pcu_annual_report                                      │
  │    Bank/FCU/Other → bank_fcu_other_annual_report                │
  │  POST to Content Understanding Analyze API                      │
  │  Poll for result (Operation-Location header)                    │
  │  Receive: fields, confidence, grounding, markdown               │
  └─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
  ┌─ STEP 3: Agent 2 — Validate ────────────────────────────────────┐
  │  Send Agent 1 output + markdown to GPT-5 (temp=0.0)             │
  │  Apply category-specific business rules, normalize units        │
  │  Enforce structured-data-preferred rule                         │
  │  Re-extract any low-confidence or missing fields                │
  │  Return: validated data + corrections + warnings                │
  └─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
  ┌─ STEP 4: Agent 3 — Cross-Check & Assemble ────────────────────┐
  │  Send Agent 2 output + grounding metadata to GPT-5              │
  │  Final cross-check (institution, period, magnitude)             │
  │  Produce final JSON in target schema                            │
  │  Assign extraction quality score                                │
  └─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
  ┌─ STEP 5: Write Output ──────────────────────────────────────────┐
  │  Write JSON file to output_folder/{institution_name}.json       │
  │  Write category summaries (JSON + CSV):                         │
  │    _summary_pcu.json / .csv                                     │
  │    _summary_bank_fcu_other.json / .csv                          │
  │  Write combined summary: _summary.json                          │
  └─────────────────────────────────────────────────────────────────┘
```

---

## 6. Azure Resource Setup

### 6.1 Required Azure Resources

| Resource | Purpose |
|---|---|
| **Microsoft Foundry Resource** | Hosts Content Understanding + model deployments |
| **Azure Blob Storage Account** | Staging area for PDF uploads (Content Understanding requires URL input) |

### 6.2 Model Deployments (in Foundry)

| Model | Deployment Name | Used By |
|---|---|---|
| GPT-4.1 | `gpt-4.1` | Agent 1 (Content Understanding field extraction) |
| GPT-5 | `gpt-5-chat` | Agent 2 (validation & normalization), Agent 3 (cross-check & output) |
| GPT-4.1-mini | `gpt-4.1-mini` | Agent 0 (classification) |
| text-embedding-3-large | `text-embedding-3-large` | Content Understanding (analyzer training) |

### 6.3 Content Understanding Analyzer Setup

1. Create the **two** custom analyzers by `PUT`ting each schema:
   ```
   PUT {endpoint}/contentunderstanding/analyzers/pcu_annual_report?api-version=2025-11-01
   PUT {endpoint}/contentunderstanding/analyzers/bank_fcu_other_annual_report?api-version=2025-11-01
   ```
2. Optionally improve accuracy with **analyzer training** — label 2–3 sample reports per category with correct field values using the training API (see `analyzer_training.ipynb` in the Azure samples repo).

### 6.4 IAM / Security

- Assign **Cognitive Services User** role on the Foundry resource to the identity running the script.
- Use **Azure Identity** (`DefaultAzureCredential`) for authentication — no API keys in code.
- Blob Storage SAS tokens scoped to read-only, short-lived (1 hour).

---

## 7. Project Structure

```
rj-pdf-processor-llm/
├── DESIGN.md                          # This document
├── DESIGN-UI.md                       # Dashboard / UI design document
├── dashboard/                         # Streamlit view-only dashboard (see DESIGN-UI.md)
│   ├── app.py                         # Home page — folder browser
│   └── pages/
│       ├── 2_PCU_Summary.py           # PCU CSV summary table
│       ├── 3_Bank_Summary.py          # Bank/FCU/Other CSV summary table
│       ├── 4_Institution_Detail.py    # Field-by-field viewer + PDF page preview
│       └── 5_Log_Viewer.py            # Pipeline log viewer
├── config/
│   ├── settings.yaml                  # Azure endpoints, model names, thresholds
│   └── institutions.json              # Institution reference list (PCU vs Bank/FCU/Other)
├── analyzers/
│   ├── pcu_annual_report.json         # Content Understanding analyzer — PCU fields
│   └── bank_fcu_other_annual_report.json  # Content Understanding analyzer — Bank/FCU/Other fields
├── src/
│   ├── __init__.py
│   ├── main.py                        # Entry point — orchestrator loop
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── classification_agent.py    # Agent 0: PDF → institution category classification
│   │   ├── extraction_agent.py        # Agent 1: Content Understanding calls
│   │   ├── validation_agent.py        # Agent 2: GPT-5 validation & normalization
│   │   └── output_agent.py            # Agent 3: GPT-5 cross-check & assembly
│   ├── services/
│   │   ├── __init__.py
│   │   ├── blob_storage.py            # Upload PDFs, generate SAS URLs
│   │   └── content_understanding.py   # Content Understanding API wrapper
│   └── models/
│       ├── __init__.py
│       └── schemas.py                 # Pydantic models for extracted data
├── input/                             # Place PDF files here (flat)
│   ├── pcu/                           # Auto-populated by Agent 0 — PCU PDFs
│   ├── bank_fcu_other/                # Auto-populated by Agent 0 — Bank/FCU/Other PDFs
│   └── unclassified/                  # Auto-populated by Agent 0 — unmatched PDFs (skipped)
├── output/                            # JSON results + summary JSON/CSV files
├── requirements.txt
├── .env.sample                        # Environment variable template
└── README.md
```

---

## 8. Key Python Dependencies

```
azure-identity>=1.16.0
azure-storage-blob>=12.20.0
azure-ai-projects>=1.0.0
openai>=1.40.0
pydantic>=2.0.0
python-dotenv>=1.0.0
pyyaml>=6.0
```

---

## 9. Cost Estimation

| Component | Cost Driver | Estimate per PDF |
|---|---|---|
| Agent 0 (GPT-4.1-mini) | Classification tokens | ~$0.001 (one-time per batch) |
| Content Understanding | Document pages processed + GPT-4.1 tokens | ~$0.50–$2.00 (50–300 pages) |
| Agent 2 (GPT-5) | Input/output tokens | ~$0.05–$0.20 |
| Agent 3 (GPT-5) | Input/output tokens | ~$0.05–$0.20 |
| Blob Storage | Storage + transactions | < $0.01 |
| **Total per PDF** | | **~$0.60–$2.40** |
| **Total for 20+ PDFs** | | **~$12–$48** |

> Note: Content Understanding is the dominant cost. Costs vary with page count; credit union reports (shorter) will cost less than Big 5 bank reports (longer). The `estimateFieldSourceAndConfidence` feature uses additional contextualization tokens.

---

## 10. Handling Edge Cases

| Edge Case | Mitigation |
|---|---|
| **Field not found** | Agent 2 searches markdown for alternate terms (e.g., "Net Loans" vs. "Total Loans", "Member deposits" vs. "Total deposits"). Agent 3 flags `extractionQuality: "low"` if still missing. |
| **Multiple candidate values** (e.g., gross vs. net loans) | Analyzer description specifies preferred term. Agent 2 uses balance-sheet context to select the correct figure. |
| **Different fiscal year-ends** | Most credit unions use Dec 31. Agent 1 extracts comparative columns; Agent 2 verifies both 2023 and 2024 are present. |
| **Values in different units** (thousands vs. millions vs. billions) | Agent 1 extracts `ReportingCurrencyUnit`. Agent 2 normalizes Assets/Deposits/Loans to Billions and ACL/Write-Offs to Millions CAD. |
| **Multi-page tables** | Content Understanding handles multi-page table detection. `tableFormat: "html"` preserves continuity. |
| **Low confidence extraction** | Agent 2 re-examines the markdown. If confidence remains low after re-extraction, the warning propagates to the final output. |
| **Scanned / image-based PDFs** | Content Understanding includes OCR. No special handling needed. |
| **DBRS rating not in PDF** | Not all credit unions are rated by DBRS. Agent 2 marks as "Not Rated" if not found; Agent 3 preserves this in the output with a note. |
| **Province / Deposit Guarantee Corp not stated explicitly** | Agent 1 uses `method: "generate"` to infer from the institution name, address, or charter. Agent 2 cross-validates the Province ↔ Guarantee Corp mapping. |
| **2023 comparative data missing** | Some smaller reports may not include prior-year comparatives. Agent 2 flags these; Agent 3 outputs null with a warning. |

---

## 11. Future Enhancements

1. **Analyzer Training:** Label 3–5 sample reports with correct values using the Content Understanding training API to significantly improve extraction accuracy for this specific document type.
2. **Batch Processing API:** When Content Understanding releases batch endpoints, switch from sequential per-PDF calls to batch mode.
3. **Extended Historical Comparison:** Extract additional prior years (2022, 2021) for multi-year trend analysis.
4. **Azure Functions Deployment:** Move from local script to Azure Functions with Blob trigger for automated processing when new PDFs are dropped into storage.
5. **Streamlit Dashboard:** A view-only web dashboard for reviewing extraction results, browsing summary tables, and inspecting individual fields with PDF page rendering. See [DESIGN-UI.md](DESIGN-UI.md) for full details.
6. **Power BI Integration:** Output to a format (CSV/Parquet) suitable for direct Power BI ingestion.
7. **Consolidated Summary Report:** Agent 3 produces a master JSON/CSV with all credit unions' data side-by-side.
8. **Province ↔ Guarantee Corp Lookup Table:** Maintain a reference table to validate generated metadata fields without LLM calls.
9. **RIA Membership Registry Integration:** Cross-reference extracted RIA membership against an authoritative registry for validation.

---

## 12. Summary

This design uses **four LLM agents** in a sequential pipeline:

| Agent | Role | Model | Purpose |
|---|---|---|---|
| **Agent 0** | Classification | GPT-4.1-mini (via Foundry) | Match PDF filenames to institution list, determine PCU vs Bank/FCU/Other category |
| **Agent 1** | Document Extraction | GPT-4.1 (via Content Understanding) | OCR + layout + field extraction using category-specific analyzer |
| **Agent 2** | Validation & Normalization | GPT-5 (via Foundry, temp=0.0) | Business rule checks, structured-data-preferred enforcement, unit normalization, re-extraction |
| **Agent 3** | Cross-Check & Output | GPT-5 (via Foundry, temp=0.0) | Final quality gate, schema assembly, quality scoring, credit rating pass-through |

The pipeline first classifies PDFs into two institution categories (Provincial Credit Unions vs. Banks/Federal Credit Unions/Others), each with its own field schema and Content Understanding analyzer. Each classified PDF is processed through Agents 1–3 with category-appropriate extraction, validation, and output logic. Failed PDFs are retried up to 3 times with exponential backoff (10s, 20s). Results are written as individual JSON files per institution, per-category summaries in both JSON and CSV formats (`_summary_pcu.json/.csv` and `_summary_bank_fcu_other.json/.csv`), and a combined summary (`_summary.json`).
