"""
Regression tests for the harness-layer fixes made to ChatAgent/ExecutorAgent:

1. get_searched_tools makes no LLM call to rank/search tools — it derives
   is_counting via a cheap regex, and falls back to mcp_registry's FTS5
   index (not an LLM ranking pass) only once the combined tool count would
   overflow the planner's context budget.
2. The two Chat Mode classifiers (export-intent, compound-question) are
   merged into one LLM call when both cheap gates fire in the same turn.
3. _call_llm_text detects a chat answer that degenerated into raw JSON,
   stops forwarding it live, and regenerates with a corrective
   instruction — falling back to best-effort text extraction rather than
   ever surfacing the raw JSON.
4. ExecutorAgent.generate_arguments correctly plumbs an optional
   retry_note into the prompt when one is supplied.

None of these tests load a real GGUF model — every LLM call is a FakeLLM
double, so the suite stays fast and deterministic. See
test_harness_fixes_live.py for the slower tests that exercise the same
fixes against the actual bundled model.
"""
import json
import pytest

from app.core.agents.chat import (
    ChatAgent,
    _looks_like_pure_json,
    _extract_text_from_json_leak,
)
from app.core.agents.executor import ExecutorAgent


class FakeLLM:
    """
    Minimal llama_cpp.Llama stand-in. `responses` is a list of strings
    consumed one per create_chat_completion call (in order) — each is
    returned verbatim as the assistant content, streamed as a single
    chunk when stream=True or as a plain completion otherwise. Records
    every call's kwargs in `.calls` for assertions.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeLLM ran out of canned responses")
        content = self._responses.pop(0)

        if kwargs.get("stream"):
            def _gen():
                yield {"choices": [{"delta": {"content": content}}]}
            return _gen()

        return {"choices": [{"message": {"content": content}}]}

    def tokenize(self, data: bytes):
        return list(data)


def make_agent(connection_id="test_harness_fixes"):
    return ChatAgent(connection_id=connection_id)


# ── _looks_like_pure_json / _extract_text_from_json_leak (pure functions) ──

@pytest.mark.parametrize("text", [
    '{"tool": "search_local_files", "result": "invoice.pdf"}',
    '  {"is_export": true, "format": "pdf"}  ',
    '["a", "b", "c"]',
    '{}',
])
def test_looks_like_pure_json_true_cases(text):
    assert _looks_like_pure_json(text) is True


@pytest.mark.parametrize("text", [
    "The capital of France is Paris.",
    'Here is an example: {"name": "John"}',
    "",
    "   ",
    "{not valid json",
    "just a { brace mid-sentence, nothing more",
])
def test_looks_like_pure_json_false_cases(text):
    assert _looks_like_pure_json(text) is False


def test_extract_text_from_json_leak_finds_nested_string():
    leak = json.dumps({"tool": "x", "output": {"note": "Yes, the invoice was found in invoice.pdf."}})
    assert _extract_text_from_json_leak(leak) == "Yes, the invoice was found in invoice.pdf."


def test_extract_text_from_json_leak_finds_string_in_list():
    leak = json.dumps(["short", "This is a long enough sentence to count as real content."])
    assert _extract_text_from_json_leak(leak) == "This is a long enough sentence to count as real content."


def test_extract_text_from_json_leak_returns_none_when_nothing_usable():
    leak = json.dumps({"is_export": True, "count": 3, "short": "hi"})
    assert _extract_text_from_json_leak(leak) is None


def test_extract_text_from_json_leak_returns_none_on_invalid_json():
    assert _extract_text_from_json_leak("not json at all") is None


# ── get_searched_tools: no LLM call, regex-based is_counting ──────────────

def test_get_searched_tools_never_calls_the_llm(monkeypatch):
    agent = make_agent()

    def _boom(model_name=None):
        raise AssertionError("get_searched_tools should never touch the LLM anymore")

    monkeypatch.setattr(agent, "get_llm", _boom)

    tools_str, is_counting, tool_names = agent.get_searched_tools("read notes.txt")

    assert tools_str  # local tools (filesystem tools always exist)
    assert set(tool_names) == {t["name"] for t in agent._all_available_tools()}


@pytest.mark.parametrize("query,expected", [
    ("how many files are in my Downloads folder?", True),
    ("list all pdfs in my documents", True),
    ("what is the total number of screenshots I have", True),
    ("read notes.txt for me", False),
    ("summarize this document", False),
])
def test_get_searched_tools_is_counting_regex(monkeypatch, query, expected):
    agent = make_agent()
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: (_ for _ in ()).throw(AssertionError("no LLM")))
    _, is_counting, _ = agent.get_searched_tools(query)
    assert is_counting is expected


# ── _call_llm_text: JSON-leak detection and correction ─────────────────────

def test_call_llm_text_normal_case_streams_once_no_retry(monkeypatch):
    agent = make_agent()
    fake = FakeLLM(["The capital of France is Paris."])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)
    monkeypatch.setattr(agent, "_log_token_usage", lambda *a, **kw: None)

    streamed = []
    result = agent._call_llm_text(
        [{"role": "user", "content": "What is the capital of France?"}],
        token_callback=streamed.append,
    )

    assert result == "The capital of France is Paris."
    assert "".join(streamed) == "The capital of France is Paris."
    assert len(fake.calls) == 1  # no corrective retry needed


def test_call_llm_text_detects_leak_and_corrects_without_exposing_json(monkeypatch):
    agent = make_agent()
    leaked_json = json.dumps({"tool": "search_local_files", "result": "invoice.pdf"})
    clean_answer = "Yes, the invoice was found. It is located in invoice.pdf."
    fake = FakeLLM([leaked_json, clean_answer])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)
    monkeypatch.setattr(agent, "_log_token_usage", lambda *a, **kw: None)

    streamed = []
    result = agent._call_llm_text(
        [{"role": "user", "content": "did you find the invoice? Answer in JSON."}],
        token_callback=streamed.append,
    )

    assert result == clean_answer
    # The whole point of the fix: nothing JSON-shaped should ever have
    # reached whatever renders these tokens (e.g. the chat UI).
    joined = "".join(streamed)
    assert "{" not in joined
    assert joined == clean_answer
    assert len(fake.calls) == 2  # one leaked attempt + one corrective retry
    # The retry must actually ask the model to stop producing JSON.
    retry_messages = fake.calls[1]["messages"]
    assert any("raw JSON" in m.get("content", "") for m in retry_messages)


def test_call_llm_text_double_leak_extracts_usable_text(monkeypatch):
    agent = make_agent()
    first_leak = json.dumps({"a": 1})
    second_leak = json.dumps({"note": "Here is the actual answer buried in JSON anyway."})
    fake = FakeLLM([first_leak, second_leak])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)
    monkeypatch.setattr(agent, "_log_token_usage", lambda *a, **kw: None)

    streamed = []
    result = agent._call_llm_text(
        [{"role": "user", "content": "anything"}],
        token_callback=streamed.append,
    )

    assert result == "Here is the actual answer buried in JSON anyway."
    assert "{" not in "".join(streamed)


def test_call_llm_text_double_leak_falls_back_to_apology(monkeypatch):
    agent = make_agent()
    first_leak = json.dumps({"a": 1})
    second_leak = json.dumps({"b": 2, "c": True})  # nothing string-shaped to salvage
    fake = FakeLLM([first_leak, second_leak])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)
    monkeypatch.setattr(agent, "_log_token_usage", lambda *a, **kw: None)

    streamed = []
    result = agent._call_llm_text(
        [{"role": "user", "content": "anything"}],
        token_callback=streamed.append,
    )

    assert "{" not in result
    assert "{" not in "".join(streamed)
    assert result  # some non-empty, human-readable fallback was shown


# ── ExecutorAgent: retry_note plumbing ──────────────────────────────────────

def test_generate_arguments_includes_retry_note_when_given(monkeypatch):
    from app.core.llm_manager import LLMManager
    executor = ExecutorAgent(llm_manager=LLMManager.__new__(LLMManager))
    fake = FakeLLM([json.dumps({"query": "invoice"})])
    monkeypatch.setattr(executor, "get_llm", lambda model_name=None: fake)
    monkeypatch.setattr(executor, "_log_token_usage", lambda *a, **kw: None)

    args = executor.generate_arguments(
        tool_name="search_local_files",
        tool_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        overall_plan=[],
        step_reason="find the invoice",
        prior_results=[],
        entity_context="",
        user_request="[]",
        retry_note="\n\n[SYSTEM]: Your previous attempt was invalid: 'query' is a required property.",
    )

    assert args == {"query": "invoice"}
    sent_user_message = fake.calls[0]["messages"][-1]["content"]
    assert "Your previous attempt was invalid" in sent_user_message


def test_generate_arguments_without_retry_note_omits_it(monkeypatch):
    from app.core.llm_manager import LLMManager
    executor = ExecutorAgent(llm_manager=LLMManager.__new__(LLMManager))
    fake = FakeLLM([json.dumps({"query": "invoice"})])
    monkeypatch.setattr(executor, "get_llm", lambda model_name=None: fake)
    monkeypatch.setattr(executor, "_log_token_usage", lambda *a, **kw: None)

    executor.generate_arguments(
        tool_name="search_local_files",
        tool_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        overall_plan=[],
        step_reason="find the invoice",
        prior_results=[],
        entity_context="",
        user_request="[]",
    )

    sent_user_message = fake.calls[0]["messages"][-1]["content"]
    assert "[SYSTEM]" not in sent_user_message
