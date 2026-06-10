"""Agent 0 — Classification Agent.

An agentic classifier: it matches each PDF to the institution reference list and
determines whether it belongs to the PCU or Bank/FCU/Other category. When a
filename is ambiguous it can call the ``read_document_text`` tool to inspect the
PDF's first pages before deciding, rather than giving up. Files are physically
moved into the appropriate intake subfolder.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from textwrap import dedent

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from src.agents.agentic import PdfTextTool, run_tool_loop

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- #
#  Prompt templates
# --------------------------------------------------------------------- #

_SYSTEM_PROMPT = dedent("""\
    You are a classification assistant.  You are given a PDF filename and
    two lists of Canadian financial institutions: one for Provincial Credit
    Unions (PCU) and one for Banks / Federal Credit Unions / Others.

    Your task is to determine which institution the PDF belongs to and
    which category it falls into.

    Rules:
    - Match using fuzzy logic — filenames may contain abbreviations,
      year prefixes, extra punctuation, or informal names.
    - If the filename clearly matches an institution in one of the lists,
      return the match.
    - If the filename is ambiguous or does not obviously match any
      institution, call the read_document_text tool to read the first
      page(s) of the PDF and identify the institution from its contents
      BEFORE deciding.
    - Only return category "unclassified" if, after inspecting the
      document, you still cannot confidently match it to any institution.

    When you have decided, respond ONLY with a JSON object (no markdown fences):
    {
      "institution": "<matched institution name from the list, or null>",
      "category": "<pcu | bank_fcu_other | unclassified>",
      "confidence": <0.0 to 1.0>
    }
""")

_USER_PROMPT_TEMPLATE = dedent("""\
    ## PDF Filename
    {filename}

    ## Institution Lists

    ### Provincial Credit Unions (PCU)
    {pcu_list}

    ### Banks / Federal Credit Unions / Others
    {bank_fcu_other_list}

    Classify the PDF filename above.  Return JSON only.
""")


# --------------------------------------------------------------------- #
#  Data class for classification result
# --------------------------------------------------------------------- #

class ClassificationResult:
    """Result of classifying a single PDF."""

    def __init__(
        self,
        filename: str,
        institution: str | None,
        category: str,
        confidence: float,
    ):
        self.filename = filename
        self.institution = institution
        self.category = category
        self.confidence = confidence

    def __repr__(self) -> str:
        return (
            f"ClassificationResult(filename={self.filename!r}, "
            f"institution={self.institution!r}, "
            f"category={self.category!r}, "
            f"confidence={self.confidence})"
        )


# --------------------------------------------------------------------- #
#  Classification Agent
# --------------------------------------------------------------------- #

class ClassificationAgent:
    """Agent 0: classify PDFs into PCU vs Bank/FCU/Other categories."""

    def __init__(
        self,
        institutions_path: Path,
        credential: DefaultAzureCredential | None = None,
    ):
        self._institutions = self._load_institutions(institutions_path)

        # Build the formatted institution lists for the prompt
        self._pcu_list = "\n".join(
            f"- {name}" for name in self._institutions["pcu"]["institutions"]
        )
        self._bank_fcu_other_list = "\n".join(
            f"- {name}"
            for name in self._institutions["bank_fcu_other"]["institutions"]
        )

        # Azure OpenAI client (GPT-4.1-mini)
        endpoint = os.environ["AZURE_AI_ENDPOINT"]
        deployment = os.environ.get(
            "AZURE_OPENAI_CLASSIFICATION_DEPLOYMENT", "gpt-4.1-mini"
        )

        if credential is None:
            credential = DefaultAzureCredential()

        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )

        self._client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2025-04-01-preview",
        )
        self._deployment = deployment
        self._max_iterations = int(os.environ.get("AGENT_MAX_ITERATIONS", "6"))

    # ----------------------------------------------------------------- #
    #  Public API
    # ----------------------------------------------------------------- #

    def classify_and_sort(
        self,
        input_folder: Path,
        pdf_paths: list[Path],
    ) -> list[ClassificationResult]:
        """Classify each PDF and move it to the appropriate subfolder.

        Creates ``input/pcu/``, ``input/bank_fcu_other/``, and
        ``input/unclassified/`` subfolders as needed.

        Returns a list of ClassificationResult objects.
        """
        pcu_dir = input_folder / "pcu"
        bank_dir = input_folder / "bank_fcu_other"
        unclassified_dir = input_folder / "unclassified"

        for d in (pcu_dir, bank_dir, unclassified_dir):
            d.mkdir(exist_ok=True)

        results: list[ClassificationResult] = []

        for pdf_path in pdf_paths:
            result = self._classify_one(pdf_path)

            # Move file to the appropriate subfolder
            dest_map = {
                "pcu": pcu_dir,
                "bank_fcu_other": bank_dir,
                "unclassified": unclassified_dir,
            }
            dest_dir = dest_map.get(result.category, unclassified_dir)
            dest_path = dest_dir / pdf_path.name

            if pdf_path != dest_path:
                shutil.move(str(pdf_path), str(dest_path))

            logger.info(
                "  %-40s → %-16s  (%s, conf=%.2f)",
                pdf_path.name,
                result.category,
                result.institution or "??",
                result.confidence,
            )

            if result.category == "unclassified":
                logger.warning(
                    "  ⚠ '%s' could not be matched — moved to unclassified/ (skipped)",
                    pdf_path.name,
                )

            results.append(result)

        # Summary
        n_pcu = sum(1 for r in results if r.category == "pcu")
        n_bank = sum(1 for r in results if r.category == "bank_fcu_other")
        n_unk = sum(1 for r in results if r.category == "unclassified")
        logger.info(
            "Classification complete: %d PCU, %d Bank/FCU/Other, %d unclassified",
            n_pcu,
            n_bank,
            n_unk,
        )

        return results

    # ----------------------------------------------------------------- #
    #  Internals
    # ----------------------------------------------------------------- #

    @staticmethod
    def _load_institutions(path: Path) -> dict:
        with open(path) as f:
            return json.load(f)

    def _classify_one(self, pdf_path: Path) -> ClassificationResult:
        """Classify a single PDF, inspecting its contents if the name is unclear.

        Runs an agentic loop: the model may call ``read_document_text`` to read
        the PDF's first pages before committing to a classification.
        """
        filename = pdf_path.name
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            filename=filename,
            pcu_list=self._pcu_list,
            bank_fcu_other_list=self._bank_fcu_other_list,
        )

        pdf_tool = PdfTextTool(pdf_path)
        tools = [pdf_tool.schema]

        def dispatch(name: str, args: dict) -> str:
            if name == pdf_tool.name:
                return pdf_tool.run(args)
            return f"ERROR: unknown tool '{name}'"

        raw_text = run_tool_loop(
            self._client,
            deployment=self._deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            tools=tools,
            dispatch=dispatch,
            max_iterations=self._max_iterations,
        )

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Failed to parse classification response: %s", raw_text)
            return ClassificationResult(
                filename=filename,
                institution=None,
                category="unclassified",
                confidence=0.0,
            )

        category = data.get("category", "unclassified")
        if category not in ("pcu", "bank_fcu_other"):
            category = "unclassified"

        return ClassificationResult(
            filename=filename,
            institution=data.get("institution"),
            category=category,
            confidence=data.get("confidence", 0.0),
        )
