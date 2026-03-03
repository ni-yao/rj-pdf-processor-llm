"""Pydantic models for extracted financial data schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
#  Raw extraction result from Agent 1 (Content Understanding)
# --------------------------------------------------------------------------- #

class RawFieldValue(BaseModel):
    """A single field value as returned by Content Understanding."""
    value: Optional[str | float | int] = None
    confidence: Optional[float] = None
    source: Optional[str] = None  # grounding polygon string
    source_page: Optional[int] = None


class RawExtractionResult(BaseModel):
    """Complete raw extraction result from Agent 1.

    This is a **union model** — it holds fields for both PCU and Bank/FCU/Other
    categories.  The ``category`` field indicates which set of fields is
    populated.  Unused fields for the other category remain ``None``.
    """
    category: Literal["pcu", "bank_fcu_other"] = "pcu"

    # --- Common fields (both categories) --- #
    institution_name: Optional[RawFieldValue] = None
    member_of_ria: Optional[RawFieldValue] = None
    capital_ratio: Optional[RawFieldValue] = None
    assets_2023: Optional[RawFieldValue] = None
    assets_2024: Optional[RawFieldValue] = None
    deposits_2023: Optional[RawFieldValue] = None
    deposits_2024: Optional[RawFieldValue] = None
    total_loans_2023: Optional[RawFieldValue] = None
    total_loans_2024: Optional[RawFieldValue] = None
    allowance_for_credit_losses: Optional[RawFieldValue] = None
    loans_written_off: Optional[RawFieldValue] = None
    reporting_currency_unit: Optional[RawFieldValue] = None

    # --- PCU-only fields --- #
    province: Optional[RawFieldValue] = None
    deposit_insurance_amount_guaranteed: Optional[RawFieldValue] = None
    deposit_insurance_dbrs: Optional[RawFieldValue] = None
    deposit_insurance_guarantee_corporation: Optional[RawFieldValue] = None

    # --- Bank/FCU/Other-only fields --- #
    short_term_dbrs: Optional[RawFieldValue] = None
    short_term_sp: Optional[RawFieldValue] = None
    short_term_moodys: Optional[RawFieldValue] = None
    long_term_dbrs: Optional[RawFieldValue] = None
    long_term_sp: Optional[RawFieldValue] = None
    long_term_moodys: Optional[RawFieldValue] = None

    # Markdown content from Content Understanding (used by Agent 2)
    markdown_content: Optional[str] = None
    source_file: Optional[str] = None


# --------------------------------------------------------------------------- #
#  Validated / normalised result from Agent 2
# --------------------------------------------------------------------------- #

class Correction(BaseModel):
    """A single correction applied during validation."""
    field: str
    original_value: Optional[str | float | int] = None
    corrected_value: Optional[str | float | int] = None
    reason: str


class Warning(BaseModel):
    """A warning raised during validation."""
    field: str
    message: str


class ValidatedResult(BaseModel):
    """Output of Agent 2 — validated and unit-normalised data.

    Union model — holds fields for both categories.
    """
    category: Literal["pcu", "bank_fcu_other"] = "pcu"

    # --- Common fields --- #
    institution_name: Optional[str] = None
    member_of_ria: Optional[str] = None
    capital_ratio: Optional[float] = None  # percentage

    # Financial values (normalised to target units)
    assets_2023_billion: Optional[float] = None
    assets_2024_billion: Optional[float] = None
    deposits_2023_billion: Optional[float] = None
    deposits_2024_billion: Optional[float] = None
    total_loans_2023_billion: Optional[float] = None
    total_loans_2024_billion: Optional[float] = None
    allowance_for_credit_losses_mm: Optional[float] = None
    loans_written_off_mm: Optional[float] = None

    # --- PCU-only validated fields --- #
    province: Optional[str] = None
    deposit_insurance_amount_guaranteed: Optional[str] = None
    deposit_insurance_dbrs: Optional[str] = None
    deposit_insurance_guarantee_corporation: Optional[str] = None

    # --- Bank/FCU/Other-only validated fields --- #
    short_term_dbrs: Optional[str] = None
    short_term_sp: Optional[str] = None
    short_term_moodys: Optional[str] = None
    long_term_dbrs: Optional[str] = None
    long_term_sp: Optional[str] = None
    long_term_moodys: Optional[str] = None

    corrections: list[Correction] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Final output from Agent 3
# --------------------------------------------------------------------------- #

class ConfidenceValue(BaseModel):
    """A numeric value with confidence metadata."""
    value: Optional[float] = None
    unit: str
    confidence: Optional[float] = None
    source_page: Optional[int] = None


class StringConfidenceValue(BaseModel):
    """A string value with confidence metadata (for ratings, insurance, etc.)."""
    value: Optional[str] = None
    confidence: Optional[float] = None
    source_page: Optional[int] = None


class YearPairValue(BaseModel):
    """A pair of year-over-year values."""
    year_2023: Optional[ConfidenceValue] = Field(None, alias="2023")
    year_2024: Optional[ConfidenceValue] = Field(None, alias="2024")

    model_config = {"populate_by_name": True}


class FinalOutput(BaseModel):
    """The complete output JSON written for each PDF.

    Union model — holds fields for both categories.
    """
    category: Literal["pcu", "bank_fcu_other"] = "pcu"

    # --- Common fields --- #
    institution_name: Optional[str] = None
    member_of_ria: Optional[str] = None
    capital_ratio: Optional[ConfidenceValue] = None
    assets: Optional[YearPairValue] = None
    deposits: Optional[YearPairValue] = None
    total_loans: Optional[YearPairValue] = None
    allowance_for_credit_losses: Optional[ConfidenceValue] = None
    loans_written_off: Optional[ConfidenceValue] = None

    # --- PCU-only fields --- #
    province: Optional[str] = None
    deposit_insurance_amount_guaranteed: Optional[StringConfidenceValue] = None
    deposit_insurance_dbrs: Optional[StringConfidenceValue] = None
    deposit_insurance_guarantee_corporation: Optional[StringConfidenceValue] = None

    # --- Bank/FCU/Other-only fields --- #
    short_term_dbrs: Optional[StringConfidenceValue] = None
    short_term_sp: Optional[StringConfidenceValue] = None
    short_term_moodys: Optional[StringConfidenceValue] = None
    long_term_dbrs: Optional[StringConfidenceValue] = None
    long_term_sp: Optional[StringConfidenceValue] = None
    long_term_moodys: Optional[StringConfidenceValue] = None

    source_file: Optional[str] = None
    extracted_at: Optional[datetime] = None
    extraction_quality: Optional[str] = None  # high / medium / low
    corrections: list[Correction] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
