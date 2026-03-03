"""Agent 1 — Document Extraction Agent.

Uses Azure Content Understanding to parse a PDF and extract raw financial
field values with confidence scores and source grounding.

Supports two analyzer schemas — one for PCU, one for Bank/FCU/Other.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from src.models.schemas import RawExtractionResult, RawFieldValue
from src.services.blob_storage import BlobStorageService
from src.services.content_understanding import ContentUnderstandingService

logger = logging.getLogger(__name__)

# Field maps: Content Understanding field names → RawExtractionResult attributes
# The maps are different for each institution category.

_PCU_FIELD_MAP: dict[str, str] = {
    "InstitutionName": "institution_name",
    "Province": "province",
    "MemberOfRIA": "member_of_ria",
    "DepositInsuranceAmountGuaranteed": "deposit_insurance_amount_guaranteed",
    "DepositInsuranceDBRS": "deposit_insurance_dbrs",
    "DepositInsuranceGuaranteeCorporation": "deposit_insurance_guarantee_corporation",
    "CapitalRatio": "capital_ratio",
    "Assets2023": "assets_2023",
    "Assets2024": "assets_2024",
    "Deposits2023": "deposits_2023",
    "Deposits2024": "deposits_2024",
    "TotalLoans2023": "total_loans_2023",
    "TotalLoans2024": "total_loans_2024",
    "AllowanceForCreditLosses": "allowance_for_credit_losses",
    "LoansWrittenOff": "loans_written_off",
    "ReportingCurrencyUnit": "reporting_currency_unit",
}

_BANK_FIELD_MAP: dict[str, str] = {
    "InstitutionName": "institution_name",
    "MemberOfRIA": "member_of_ria",
    "ShortTermDBRS": "short_term_dbrs",
    "ShortTermSP": "short_term_sp",
    "ShortTermMoodys": "short_term_moodys",
    "LongTermDBRS": "long_term_dbrs",
    "LongTermSP": "long_term_sp",
    "LongTermMoodys": "long_term_moodys",
    "CapitalRatio": "capital_ratio",
    "Assets2023": "assets_2023",
    "Assets2024": "assets_2024",
    "Deposits2023": "deposits_2023",
    "Deposits2024": "deposits_2024",
    "TotalLoans2023": "total_loans_2023",
    "TotalLoans2024": "total_loans_2024",
    "AllowanceForCreditLosses": "allowance_for_credit_losses",
    "LoansWrittenOff": "loans_written_off",
    "ReportingCurrencyUnit": "reporting_currency_unit",
}


def _parse_page_from_source(source: str | None) -> int | None:
    """Extract page number from Content Understanding source grounding string.

    The grounding format is like ``D(1,...)`` where the first number is the
    1-based page index.
    """
    if not source:
        return None
    match = re.match(r"D\((\d+)", source)
    return int(match.group(1)) if match else None


def _extract_field(field_data: dict) -> RawFieldValue:
    """Convert a single Content Understanding field dict into RawFieldValue."""
    ftype = field_data.get("type", "")
    if ftype == "number":
        value = field_data.get("valueNumber")
    elif ftype == "string":
        value = field_data.get("valueString")
    elif ftype == "date":
        value = field_data.get("valueDate")
    else:
        value = field_data.get("valueString") or field_data.get("valueNumber")

    source_str = field_data.get("source")

    return RawFieldValue(
        value=value,
        confidence=field_data.get("confidence"),
        source=source_str,
        source_page=_parse_page_from_source(source_str),
    )


class ExtractionAgent:
    """Agent 1: wraps Content Understanding to extract fields from a PDF.

    Supports two analyzers — one per institution category.
    """

    def __init__(
        self,
        blob_service: BlobStorageService,
        cu_service: ContentUnderstandingService,
    ) -> None:
        self.blob_service = blob_service
        self.cu_service = cu_service

    def ensure_analyzer_exists(
        self,
        schema_path: str | Path,
        analyzer_id: str | None = None,
    ) -> None:
        """Create the custom analyzer if it doesn't already exist.

        If *analyzer_id* is given, temporarily override the service's
        default analyzer ID for this call.
        """
        original_id = self.cu_service.analyzer_id
        if analyzer_id:
            self.cu_service.analyzer_id = analyzer_id

        try:
            existing = self.cu_service.get_analyzer()
            if existing:
                logger.info(
                    "Analyzer '%s' already exists — skipping creation.",
                    self.cu_service.analyzer_id,
                )
                return

            logger.info(
                "Analyzer '%s' not found — creating …",
                self.cu_service.analyzer_id,
            )
            self.cu_service.create_or_update_analyzer(schema_path)
        finally:
            self.cu_service.analyzer_id = original_id

    def ensure_both_analyzers(
        self,
        pcu_schema_path: Path,
        bank_schema_path: Path,
        pcu_analyzer_id: str = "pcu_annual_report",
        bank_analyzer_id: str = "bank_fcu_other_annual_report",
    ) -> None:
        """Ensure both category-specific analyzers exist."""
        self.ensure_analyzer_exists(pcu_schema_path, pcu_analyzer_id)
        self.ensure_analyzer_exists(bank_schema_path, bank_analyzer_id)

    def extract(
        self,
        pdf_path: str | Path,
        category: Literal["pcu", "bank_fcu_other"] = "pcu",
        analyzer_id: str | None = None,
    ) -> RawExtractionResult:
        """Run the full extraction pipeline for a single PDF.

        1. Upload to Blob Storage → SAS URL
        2. Call Content Understanding Analyze API with the category-appropriate analyzer
        3. Parse the response into ``RawExtractionResult``
        """
        pdf_path = Path(pdf_path)

        if analyzer_id is None:
            analyzer_id = (
                "pcu_annual_report"
                if category == "pcu"
                else "bank_fcu_other_annual_report"
            )

        logger.info(
            "=== Agent 1: Extracting '%s' (category=%s, analyzer=%s) ===",
            pdf_path.name,
            category,
            analyzer_id,
        )

        # Step 1 — upload & get SAS URL
        sas_url = self.blob_service.upload_and_get_sas_url(pdf_path)

        # Step 2 — call analyze with the correct analyzer
        original_id = self.cu_service.analyzer_id
        self.cu_service.analyzer_id = analyzer_id
        try:
            result = self.cu_service.analyze(sas_url)
        finally:
            self.cu_service.analyzer_id = original_id

        # Step 3 — parse
        return self._parse_result(result, pdf_path.name, category)

    # ------------------------------------------------------------------ #
    #  Result parsing
    # ------------------------------------------------------------------ #

    def _parse_result(
        self,
        result: dict,
        source_file: str,
        category: Literal["pcu", "bank_fcu_other"] = "pcu",
    ) -> RawExtractionResult:
        """Parse the Content Understanding JSON response."""
        extraction = RawExtractionResult(source_file=source_file, category=category)

        field_map = _PCU_FIELD_MAP if category == "pcu" else _BANK_FIELD_MAP

        # Navigate into the result structure
        contents = (
            result.get("result", {}).get("contents", [])
        )
        if not contents:
            logger.warning("No contents returned by Content Understanding for %s", source_file)
            return extraction

        # Use the first content block (single-document scenario)
        content = contents[0]

        # Capture full markdown for Agent 2
        extraction.markdown_content = content.get("markdown", "")

        # Extract each field
        fields = content.get("fields", {})
        for cu_name, attr_name in field_map.items():
            field_data = fields.get(cu_name)
            if field_data:
                setattr(extraction, attr_name, _extract_field(field_data))
            else:
                logger.debug("Field '%s' not found in response for %s", cu_name, source_file)

        return extraction
