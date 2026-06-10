"""Tests for the shared agentic infrastructure (src/agents/agentic.py).

These exercise the deterministic tools and the tool-calling loop using a fake
OpenAI client, so they run without any Azure credentials or network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.agentic import (
    CalculatorTool,
    DocumentSearchTool,
    PdfTextTool,
    extract_json_text,
    run_tool_loop,
    safe_calculate,
)


# --------------------------------------------------------------------------- #
#  Fake OpenAI client
# --------------------------------------------------------------------------- #

class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [SimpleNamespace(message=message)]


class _FakeCompletions:
    def __init__(self, scripted: list[_FakeMessage]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    def create(self, **kwargs) -> _FakeResponse:
        self.calls.append(kwargs)
        message = self._scripted.pop(0) if self._scripted else _FakeMessage(content="{}")
        return _FakeResponse(message)


class FakeClient:
    """Minimal stand-in for the AzureOpenAI client used by run_tool_loop."""

    def __init__(self, scripted: list[_FakeMessage]) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(scripted))

    @property
    def calls(self) -> list[dict]:
        return self.chat.completions.calls


def _tool_call(name: str, arguments: str = "{}", call_id: str = "call_1") -> _FakeMessage:
    return _FakeMessage(tool_calls=[_FakeToolCall(call_id, name, arguments)])


# --------------------------------------------------------------------------- #
#  extract_json_text
# --------------------------------------------------------------------------- #

def test_extract_json_plain():
    assert extract_json_text('{"a": 1}') == '{"a": 1}'


def test_extract_json_fenced():
    out = extract_json_text('```json\n{"a": 1}\n```')
    assert json.loads(out) == {"a": 1}


def test_extract_json_with_prose():
    out = extract_json_text('Sure! {"a": 1} done.')
    assert json.loads(out) == {"a": 1}


@pytest.mark.parametrize("value", ["no json here", "", None])
def test_extract_json_none(value):
    assert extract_json_text(value) is None


# --------------------------------------------------------------------------- #
#  CalculatorTool / safe_calculate
# --------------------------------------------------------------------------- #

def test_safe_calculate_conversions():
    assert safe_calculate("7200 / 1000") == 7.2
    assert safe_calculate("1411043 / 1000") == 1411.043
    assert safe_calculate("2 ** 3 + 1") == 9
    assert safe_calculate("-(5) + 2") == -3


@pytest.mark.parametrize("expr", ["__import__('os')", "os.system('x')", "a + b"])
def test_safe_calculate_rejects_non_numeric(expr):
    with pytest.raises((ValueError, SyntaxError)):
        safe_calculate(expr)


def test_calculator_tool_run():
    tool = CalculatorTool()
    assert "7.2" in tool.run({"expression": "7200/1000"})
    assert "ERROR" in tool.run({"expression": "__import__('os')"})
    assert "No expression" in tool.run({"expression": ""})
    # Schema is well-formed.
    assert tool.schema["function"]["name"] == "calculate"


# --------------------------------------------------------------------------- #
#  DocumentSearchTool
# --------------------------------------------------------------------------- #

def test_document_search_finds_value():
    doc = "Foo bar. The Total Capital Ratio was 14.2% in fiscal 2024. Baz."
    tool = DocumentSearchTool(doc, context_chars=15)
    out = tool.run({"query": "Total Capital Ratio"})
    assert "match" in out.lower()
    assert "14.2" in out


def test_document_search_no_match():
    tool = DocumentSearchTool("some text")
    assert "No matches" in tool.run({"query": "absent"})


def test_document_search_unavailable():
    assert "unavailable" in DocumentSearchTool("").run({"query": "x"}).lower()


def test_document_search_respects_max_results():
    tool = DocumentSearchTool("alpha " * 10)
    out = tool.run({"query": "alpha", "max_results": 3})
    assert out.startswith("Found 3 match(es)")


def test_document_search_empty_query():
    assert "No query" in DocumentSearchTool("text").run({"query": "  "})


# --------------------------------------------------------------------------- #
#  PdfTextTool
# --------------------------------------------------------------------------- #

def test_pdf_tool_missing_file():
    out = PdfTextTool("definitely_not_here.pdf").run({})
    assert "ERROR" in out


def test_pdf_tool_reads_sample_pdf():
    pytest.importorskip("fitz")
    repo_root = Path(__file__).resolve().parents[1]
    pdfs = sorted(repo_root.glob("*.pdf"))
    if not pdfs:
        pytest.skip("no sample PDFs available")
    out = PdfTextTool(pdfs[0]).run({"num_pages": 1})
    assert isinstance(out, str) and out
    assert ("Page 1" in out) or ("No extractable text" in out)


# --------------------------------------------------------------------------- #
#  run_tool_loop
# --------------------------------------------------------------------------- #

def test_loop_immediate_json_single_call():
    client = FakeClient([_FakeMessage(content='{"category": "pcu"}')])
    out = run_tool_loop(
        client,
        deployment="d",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        dispatch=lambda n, a: "",
    )
    assert json.loads(out) == {"category": "pcu"}
    assert len(client.calls) == 1  # no wasteful second call


def test_loop_calls_tool_then_answers():
    client = FakeClient(
        [
            _tool_call("search_document", '{"query": "capital"}'),
            _FakeMessage(content='{"answer": 42}'),
        ]
    )
    seen = {}

    def dispatch(name, args):
        seen["name"] = name
        seen["args"] = args
        return "found capital ratio 14.2%"

    out = run_tool_loop(
        client,
        deployment="d",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
        dispatch=dispatch,
    )
    assert json.loads(out) == {"answer": 42}
    assert seen == {"name": "search_document", "args": {"query": "capital"}}
    assert len(client.calls) == 2
    # The tool result was fed back into the second model call.
    second_call_messages = client.calls[1]["messages"]
    assert any(
        m.get("role") == "tool" and "14.2" in m.get("content", "")
        for m in second_call_messages
    )


def test_loop_repairs_unparseable_answer():
    client = FakeClient(
        [
            _FakeMessage(content="I think it is a credit union."),
            _FakeMessage(content='{"category": "pcu", "confidence": 0.9}'),
        ]
    )
    out = run_tool_loop(
        client,
        deployment="d",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        dispatch=lambda n, a: "",
    )
    assert json.loads(out)["category"] == "pcu"
    assert len(client.calls) == 2
    # The repair call forces JSON and disables tools.
    assert client.calls[1].get("response_format") == {"type": "json_object"}
    assert "tools" not in client.calls[1]


def test_loop_dispatch_error_does_not_crash():
    client = FakeClient(
        [
            _tool_call("boom"),
            _FakeMessage(content='{"ok": 1}'),
        ]
    )

    def dispatch(name, args):
        raise RuntimeError("kaboom")

    out = run_tool_loop(
        client,
        deployment="d",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
        dispatch=dispatch,
    )
    assert json.loads(out) == {"ok": 1}
    tool_messages = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages and "ERROR" in tool_messages[0]["content"]


def test_loop_max_iterations_forces_answer():
    client = FakeClient(
        [
            _tool_call("search_document"),
            _tool_call("search_document"),
            _FakeMessage(content='{"done": true}'),
        ]
    )
    out = run_tool_loop(
        client,
        deployment="d",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
        dispatch=lambda n, a: "still searching",
        max_iterations=2,
    )
    assert json.loads(out) == {"done": True}
    # 2 loop iterations + 1 forced-final call.
    assert len(client.calls) == 3
    assert client.calls[2].get("response_format") == {"type": "json_object"}


def test_loop_does_not_mutate_caller_messages():
    client = FakeClient([_FakeMessage(content='{"x": 1}')])
    messages = [{"role": "user", "content": "hi"}]
    run_tool_loop(
        client,
        deployment="d",
        messages=messages,
        tools=[],
        dispatch=lambda n, a: "",
    )
    assert messages == [{"role": "user", "content": "hi"}]
