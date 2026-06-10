"""Shared agentic infrastructure.

This module provides the building blocks that turn a single-shot LLM call into a
genuinely *agentic* step — an LLM that dynamically directs its own process by
calling tools in a reason -> act -> observe loop until it is ready to answer.

Components:
  * :func:`run_tool_loop` — a model-agnostic tool-calling loop that drives an
    Azure OpenAI chat model, dispatches its tool calls, feeds results back, and
    finishes with a guaranteed schema-valid JSON answer.
  * :class:`DocumentSearchTool` — retrieve evidence from the *full* source
    document (not a truncated dump) on demand.
  * :class:`CalculatorTool` — exact arithmetic for unit conversions / consistency
    checks (LLMs are unreliable at multi-digit math).
  * :class:`PdfTextTool` — read the first pages of a PDF to disambiguate.
  * :func:`extract_json_text` — tolerant extraction of a JSON object from a model
    response (handles code fences / surrounding prose).
"""

from __future__ import annotations

import ast
import json
import logging
import operator
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# A dispatch callable maps (tool_name, arguments) -> tool result string.
ToolDispatch = Callable[[str, dict], str]


# --------------------------------------------------------------------------- #
#  JSON helpers
# --------------------------------------------------------------------------- #

def extract_json_text(text: str | None) -> str | None:
    """Return a parseable JSON object substring from *text*, or ``None``.

    Tolerates markdown code fences and leading/trailing prose so that the
    downstream ``json.loads`` in each agent always receives clean JSON.
    """
    if not text:
        return None

    candidate = text.strip()

    # Strip a leading ```json / ``` fence and trailing ``` if present.
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z0-9]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate).strip()

    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced-looking {...} block.
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if match:
        block = match.group(0)
        try:
            json.loads(block)
            return block
        except json.JSONDecodeError:
            return None
    return None


def _safe_json_loads(raw: str | None) -> dict:
    """Parse tool-call arguments into a dict, never raising."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _assistant_turn(message: Any) -> dict:
    """Convert an OpenAI assistant message into a plain dict for replay."""
    turn: dict = {"role": "assistant", "content": message.content or ""}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        turn["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tool_calls
        ]
    return turn


# --------------------------------------------------------------------------- #
#  The agentic loop
# --------------------------------------------------------------------------- #

def run_tool_loop(
    client: Any,
    *,
    deployment: str,
    messages: list[dict],
    tools: list[dict],
    dispatch: ToolDispatch,
    max_iterations: int = 6,
    temperature: float = 0.0,
) -> str:
    """Drive an agentic tool-calling loop and return the final JSON text.

    The model may call *tools* repeatedly to gather evidence. Each tool call is
    dispatched via *dispatch* and the result is fed back into the conversation.
    The loop ends when the model answers without requesting a tool. If that
    answer is already valid JSON it is returned directly (cheap path); otherwise
    a final completion is made with ``response_format=json_object`` and tools
    disabled, guaranteeing a schema-valid answer.

    The caller's *messages* list is not mutated.
    """
    convo = list(messages)

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=deployment,
            temperature=temperature,
            messages=convo,
            tools=tools,
            tool_choice="auto",
        )
        message = response.choices[0].message

        if not getattr(message, "tool_calls", None):
            # Model is ready to answer.
            direct = extract_json_text(message.content or "")
            if direct is not None:
                return direct
            # Unparseable — fall through to the forced-JSON repair step.
            convo.append(_assistant_turn(message))
            break

        # Replay the assistant's tool-call turn, then answer each tool call.
        convo.append(_assistant_turn(message))
        for tool_call in message.tool_calls:
            name = tool_call.function.name
            args = _safe_json_loads(tool_call.function.arguments)
            try:
                result = dispatch(name, args)
            except Exception as exc:  # a tool failure must not crash the agent
                logger.warning("Tool '%s' raised: %s", name, exc)
                result = f"ERROR: tool '{name}' failed: {exc}"
            convo.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result if isinstance(result, str) else json.dumps(result, default=str),
                }
            )
    else:
        logger.warning(
            "Agentic loop reached max_iterations=%d; forcing a final answer.",
            max_iterations,
        )

    # Forced final JSON (no tools) — guarantees a schema-valid response.
    final = client.chat.completions.create(
        model=deployment,
        temperature=temperature,
        messages=convo
        + [
            {
                "role": "user",
                "content": (
                    "Now respond with ONLY the final JSON object described in the "
                    "instructions. Do not call any tools and do not add commentary."
                ),
            }
        ],
        response_format={"type": "json_object"},
    )
    return extract_json_text(final.choices[0].message.content or "{}") or "{}"


# --------------------------------------------------------------------------- #
#  Tool: full-document search
# --------------------------------------------------------------------------- #

class DocumentSearchTool:
    """Search the *full* source document text and return matching snippets.

    Unlike dumping a truncated document into the prompt, this lets the agent
    retrieve evidence on demand from the entire document — important when the
    document is larger than what fits comfortably in context.
    """

    name = "search_document"

    def __init__(self, document_text: str | None, *, context_chars: int = 300) -> None:
        self._text = document_text or ""
        self._context = context_chars

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Search the FULL source document text for a term or number and "
                    "return matching snippets with surrounding context. Use this to "
                    "verify low-confidence values, locate a metric in a financial "
                    "table, or find a value that is missing from the extracted data."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Word, phrase, or number to find (case-insensitive "
                                "substring match), e.g. 'total capital ratio' or '14.2'."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum snippets to return (1-10, default 5).",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    def run(self, args: dict) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "No query provided. Supply a non-empty 'query' string."
        if not self._text:
            return "The source document text is unavailable for searching."

        try:
            max_results = int(args.get("max_results", 5))
        except (TypeError, ValueError):
            max_results = 5
        max_results = max(1, min(max_results, 10))

        haystack = self._text
        lowered = haystack.lower()
        needle = query.lower()

        snippets: list[str] = []
        start = 0
        while len(snippets) < max_results:
            idx = lowered.find(needle, start)
            if idx == -1:
                break
            ctx_start = max(0, idx - self._context)
            ctx_end = min(len(haystack), idx + len(needle) + self._context)
            snippet = " ".join(haystack[ctx_start:ctx_end].split())
            snippets.append(snippet)
            start = idx + len(needle)

        if not snippets:
            return (
                f"No matches found for '{query}'. Try a shorter term, a synonym, or "
                f"just the numeric value."
            )

        lines = [f"Found {len(snippets)} match(es) for '{query}':"]
        for i, snippet in enumerate(snippets, 1):
            lines.append(f"[{i}] …{snippet}…")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Tool: safe calculator
# --------------------------------------------------------------------------- #

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_arith(node: ast.AST) -> float:
    """Recursively evaluate a restricted arithmetic AST node."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric constants are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("operator not allowed")
        return op(_eval_arith(node.left), _eval_arith(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError("unary operator not allowed")
        return op(_eval_arith(node.operand))
    raise ValueError("unsupported expression element")


def safe_calculate(expression: str) -> float:
    """Evaluate a basic arithmetic *expression* with no names/calls/attributes.

    Supports + - * / // % ** and parentheses on numeric literals only. Raises
    ``ValueError``/``SyntaxError`` on anything else.
    """
    tree = ast.parse(expression, mode="eval")
    return _eval_arith(tree.body)


class CalculatorTool:
    """Deterministic arithmetic so the agent never has to guess at math."""

    name = "calculate"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Evaluate a basic arithmetic expression exactly. Use this for unit "
                    "conversions (e.g. '7200 / 1000' to convert millions to billions) "
                    "and consistency checks instead of doing mental math. Supports "
                    "+ - * / // % ** and parentheses on numbers only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Arithmetic expression, e.g. '1411043 / 1000'.",
                        }
                    },
                    "required": ["expression"],
                },
            },
        }

    def run(self, args: dict) -> str:
        expression = str(args.get("expression") or "").strip()
        if not expression:
            return "No expression provided. Supply an 'expression' string."
        try:
            result = safe_calculate(expression)
        except Exception as exc:
            return f"ERROR: could not evaluate '{expression}': {exc}"
        return f"{expression} = {result}"


# --------------------------------------------------------------------------- #
#  Tool: read PDF text
# --------------------------------------------------------------------------- #

class PdfTextTool:
    """Read the first pages of a PDF (via PyMuPDF) to disambiguate content."""

    name = "read_document_text"

    def __init__(
        self,
        pdf_path: str | Path,
        *,
        default_pages: int = 2,
        max_pages: int = 5,
        char_limit: int = 6000,
    ) -> None:
        self._path = Path(pdf_path)
        self._default_pages = default_pages
        self._max_pages = max_pages
        self._char_limit = char_limit

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Read the text of the first N pages of the PDF being classified. "
                    "Use this only when the filename is ambiguous and you need to look "
                    "inside the document (e.g. the cover or title page) to identify the "
                    "institution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "num_pages": {
                            "type": "integer",
                            "description": (
                                f"How many leading pages to read "
                                f"(1-{self._max_pages}, default {self._default_pages})."
                            ),
                        }
                    },
                    "required": [],
                },
            },
        }

    def run(self, args: dict) -> str:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return "ERROR: PDF reading is unavailable (PyMuPDF is not installed)."

        try:
            num_pages = int(args.get("num_pages", self._default_pages))
        except (TypeError, ValueError):
            num_pages = self._default_pages
        num_pages = max(1, min(num_pages, self._max_pages))

        if not self._path.exists():
            return f"ERROR: file not found: {self._path.name}"

        try:
            doc = fitz.open(self._path)
        except Exception as exc:
            return f"ERROR: cannot open PDF '{self._path.name}': {exc}"

        try:
            pages: list[str] = []
            for i in range(min(num_pages, doc.page_count)):
                text = doc.load_page(i).get_text().strip()
                pages.append(f"--- Page {i + 1} ---\n{text}")
        finally:
            doc.close()

        joined = "\n\n".join(pages).strip()
        if not joined:
            return "No extractable text found (the PDF may be scanned/image-only)."
        return joined[: self._char_limit]
