# RJ PDF Processor LLM

A 3-agent pipeline that extracts financial data from provincial credit union annual reports (PDF) and outputs structured JSON.

Built on **Azure AI Foundry**, **Content Understanding**, and **Azure OpenAI**.

---

## Architecture

![Architecture Diagram](docs/images/Simple%20Architecture%20Diagram.png)

| Agent   | Model (Foundry deployment)        | Agentic?         | Role                                                                 |
| ------- | --------------------------------- | ---------------- | -------------------------------------------------------------------- |
| Agent 0 | `GPT_41_MINI_DEPLOYMENT`          | ✅ tool-using    | Classify PDFs into PCU vs Bank/FCU/Other. Can read the PDF's first pages (`read_document_text`) when the filename is ambiguous instead of giving up. |
| Agent 1 | Content Understanding analyzer    | ❌ deterministic | OCR + field extraction via a custom analyzer. A managed-service wrapper — no LLM call of its own, so left as-is. |
| Agent 2 | `GPT_41_DEPLOYMENT` (temp=0.0)    | ✅ tool-using    | Validate & normalise. Searches the **full** document (`search_document`) and computes exact unit conversions (`calculate`) in a reason→act loop. |
| Agent 3 | `GPT_41_DEPLOYMENT` (temp=0.0)    | ✅ tool-using    | Final cross-check — verifies implausible figures against the source (`search_document`); the JSON is then assembled deterministically. |

> **Deployment names:** the `*_DEPLOYMENT` variables point to whatever you deploy in Foundry. `.env.sample` maps `GPT_41_DEPLOYMENT` to a `gpt-5-chat` deployment, so "GPT-4.1" here refers to the variable, not necessarily the model.

### Agentic design

Agents 0, 2, and 3 are genuinely *agentic*: each drives its model in a tool-calling
loop (reason → act → observe) via `src/agents/agentic.py`, calling tools and feeding
results back until it is ready to answer, then emitting schema-valid JSON. Shared tools:

| Tool | Used by | Purpose |
|------|---------|---------|
| `search_document` | Agents 2, 3 | Retrieve evidence from the **full** source document on demand (not a truncated dump) |
| `calculate` | Agent 2 | Exact arithmetic for unit conversions and consistency checks |
| `read_document_text` | Agent 0 | Read the PDF's first pages to disambiguate an unclear filename |

Agent 1 stays deterministic by design — it wraps the managed Content Understanding
service, where making it "agentic" would add no value. Loop depth is bounded by
`AGENT_MAX_ITERATIONS` (default 6).

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
| `AGENT_MAX_ITERATIONS` | Max tool-calling iterations per agentic step (default `6`) |

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
│   │   ├── agentic.py               # Shared agentic loop + tools (search/calculate/read-pdf)
│   │   ├── classification_agent.py  # Agent 0 — agentic classifier (read-pdf tool)
│   │   ├── extraction_agent.py      # Agent 1 — Content Understanding extraction (deterministic)
│   │   ├── validation_agent.py      # Agent 2 — agentic validator (search + calculate tools)
│   │   └── output_agent.py          # Agent 3 — agentic cross-check + deterministic assembly
│   ├── models/
│   │   └── schemas.py               # Pydantic data models
│   └── services/
│       ├── blob_storage.py          # Azure Blob Storage wrapper
│       └── content_understanding.py # Content Understanding REST client
├── tests/
│   └── test_agentic.py              # Tests for the agentic loop + tools (no Azure needed)
├── .env.sample
├── DESIGN.md
├── README.md
├── requirements.txt
└── requirements-dev.txt
```

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite (`tests/test_agentic.py`) covers the agentic loop and tools using a fake
LLM client, so it runs without Azure credentials.

---

## Design Document

See [DESIGN.md](DESIGN.md) for the full architecture, agent prompts, schema definitions, and edge-case handling strategy.
