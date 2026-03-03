"""Main orchestrator — processes all PDFs through the 4-agent pipeline."""

from __future__ import annotations

import csv
import json
import logging
import sys
import time
from pathlib import Path

import yaml
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from src.agents.classification_agent import ClassificationAgent
from src.agents.extraction_agent import ExtractionAgent
from src.agents.output_agent import OutputAgent
from src.agents.validation_agent import ValidationAgent
from src.models.schemas import FinalOutput
from src.services.blob_storage import BlobStorageService
from src.services.content_understanding import ContentUnderstandingService

# --------------------------------------------------------------------------- #
#  Logging setup
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def _load_config(config_path: Path) -> dict:
    """Load YAML configuration."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def _find_pdfs(input_folder: Path) -> list[Path]:
    """Return all PDF files in *input_folder*, sorted alphabetically."""
    pdfs = sorted(input_folder.glob("*.pdf"), key=lambda p: p.name)
    if not pdfs:
        pdfs = sorted(input_folder.glob("*.PDF"), key=lambda p: p.name)
    return pdfs


def _write_output(output: FinalOutput, output_folder: Path) -> Path:
    """Write a FinalOutput to a JSON file and return the path."""
    # Derive filename from institution name, falling back to source file
    name = output.institution_name or output.source_file or "unknown"
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
    out_path = output_folder / f"{safe_name}.json"

    data = output.model_dump(mode="json", by_alias=True)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return out_path


def _write_summary(results: list[FinalOutput], output_folder: Path, suffix: str = "") -> Path:
    """Write a consolidated summary JSON containing all results.

    *suffix* is appended to the filename, e.g. ``_pcu`` or ``_bank_fcu_other``.
    """
    out_path = output_folder / f"_summary{suffix}.json"

    summary = []
    for r in results:
        row: dict = {
            "institution_name": r.institution_name,
            "category": r.category,
            "member_of_ria": r.member_of_ria,
        }

        if r.category == "pcu":
            row.update({
                "province": r.province,
                "deposit_insurance_amount_guaranteed": r.deposit_insurance_amount_guaranteed.value if r.deposit_insurance_amount_guaranteed else None,
                "deposit_insurance_dbrs": r.deposit_insurance_dbrs.value if r.deposit_insurance_dbrs else None,
                "deposit_insurance_guarantee_corporation": r.deposit_insurance_guarantee_corporation.value if r.deposit_insurance_guarantee_corporation else None,
            })
        else:
            row.update({
                "short_term_dbrs": r.short_term_dbrs.value if r.short_term_dbrs else None,
                "short_term_sp": r.short_term_sp.value if r.short_term_sp else None,
                "short_term_moodys": r.short_term_moodys.value if r.short_term_moodys else None,
                "long_term_dbrs": r.long_term_dbrs.value if r.long_term_dbrs else None,
                "long_term_sp": r.long_term_sp.value if r.long_term_sp else None,
                "long_term_moodys": r.long_term_moodys.value if r.long_term_moodys else None,
            })

        row.update({
            "capital_ratio": r.capital_ratio.value if r.capital_ratio else None,
            "assets_2023_billion": r.assets.year_2023.value if r.assets and r.assets.year_2023 else None,
            "assets_2024_billion": r.assets.year_2024.value if r.assets and r.assets.year_2024 else None,
            "deposits_2023_billion": r.deposits.year_2023.value if r.deposits and r.deposits.year_2023 else None,
            "deposits_2024_billion": r.deposits.year_2024.value if r.deposits and r.deposits.year_2024 else None,
            "total_loans_2023_billion": r.total_loans.year_2023.value if r.total_loans and r.total_loans.year_2023 else None,
            "total_loans_2024_billion": r.total_loans.year_2024.value if r.total_loans and r.total_loans.year_2024 else None,
            "allowance_for_credit_losses_mm": r.allowance_for_credit_losses.value if r.allowance_for_credit_losses else None,
            "loans_written_off_mm": r.loans_written_off.value if r.loans_written_off else None,
            "extraction_quality": r.extraction_quality,
            "source_file": r.source_file,
        })

        summary.append(row)

    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    return out_path


def _write_csv(results: list[FinalOutput], output_folder: Path, suffix: str = "") -> Path:
    """Write a CSV summary for the given results.

    Column set differs by category (PCU vs Bank/FCU/Other).
    """
    if not results:
        return output_folder / f"_summary{suffix}.csv"

    category = results[0].category
    out_path = output_folder / f"_summary{suffix}.csv"

    # Common columns
    common_cols = [
        "institution_name", "member_of_ria", "capital_ratio",
        "assets_2023_billion", "assets_2024_billion",
        "deposits_2023_billion", "deposits_2024_billion",
        "total_loans_2023_billion", "total_loans_2024_billion",
        "allowance_for_credit_losses_mm", "loans_written_off_mm",
        "extraction_quality", "source_file",
    ]

    if category == "pcu":
        header = [
            "institution_name", "province", "member_of_ria",
            "deposit_insurance_amount_guaranteed",
            "deposit_insurance_dbrs",
            "deposit_insurance_guarantee_corporation",
        ] + common_cols[2:]  # skip institution_name and member_of_ria (already listed)
    else:
        header = [
            "institution_name", "member_of_ria",
            "short_term_dbrs", "short_term_sp", "short_term_moodys",
            "long_term_dbrs", "long_term_sp", "long_term_moodys",
        ] + common_cols[2:]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()

        for r in results:
            row: dict = {
                "institution_name": r.institution_name,
                "member_of_ria": r.member_of_ria,
                "capital_ratio": r.capital_ratio.value if r.capital_ratio else None,
                "assets_2023_billion": r.assets.year_2023.value if r.assets and r.assets.year_2023 else None,
                "assets_2024_billion": r.assets.year_2024.value if r.assets and r.assets.year_2024 else None,
                "deposits_2023_billion": r.deposits.year_2023.value if r.deposits and r.deposits.year_2023 else None,
                "deposits_2024_billion": r.deposits.year_2024.value if r.deposits and r.deposits.year_2024 else None,
                "total_loans_2023_billion": r.total_loans.year_2023.value if r.total_loans and r.total_loans.year_2023 else None,
                "total_loans_2024_billion": r.total_loans.year_2024.value if r.total_loans and r.total_loans.year_2024 else None,
                "allowance_for_credit_losses_mm": r.allowance_for_credit_losses.value if r.allowance_for_credit_losses else None,
                "loans_written_off_mm": r.loans_written_off.value if r.loans_written_off else None,
                "extraction_quality": r.extraction_quality,
                "source_file": r.source_file,
            }

            if category == "pcu":
                row.update({
                    "province": r.province,
                    "deposit_insurance_amount_guaranteed": r.deposit_insurance_amount_guaranteed.value if r.deposit_insurance_amount_guaranteed else None,
                    "deposit_insurance_dbrs": r.deposit_insurance_dbrs.value if r.deposit_insurance_dbrs else None,
                    "deposit_insurance_guarantee_corporation": r.deposit_insurance_guarantee_corporation.value if r.deposit_insurance_guarantee_corporation else None,
                })
            else:
                row.update({
                    "short_term_dbrs": r.short_term_dbrs.value if r.short_term_dbrs else None,
                    "short_term_sp": r.short_term_sp.value if r.short_term_sp else None,
                    "short_term_moodys": r.short_term_moodys.value if r.short_term_moodys else None,
                    "long_term_dbrs": r.long_term_dbrs.value if r.long_term_dbrs else None,
                    "long_term_sp": r.long_term_sp.value if r.long_term_sp else None,
                    "long_term_moodys": r.long_term_moodys.value if r.long_term_moodys else None,
                })

            writer.writerow(row)

    return out_path


# --------------------------------------------------------------------------- #
#  Main pipeline
# --------------------------------------------------------------------------- #

def main() -> None:
    """Entry point — run the 4-agent extraction pipeline on all PDFs."""

    # ---- Load environment & config ---- #
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")

    config_path = project_root / "config" / "settings.yaml"
    config = _load_config(config_path)

    input_folder = project_root / "input"
    output_folder = project_root / "output"
    output_folder.mkdir(exist_ok=True)

    institutions_path = project_root / "config" / "institutions.json"
    pcu_analyzer_schema = project_root / "analyzers" / "pcu_annual_report.json"
    bank_analyzer_schema = project_root / "analyzers" / "bank_fcu_other_annual_report.json"

    # ---- Discover PDFs ---- #
    pdfs = _find_pdfs(input_folder)
    if not pdfs:
        logger.error("No PDF files found in %s", input_folder)
        sys.exit(1)

    logger.info("Found %d PDF(s) to process:", len(pdfs))
    for p in pdfs:
        logger.info("  • %s", p.name)

    # ---- Initialise shared credential ---- #
    credential = DefaultAzureCredential()

    # ---- Agent 0 — Classification ---- #
    logger.info("━━━ Agent 0: Classifying PDFs ━━━")
    classification_agent = ClassificationAgent(
        institutions_path=institutions_path,
        credential=credential,
    )
    classifications = classification_agent.classify_and_sort(input_folder, pdfs)

    # Build per-category PDF lists (skip unclassified)
    pcu_pdfs = [
        input_folder / "pcu" / c.filename
        for c in classifications
        if c.category == "pcu"
    ]
    bank_pdfs = [
        input_folder / "bank_fcu_other" / c.filename
        for c in classifications
        if c.category == "bank_fcu_other"
    ]

    # Build a lookup: filename → classification result
    classification_map = {c.filename: c for c in classifications}

    # ---- Initialise services & agents ---- #
    blob_service = BlobStorageService(credential=credential)
    cu_service = ContentUnderstandingService(credential=credential)

    extraction_agent = ExtractionAgent(blob_service, cu_service)
    validation_agent = ValidationAgent(
        credential=credential,
        province_guarantee_map=config.get("province_guarantee_map"),
    )
    output_agent = OutputAgent(credential=credential)

    # ---- Ensure both custom analyzers exist ---- #
    extraction_agent.ensure_both_analyzers(pcu_analyzer_schema, bank_analyzer_schema)

    # ---- Process each classified PDF ---- #
    all_pdfs_to_process = pcu_pdfs + bank_pdfs
    results: list[FinalOutput] = []
    errors: list[tuple[str, Exception]] = []

    max_retries = 3

    for pdf_path in all_pdfs_to_process:
        cls_result = classification_map[pdf_path.name]
        category = cls_result.category

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    "━━━ Processing: %s [%s — %s]%s ━━━",
                    pdf_path.name,
                    category,
                    cls_result.institution or "unknown",
                    f" (attempt {attempt}/{max_retries})" if attempt > 1 else "",
                )

                # Agent 1 — Extract (with category-specific analyzer)
                raw = extraction_agent.extract(pdf_path, category=category)

                # Agent 2 — Validate & Normalise
                validated = validation_agent.validate(raw)

                # Agent 3 — Cross-Check & Assemble
                final = output_agent.assemble(raw, validated)

                # Write individual output file
                out_path = _write_output(final, output_folder)
                logger.info("✓ Output written → %s", out_path.name)

                results.append(final)
                break  # success — no more retries needed

            except Exception as exc:
                if attempt < max_retries:
                    wait = 10 * attempt  # 10s, 20s backoff
                    logger.warning(
                        "⟳ Attempt %d/%d failed for '%s': %s — retrying in %ds",
                        attempt, max_retries, pdf_path.name, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.exception(
                        "✗ Failed to process '%s' after %d attempts: %s",
                        pdf_path.name, max_retries, exc,
                    )
                    errors.append((pdf_path.name, exc))

    # ---- Write per-category summaries (JSON + CSV) ---- #
    pcu_results = [r for r in results if r.category == "pcu"]
    bank_results = [r for r in results if r.category == "bank_fcu_other"]

    if pcu_results:
        sp = _write_summary(pcu_results, output_folder, suffix="_pcu")
        cp = _write_csv(pcu_results, output_folder, suffix="_pcu")
        logger.info("PCU summary → %s + %s  (%d institutions)", sp.name, cp.name, len(pcu_results))

    if bank_results:
        sb = _write_summary(bank_results, output_folder, suffix="_bank_fcu_other")
        cb = _write_csv(bank_results, output_folder, suffix="_bank_fcu_other")
        logger.info("Bank/FCU/Other summary → %s + %s  (%d institutions)", sb.name, cb.name, len(bank_results))

    # Also write a combined summary (JSON only)
    if results:
        sa = _write_summary(results, output_folder)
        logger.info("Combined summary written → %s", sa.name)

    # ---- Final report ---- #
    n_skipped = sum(1 for c in classifications if c.category == "unclassified")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("Processing complete:")
    logger.info("  Classified: %d PCU, %d Bank/FCU/Other, %d unclassified (skipped)",
                len(pcu_pdfs), len(bank_pdfs), n_skipped)
    logger.info("  Succeeded:  %d / %d", len(results), len(all_pdfs_to_process))
    if errors:
        logger.info("  Failed:     %d", len(errors))
        for name, exc in errors:
            logger.info("    • %s — %s", name, exc)
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    main()
