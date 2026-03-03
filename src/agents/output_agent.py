"""Agent 3 — Cross-Check & Output Assembly Agent.

Uses GPT-4.1 to perform a final quality gate on the validated data, then
assembles the canonical JSON output for each institution.

Supports category-specific prompts for PCU vs Bank/FCU/Other.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from textwrap import dedent

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from src.models.schemas import (
    ConfidenceValue,
    Correction,
    FinalOutput,
    RawExtractionResult,
    StringConfidenceValue,
    ValidatedResult,
    Warning,
    YearPairValue,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- #
#  Prompts
# --------------------------------------------------------------------- #

_SYSTEM_PROMPT_PCU = dedent("""\
    You are a senior financial analyst performing a final quality review of
    extracted data from a Canadian provincial credit union annual report.

    You will receive:
      1. The validated/normalised data from a prior agent (JSON).
      2. Metadata about the source file and extraction confidence.

    Your tasks:
      • Cross-check that the credit union name is plausible given the
        source filename.
      • Verify both 2023 and 2024 data is present for Assets, Deposits,
        and Total Loans.
      • Confirm figures are in a reasonable range for a provincial credit
        union (e.g. assets typically $0.5B – $30B).
      • Validate Province ↔ Deposit Guarantee Corporation mapping.
      • Assign an overall extraction_quality score:
          "high"   – all fields present, no corrections, confidence ≥ 0.75
          "medium" – minor corrections or 1-2 missing fields
          "low"    – major corrections, multiple missing fields, or
                     confidence issues
      • Produce any additional corrections or warnings.

    Respond with valid JSON only (no markdown fences).
""")

_SYSTEM_PROMPT_BANK = dedent("""\
    You are a senior financial analyst performing a final quality review of
    extracted data from a Canadian bank / federal credit union / other
    financial institution annual report.

    You will receive:
      1. The validated/normalised data from a prior agent (JSON).
      2. Metadata about the source file and extraction confidence.

    Your tasks:
      • Cross-check that the institution name is plausible given the
        source filename.
      • Verify both 2023 and 2024 data is present for Assets, Deposits,
        and Total Loans.
      • Confirm figures are in a reasonable range for the institution type
        (e.g. Big 5 bank assets $300B – $2T; smaller banks $5B – $100B).
      • Verify credit ratings are valid rating agency scales.
      • Assign an overall extraction_quality score:
          "high"   – all fields present, no corrections, confidence ≥ 0.75
          "medium" – minor corrections or 1-2 missing fields
          "low"    – major corrections, multiple missing fields, or
                     confidence issues
      • Produce any additional corrections or warnings.

    Respond with valid JSON only (no markdown fences).
""")

_USER_PROMPT_PCU = dedent("""\
    ## Source File
    {source_file}

    ## Validated Data (Agent 2 output)
    ```json
    {validated_json}
    ```

    ## Agent 2 Corrections So Far
    {corrections_json}

    ## Agent 2 Warnings So Far
    {warnings_json}

    ## Required Response Schema

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
      "extraction_quality": "<high|medium|low>",
      "additional_corrections": [
        {{"field": "<name>", "original_value": ..., "corrected_value": ..., "reason": "<why>"}}
      ],
      "additional_warnings": [
        {{"field": "<name>", "message": "<description>"}}
      ]
    }}
""")

_USER_PROMPT_BANK = dedent("""\
    ## Source File
    {source_file}

    ## Validated Data (Agent 2 output)
    ```json
    {validated_json}
    ```

    ## Agent 2 Corrections So Far
    {corrections_json}

    ## Agent 2 Warnings So Far
    {warnings_json}

    ## Required Response Schema

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
      "extraction_quality": "<high|medium|low>",
      "additional_corrections": [
        {{"field": "<name>", "original_value": ..., "corrected_value": ..., "reason": "<why>"}}
      ],
      "additional_warnings": [
        {{"field": "<name>", "message": "<description>"}}
      ]
    }}
""")


class OutputAgent:
    """Agent 3: final cross-check and output assembly."""

    def __init__(self, credential: DefaultAzureCredential | None = None) -> None:
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

    def assemble(
        self,
        raw: RawExtractionResult,
        validated: ValidatedResult,
    ) -> FinalOutput:
        """Run the final cross-check and produce the output JSON."""
        category = raw.category
        logger.info(
            "=== Agent 3: Assembling output for '%s' (category=%s) ===",
            raw.source_file,
            category,
        )

        validated_json = json.dumps(
            validated.model_dump(exclude={"corrections", "warnings"}),
            indent=2,
            default=str,
        )
        corrections_json = json.dumps(
            [c.model_dump() for c in validated.corrections], indent=2, default=str
        )
        warnings_json = json.dumps(
            [w.model_dump() for w in validated.warnings], indent=2, default=str
        )

        # Select category-specific prompts
        if category == "pcu":
            system_prompt = _SYSTEM_PROMPT_PCU
            user_prompt = _USER_PROMPT_PCU.format(
                source_file=raw.source_file or "unknown",
                validated_json=validated_json,
                corrections_json=corrections_json,
                warnings_json=warnings_json,
            )
        else:
            system_prompt = _SYSTEM_PROMPT_BANK
            user_prompt = _USER_PROMPT_BANK.format(
                source_file=raw.source_file or "unknown",
                validated_json=validated_json,
                corrections_json=corrections_json,
                warnings_json=warnings_json,
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
        logger.debug("Agent 3 raw response: %s", content[:500])

        return self._build_final_output(content, raw, validated)

    # ------------------------------------------------------------------ #
    #  Build the final output model
    # ------------------------------------------------------------------ #

    def _build_final_output(
        self,
        llm_content: str,
        raw: RawExtractionResult,
        validated: ValidatedResult,
    ) -> FinalOutput:
        """Merge Agent 3's LLM response with prior data into FinalOutput."""
        data = json.loads(llm_content)
        category = raw.category

        # Helper to get confidence from raw extraction
        def _conf(attr: str) -> float | None:
            field_val = getattr(raw, attr, None)
            return field_val.confidence if field_val else None

        def _page(attr: str) -> int | None:
            field_val = getattr(raw, attr, None)
            return field_val.source_page if field_val else None

        # Merge corrections from Agent 2 + Agent 3
        all_corrections = list(validated.corrections)
        for c in data.get("additional_corrections", []):
            all_corrections.append(Correction(**c))

        all_warnings = list(validated.warnings)
        for w in data.get("additional_warnings", []):
            all_warnings.append(Warning(**w))

        output = FinalOutput(
            category=category,
            institution_name=data.get("institution_name", validated.institution_name),
            member_of_ria=data.get("member_of_ria", validated.member_of_ria),
            capital_ratio=ConfidenceValue(
                value=data.get("capital_ratio", validated.capital_ratio),
                unit="%",
                confidence=_conf("capital_ratio"),
                source_page=_page("capital_ratio"),
            ),
            assets=YearPairValue(
                **{
                    "2023": ConfidenceValue(
                        value=data.get("assets_2023_billion", validated.assets_2023_billion),
                        unit="billion",
                        confidence=_conf("assets_2023"),
                        source_page=_page("assets_2023"),
                    ),
                    "2024": ConfidenceValue(
                        value=data.get("assets_2024_billion", validated.assets_2024_billion),
                        unit="billion",
                        confidence=_conf("assets_2024"),
                        source_page=_page("assets_2024"),
                    ),
                }
            ),
            deposits=YearPairValue(
                **{
                    "2023": ConfidenceValue(
                        value=data.get("deposits_2023_billion", validated.deposits_2023_billion),
                        unit="billion",
                        confidence=_conf("deposits_2023"),
                        source_page=_page("deposits_2023"),
                    ),
                    "2024": ConfidenceValue(
                        value=data.get("deposits_2024_billion", validated.deposits_2024_billion),
                        unit="billion",
                        confidence=_conf("deposits_2024"),
                        source_page=_page("deposits_2024"),
                    ),
                }
            ),
            total_loans=YearPairValue(
                **{
                    "2023": ConfidenceValue(
                        value=data.get("total_loans_2023_billion", validated.total_loans_2023_billion),
                        unit="billion",
                        confidence=_conf("total_loans_2023"),
                        source_page=_page("total_loans_2023"),
                    ),
                    "2024": ConfidenceValue(
                        value=data.get("total_loans_2024_billion", validated.total_loans_2024_billion),
                        unit="billion",
                        confidence=_conf("total_loans_2024"),
                        source_page=_page("total_loans_2024"),
                    ),
                }
            ),
            allowance_for_credit_losses=ConfidenceValue(
                value=data.get("allowance_for_credit_losses_mm", validated.allowance_for_credit_losses_mm),
                unit="millions",
                confidence=_conf("allowance_for_credit_losses"),
                source_page=_page("allowance_for_credit_losses"),
            ),
            loans_written_off=ConfidenceValue(
                value=data.get("loans_written_off_mm", validated.loans_written_off_mm),
                unit="millions",
                confidence=_conf("loans_written_off"),
                source_page=_page("loans_written_off"),
            ),
            source_file=raw.source_file,
            extracted_at=datetime.now(timezone.utc),
            extraction_quality=data.get("extraction_quality", "medium"),
            corrections=all_corrections,
            warnings=all_warnings,
        )

        # Category-specific fields
        if category == "pcu":
            output.province = data.get("province", validated.province)
            output.deposit_insurance_amount_guaranteed = StringConfidenceValue(
                value=data.get("deposit_insurance_amount_guaranteed",
                               validated.deposit_insurance_amount_guaranteed),
                confidence=_conf("deposit_insurance_amount_guaranteed"),
                source_page=_page("deposit_insurance_amount_guaranteed"),
            )
            output.deposit_insurance_dbrs = StringConfidenceValue(
                value=data.get("deposit_insurance_dbrs",
                               validated.deposit_insurance_dbrs),
                confidence=_conf("deposit_insurance_dbrs"),
                source_page=_page("deposit_insurance_dbrs"),
            )
            output.deposit_insurance_guarantee_corporation = StringConfidenceValue(
                value=data.get("deposit_insurance_guarantee_corporation",
                               validated.deposit_insurance_guarantee_corporation),
                confidence=_conf("deposit_insurance_guarantee_corporation"),
                source_page=_page("deposit_insurance_guarantee_corporation"),
            )
        else:
            output.short_term_dbrs = StringConfidenceValue(
                value=data.get("short_term_dbrs", validated.short_term_dbrs),
                confidence=_conf("short_term_dbrs"),
                source_page=_page("short_term_dbrs"),
            )
            output.short_term_sp = StringConfidenceValue(
                value=data.get("short_term_sp", validated.short_term_sp),
                confidence=_conf("short_term_sp"),
                source_page=_page("short_term_sp"),
            )
            output.short_term_moodys = StringConfidenceValue(
                value=data.get("short_term_moodys", validated.short_term_moodys),
                confidence=_conf("short_term_moodys"),
                source_page=_page("short_term_moodys"),
            )
            output.long_term_dbrs = StringConfidenceValue(
                value=data.get("long_term_dbrs", validated.long_term_dbrs),
                confidence=_conf("long_term_dbrs"),
                source_page=_page("long_term_dbrs"),
            )
            output.long_term_sp = StringConfidenceValue(
                value=data.get("long_term_sp", validated.long_term_sp),
                confidence=_conf("long_term_sp"),
                source_page=_page("long_term_sp"),
            )
            output.long_term_moodys = StringConfidenceValue(
                value=data.get("long_term_moodys", validated.long_term_moodys),
                confidence=_conf("long_term_moodys"),
                source_page=_page("long_term_moodys"),
            )

        return output
