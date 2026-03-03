# RJ PDF Processor LLM

A 3-agent pipeline that extracts financial data from provincial credit union annual reports (PDF) and outputs structured JSON.

Built on **Azure AI Foundry**, **Content Understanding**, and **Azure OpenAI**.

---

## Architecture

```
PDF → Blob Storage → Agent 1 (Extraction) → Agent 2 (Validation) → Agent 3 (Output) → JSON
```

| Agent   | Model         | Role                                              |
| ------- | ------------- | ------------------------------------------------- |
| Agent 1 | GPT-4.1 + Content Understanding | OCR + field extraction via custom analyzer |
| Agent 2 | GPT-4.1-mini  | Validate, normalise units, flag corrections        |
| Agent 3 | GPT-4.1       | Cross-check, assign confidence, produce final JSON |

## Fields Extracted

| # | Field | Unit |
|---|-------|------|
| 1 | Provincial Credit Union | Name |
| 2 | Province | Province code |
| 3 | Member of RIA | Yes / No |
| 4 | Amount Guaranteed | Currency |
| 5 | DBRS Rating | Rating string |
| 6 | Deposit Guarantee Corporation | Name |
| 7 | Total Capital Ratio | Percentage |
| 8–9 | Assets (2023 / 2024) | Billions |
| 10–11 | Deposits (2023 / 2024) | Billions |
| 12–13 | Total Loans (2023 / 2024) | Billions |
| 14 | Allowance for Credit Losses | Millions |
| 15 | Loans Written Off | Millions |

---

## Prerequisites

1. **Python 3.11+**
2. **Azure AI Foundry** resource with these model deployments:
   - `gpt-4.1`
   - `gpt-4.1-mini`
   - `text-embedding-3-large`
3. **Content Understanding** enabled on the Foundry resource
4. **Azure Blob Storage** account + container (default: `pdf-uploads`)
5. Role assignment: your identity needs **Cognitive Services User** on the Foundry resource

---

## Setup

```bash
# 1. Clone & enter project
cd rj-pdf-processor-llm

# 2. Create & activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy .env.sample → .env and fill in your values
copy .env.sample .env         # Windows
# cp .env.sample .env         # macOS / Linux
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `AZURE_AI_ENDPOINT` | Azure AI Foundry endpoint (e.g. `https://<name>.services.ai.azure.com/`) |
| `AZURE_STORAGE_ACCOUNT_URL` | Blob Storage account URL (e.g. `https://<account>.blob.core.windows.net`) |
| `AZURE_STORAGE_CONTAINER` | Container name (default `pdf-uploads`) |
| `GPT_41_DEPLOYMENT` | GPT-4.1 deployment name |
| `GPT_41_MINI_DEPLOYMENT` | GPT-4.1-mini deployment name |
| `ANALYZER_ID` | Content Understanding analyzer name |
| `CONFIDENCE_THRESHOLD` | Minimum confidence to accept a value (default `0.60`) |
| `POLL_INTERVAL_SECONDS` | Polling interval for async analysis (default `5`) |
| `POLL_TIMEOUT_SECONDS` | Max wait time for analysis (default `600`) |

> **Authentication:** This project uses `DefaultAzureCredential` from the Azure Identity SDK — no API keys needed. Ensure you're logged in via `az login`, or running under a managed identity with the appropriate role assignments (see below).

#### Required Role Assignments

| Resource | Role |
|----------|------|
| Azure AI Foundry resource | **Cognitive Services User** |
| Azure Storage account | **Storage Blob Data Contributor** |

---

## Usage

1. Place your annual report PDFs in the `input/` folder.
2. Run the pipeline:

```bash
python -m src.main
```

3. Results are written to the `output/` folder:
   - One JSON file per institution (e.g. `Affinity Credit Union.json`)
   - `_summary.json` — consolidated summary of all results

### Example Output (per institution)

```json
{
  "provincial_credit_union": "Affinity Credit Union",
  "province": "SK",
  "member_of_ria": "Yes",
  "amount_guaranteed": "Fully Guaranteed",
  "dbrs": "N/A",
  "deposit_guarantee_corporation": "Credit Union Deposit Guarantee Corporation – Saskatchewan",
  "total_capital_ratio": { "value": "14.2%", "confidence": 0.95, "page": 42 },
  "assets": {
    "2023": { "value": "9.8B", "confidence": 0.92, "page": 15 },
    "2024": { "value": "10.3B", "confidence": 0.94, "page": 15 }
  },
  "deposits": { ... },
  "total_loans": { ... },
  "allowance_for_credit_losses": { "value": "45.2M", "confidence": 0.88, "page": 51 },
  "loans_written_off": { "value": "12.1M", "confidence": 0.85, "page": 51 },
  "extraction_quality": "high",
  "corrections": [],
  "warnings": [],
  "source_file": "2024 - Affinity CU.pdf"
}
```

---

## Project Structure

```
rj-pdf-processor-llm/
├── analyzers/
│   └── banking-annual-report.json   # Content Understanding custom analyzer schema
├── config/
│   └── settings.yaml                # Runtime settings & province mapping
├── input/                           # Drop PDF annual reports here
├── output/                          # JSON results written here
├── src/
│   ├── main.py                      # Pipeline orchestrator (entry point)
│   ├── agents/
│   │   ├── extraction_agent.py      # Agent 1 — Content Understanding extraction
│   │   ├── validation_agent.py      # Agent 2 — GPT-4.1-mini validation
│   │   └── output_agent.py          # Agent 3 — GPT-4.1 cross-check & assembly
│   ├── models/
│   │   └── schemas.py               # Pydantic data models
│   └── services/
│       ├── blob_storage.py          # Azure Blob Storage wrapper
│       └── content_understanding.py # Content Understanding REST client
├── .env.sample
├── DESIGN.md
├── README.md
└── requirements.txt
```

---

## Design Document

See [DESIGN.md](DESIGN.md) for the full architecture, agent prompts, schema definitions, and edge-case handling strategy.
