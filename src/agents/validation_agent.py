"""Agent 2 — Validation & Normalization Agent.

Uses GPT-4.1-mini to validate extracted data against business rules,
normalise monetary units, and re-extract low-confidence or missing fields.

Supports category-specific prompts for PCU vs Bank/FCU/Other.
"""

from __future__ import annotations

import json
import logging
import os
from textwrap import dedent

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from src.models.schemas import (
    Correction,
    RawExtractionResult,
    ValidatedResult,
    Warning,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- #
#  System / user prompt templates
# --------------------------------------------------------------------- #

_SYSTEM_PROMPT_PCU = dedent("""\
    You are a financial data validation specialist for Canadian provincial
    credit unions.  You are given:
      1. Extracted field data (JSON) from a credit union annual report.
      2. The raw markdown text of the document.

    Your job is to:
      • Verify every extracted value.
      • Flag any field whose confidence is below 0.60 and attempt to find
        the correct value in the markdown.
      • Ensure logical consistency among financial figures.
      • Normalise monetary values to the required units.
      • Validate metadata fields.

    CRITICAL RULE — structured data preferred:
      Always prefer values that come from structured financial tables
      (balance sheet, income statement, financial highlights, audited
      statements).  If the extracted value came from a structured table,
      keep it — do NOT override it with narrative or marketing text.
      If a field is null, you may fill it ONLY from a structured table.
      If no structured-table source exists, return null rather than guess.

    ALWAYS respond with valid JSON matching the schema described in the
    user prompt.  Do NOT include markdown fences or commentary outside
    the JSON.
""")

_SYSTEM_PROMPT_BANK = dedent("""\
    You are a financial data validation specialist for Canadian banks,
    federal credit unions, and other financial institutions.  You are given:
      1. Extracted field data (JSON) from an institution's annual report.
      2. The raw markdown text of the document.

    Your job is to:
      • Verify every extracted value.
      • Flag any field whose confidence is below 0.60 and attempt to find
        the correct value in the markdown.
      • Ensure logical consistency among financial figures.
      • Normalise monetary values to the required units.
      • Validate credit rating fields.

    CRITICAL RULE — structured data preferred:
      Always prefer values that come from structured financial tables
      (balance sheet, income statement, financial highlights, audited
      statements).  If the extracted value came from a structured table,
      keep it — do NOT override it with narrative or marketing text.
      If a field is null, you may fill it ONLY from a structured table.
      If no structured-table source exists, return null rather than guess.

    ALWAYS respond with valid JSON matching the schema described in the
    user prompt.  Do NOT include markdown fences or commentary outside
    the JSON.
""")

_USER_PROMPT_PCU = dedent("""\
    ## Extracted Data (Agent 1 output)
    ```json
    {extracted_json}
    ```

    ## Raw Document Markdown (truncated to key sections)
    ```
    {markdown}
    ```

    ## Field Source Requirements

    The following fields MUST be sourced from a structured financial table
    (balance sheet, income statement, financial highlights table, capital
    adequacy table, ACL rollforward table, or audited schedule).  If the
    extracted value came from narrative text, an executive summary, or
    marketing content, discard it — search the markdown for the same metric
    in a structured table.  If no table source exists, return null.

    **Table-only fields:** capital_ratio, assets_2023_billion,
    assets_2024_billion, deposits_2023_billion, deposits_2024_billion,
    total_loans_2023_billion, total_loans_2024_billion,
    allowance_for_credit_losses_mm, loans_written_off_mm

    **Text-acceptable fields (tables or prose):** institution_name,
    province, member_of_ria, deposit_insurance_amount_guaranteed,
    deposit_insurance_dbrs, deposit_insurance_guarantee_corporation

    ## Validation & Normalisation Tasks

    1. **Confidence check** — For any field with confidence < 0.60, search the
       markdown to verify or correct the value.

    2. **Logical consistency** —
       - Assets2024 >= TotalLoans2024  (and same for 2023)
       - TotalLoans >= AllowanceForCreditLosses
       - AllowanceForCreditLosses >= LoansWrittenOff  (typically)
       - 2024 vs 2023 values for each metric should be within ~30% of each
         other; flag large deviations.

    3. **Unit normalisation** —
       The reporting currency unit extracted is: "{currency_unit}".
       Convert every monetary value so that:
         • Assets, Deposits, Total Loans → **Billions CAD**
         • AllowanceForCreditLosses, LoansWrittenOff → **Millions CAD**
       Show the converted number (e.g. if reported as 7200 in millions,
       assets should become 7.2 billion).

    4. **Metadata validation** —
       - Province must be a valid Canadian province.
       - DepositInsuranceGuaranteeCorporation must align with the province.
         Known mapping: {province_map}
       - CapitalRatio must be the Total Capital Ratio (total capital / risk-weighted assets),
         NOT CET1 or leverage ratio.  Typical range is 10%–25%.  Flag outliers.
       - DepositInsuranceDBRS is the DBRS rating of the deposit guarantee
         corporation, NOT the credit union itself.

    5. **Missing fields** — If a value is null or absent, search the markdown
       for the SAME metric in a structured financial table (balance sheet,
       income statement, financial highlights table).  Structured-table
       values are authoritative and should be used.  If no structured-table
       match exists, return null and add a warning.

    6. **Structured data priority** —
       Values from structured financial tables (balance sheet, income
       statement, financial highlights, audited statements) are the
       highest-authority source.  ALWAYS keep a structured-table value
       unless a DIFFERENT structured table provides a more specific or
       more recent figure for the SAME metric.  NEVER replace a
       structured-table value with a number from narrative or marketing
       text — those are often rounded, cover different scopes, or refer
       to different metrics (e.g. "loans outstanding" vs "gross loans",
       "combined AUM" vs "total assets").  If narrative text contradicts
       a structured-table value, keep the structured value and add a
       warning noting the discrepancy.

    ## Required Response Schema (JSON only, no markdown fences)

    {{
      "institution_name": "<string>",
      "province": "<string>",
      "member_of_ria": "<Yes|No>",
      "deposit_insurance_amount_guaranteed": "<string>",
      "deposit_insurance_dbrs": "<string or 'Not Rated'>",
      "deposit_insurance_guarantee_corporation": "<string>",
      "capital_ratio": <number or null>,
      "assets_2023_billion": <number or null>,
      "assets_2024_billion": <number or null>,
      "deposits_2023_billion": <number or null>,
      "deposits_2024_billion": <number or null>,
      "total_loans_2023_billion": <number or null>,
      "total_loans_2024_billion": <number or null>,
      "allowance_for_credit_losses_mm": <number or null>,
      "loans_written_off_mm": <number or null>,
      "corrections": [
        {{"field": "<name>", "original_value": ..., "corrected_value": ..., "reason": "<why>"}}
      ],
      "warnings": [
        {{"field": "<name>", "message": "<description>"}}
      ]
    }}
""")

_USER_PROMPT_BANK = dedent("""\
    ## Extracted Data (Agent 1 output)
    ```json
    {extracted_json}
    ```

    ## Raw Document Markdown (truncated to key sections)
    ```
    {markdown}
    ```

    ## Field Source Requirements

    The following fields MUST be sourced from a structured financial table
    (balance sheet, income statement, financial highlights table, capital
    adequacy table, ACL rollforward table, or audited schedule).  If the
    extracted value came from narrative text, an executive summary, or
    marketing content, discard it — search the markdown for the same metric
    in a structured table.  If no table source exists, return null.

    **Table-only fields:** capital_ratio, assets_2023_billion,
    assets_2024_billion, deposits_2023_billion, deposits_2024_billion,
    total_loans_2023_billion, total_loans_2024_billion,
    allowance_for_credit_losses_mm, loans_written_off_mm

    **Text-acceptable fields (tables or prose):** institution_name,
    member_of_ria, short_term_dbrs, short_term_sp, short_term_moodys,
    long_term_dbrs, long_term_sp, long_term_moodys

    ## Validation & Normalisation Tasks

    1. **Confidence check** — For any field with confidence < 0.60, search the
       markdown to verify or correct the value.

    2. **Logical consistency** —
       - Assets2024 >= TotalLoans2024  (and same for 2023)
       - TotalLoans >= AllowanceForCreditLosses
       - AllowanceForCreditLosses >= LoansWrittenOff  (typically)
       - 2024 vs 2023 values for each metric should be within ~30% of each
         other; flag large deviations.

    3. **Unit normalisation** —
       The reporting currency unit extracted is: "{currency_unit}".
       Convert every monetary value so that:
         • Assets, Deposits, Total Loans → **Billions CAD**
         • AllowanceForCreditLosses, LoansWrittenOff → **Millions CAD**
       Show the converted number (e.g. if reported as 1,411,043 in millions,
       assets should become 1411.043 billion).

    4. **Credit ratings & capital ratio** —
       - **Pass through all credit rating fields exactly as extracted.**
         Do NOT re-interpret, correct, or override short_term_dbrs,
         short_term_sp, short_term_moodys, long_term_dbrs, long_term_sp,
         or long_term_moodys.  Keep the original values unchanged.
       - If a rating is null, leave it null (not all institutions are
         rated by all agencies).
       - CapitalRatio must be the Total Capital Ratio (total capital / risk-weighted assets),
         NOT CET1 or leverage ratio.  Typical range is 10%–25%.  Flag outliers.

    5. **Missing fields** — If a value is null or absent, search the markdown
       for the SAME metric in a structured table (balance sheet, income
       statement, financial highlights table, or credit ratings table).
       Structured-table values are authoritative and should be used.
       If no structured-table match exists, return null and add a warning.

    6. **Structured data priority** —
       Values from structured financial tables (balance sheet, income
       statement, financial highlights, credit ratings tables, audited
       statements) are the highest-authority source.  ALWAYS keep a
       structured-table value unless a DIFFERENT structured table provides
       a more specific or more recent figure for the SAME metric.  NEVER
       replace a structured-table value with a number from narrative or
       marketing text — those are often rounded, cover different scopes,
       or refer to different metrics (e.g. "loans outstanding" vs "gross
       loans", "combined AUM" vs "total assets").  If narrative text
       contradicts a structured-table value, keep the structured value
       and add a warning noting the discrepancy.

    ## Required Response Schema (JSON only, no markdown fences)

    {{
      "institution_name": "<string>",
      "member_of_ria": "<Yes|No>",
      "short_term_dbrs": "<string or null>",
      "short_term_sp": "<string or null>",
      "short_term_moodys": "<string or null>",
      "long_term_dbrs": "<string or null>",
      "long_term_sp": "<string or null>",
      "long_term_moodys": "<string or null>",
      "capital_ratio": <number or null>,
      "assets_2023_billion": <number or null>,
      "assets_2024_billion": <number or null>,
      "deposits_2023_billion": <number or null>,
      "deposits_2024_billion": <number or null>,
      "total_loans_2023_billion": <number or null>,
      "total_loans_2024_billion": <number or null>,
      "allowance_for_credit_losses_mm": <number or null>,
      "loans_written_off_mm": <number or null>,
      "corrections": [
        {{"field": "<name>", "original_value": ..., "corrected_value": ..., "reason": "<why>"}}
      ],
      "warnings": [
        {{"field": "<name>", "message": "<description>"}}
      ]
    }}
""")


# --------------------------------------------------------------------- #
#  Helper: serialiser for the raw extraction
# --------------------------------------------------------------------- #

def _raw_to_dict(raw: RawExtractionResult) -> dict:
    """Serialise a RawExtractionResult into a plain dict for the prompt."""
    out: dict = {}
    for attr in raw.model_fields:
        if attr in ("markdown_content", "source_file"):
            continue
        val = getattr(raw, attr)
        if val is None:
            out[attr] = None
        elif hasattr(val, "model_dump"):
            out[attr] = val.model_dump()
        else:
            out[attr] = val
    return out


def _truncate_markdown(md: str, max_chars: int = 80_000) -> str:
    """Truncate markdown to a sensible size for the LLM context window."""
    if len(md) <= max_chars:
        return md
    half = max_chars // 2
    return md[:half] + "\n\n…[TRUNCATED]…\n\n" + md[-half:]


# --------------------------------------------------------------------- #
#  Agent class
# --------------------------------------------------------------------- #

class ValidationAgent:
    """Agent 2: validates and normalises Agent 1's raw extraction."""

    def __init__(
        self,
        credential: DefaultAzureCredential | None = None,
        province_guarantee_map: dict[str, str] | None = None,
    ) -> None:
        credential = credential or DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        self.client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_AI_ENDPOINT"],
            azure_ad_token_provider=token_provider,
            api_version="2025-01-01-preview",
        )
        self.deployment = os.environ.get("GPT_41_DEPLOYMENT", "gpt-4.1")

        # Province → Guarantee Corp mapping for the prompt
        self.province_map = province_guarantee_map or {
            "Ontario": "DICO",
            "British Columbia": "CUDGC",
            "Alberta": "CUDIC",
            "Saskatchewan": "CUDGC SK",
            "Manitoba": "DGCM",
            "Quebec": "ASDQ",
            "New Brunswick": "NBCUDIC",
            "Nova Scotia": "NSCUDIC",
        }

    def validate(self, raw: RawExtractionResult) -> ValidatedResult:
        """Send the raw extraction to GPT-4.1-mini for validation."""
        logger.info(
            "=== Agent 2: Validating '%s' (category=%s) ===",
            raw.source_file,
            raw.category,
        )

        extracted_json = json.dumps(_raw_to_dict(raw), indent=2, default=str)
        markdown = _truncate_markdown(raw.markdown_content or "")

        currency_unit = "unknown"
        if raw.reporting_currency_unit and raw.reporting_currency_unit.value:
            currency_unit = str(raw.reporting_currency_unit.value)

        # Select category-specific prompts
        if raw.category == "pcu":
            system_prompt = _SYSTEM_PROMPT_PCU
            user_prompt = _USER_PROMPT_PCU.format(
                extracted_json=extracted_json,
                markdown=markdown,
                currency_unit=currency_unit,
                province_map=json.dumps(self.province_map),
            )
        else:
            system_prompt = _SYSTEM_PROMPT_BANK
            user_prompt = _USER_PROMPT_BANK.format(
                extracted_json=extracted_json,
                markdown=markdown,
                currency_unit=currency_unit,
            )

        response = self.client.chat.completions.create(
            model=self.deployment,
            temperature=0.0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or "{}"
        logger.debug("Agent 2 raw response: %s", content[:500])

        return self._parse_response(content, raw.category)

    # ------------------------------------------------------------------ #
    #  Response parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_response(content: str, category: str = "pcu") -> ValidatedResult:
        """Parse the JSON response from GPT-4.1-mini into ValidatedResult."""
        data = json.loads(content)

        corrections = [
            Correction(**c) for c in data.get("corrections", [])
        ]
        warnings = [
            Warning(**w) for w in data.get("warnings", [])
        ]

        result = ValidatedResult(
            category=category,
            institution_name=data.get("institution_name"),
            member_of_ria=data.get("member_of_ria"),
            capital_ratio=data.get("capital_ratio"),
            assets_2023_billion=data.get("assets_2023_billion"),
            assets_2024_billion=data.get("assets_2024_billion"),
            deposits_2023_billion=data.get("deposits_2023_billion"),
            deposits_2024_billion=data.get("deposits_2024_billion"),
            total_loans_2023_billion=data.get("total_loans_2023_billion"),
            total_loans_2024_billion=data.get("total_loans_2024_billion"),
            allowance_for_credit_losses_mm=data.get("allowance_for_credit_losses_mm"),
            loans_written_off_mm=data.get("loans_written_off_mm"),
            corrections=corrections,
            warnings=warnings,
        )

        if category == "pcu":
            result.province = data.get("province")
            result.deposit_insurance_amount_guaranteed = data.get("deposit_insurance_amount_guaranteed")
            result.deposit_insurance_dbrs = data.get("deposit_insurance_dbrs")
            result.deposit_insurance_guarantee_corporation = data.get("deposit_insurance_guarantee_corporation")
        else:
            result.short_term_dbrs = data.get("short_term_dbrs")
            result.short_term_sp = data.get("short_term_sp")
            result.short_term_moodys = data.get("short_term_moodys")
            result.long_term_dbrs = data.get("long_term_dbrs")
            result.long_term_sp = data.get("long_term_sp")
            result.long_term_moodys = data.get("long_term_moodys")

        return result
