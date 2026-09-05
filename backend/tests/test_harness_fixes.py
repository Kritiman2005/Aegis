"""
Regression tests for the harness-layer fixes made to ChatAgent/ExecutorAgent:

1. get_searched_tools no longer makes an LLM call to rank/search tools
   (MCP removal made that ranking pointless — see chat.py's
   _COUNTING_HINT_RE comment) and derives is_counting via a cheap regex.
2. The two Chat Mode classifiers (export-intent, compound-question) are
   merged into one LLM call when both cheap gates fire in the same turn.
3. Per-step argument generation in execute_plan gets a bounded retry with
   a reinforced instruction before aborting the whole plan, mirroring the
   planner's own retry loop.
4. _call_llm_text detects a chat answer that degenerated into raw JSON,
   stops forwarding it live, and regenerates with a corrective
   instruction — falling back to best-effort text extraction rather than
   ever surfacing the raw JSON.

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
    _sanitize_one_shot_text,
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


# ── Merged export + compound-question classifier ──────────────────────────

def test_classify_export_and_compound_parses_both_fields(monkeypatch):
    agent = make_agent()
    fake = FakeLLM([json.dumps({
        "is_export": True,
        "format": "docx",
        "parts": ["which items are out of stock", "what is the unit price of the keyboard"],
    })])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)

    export_fmt, parts = agent._classify_export_and_compound("some message")

    assert export_fmt == "docx"
    assert parts == ["which items are out of stock", "what is the unit price of the keyboard"]
    assert len(fake.calls) == 1  # one call answers both questions


def test_classify_export_and_compound_no_export_no_compound(monkeypatch):
    agent = make_agent()
    fake = FakeLLM([json.dumps({"is_export": False, "format": None, "parts": []})])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)

    export_fmt, parts = agent._classify_export_and_compound("hello there")

    assert export_fmt is None
    assert parts is None


def test_classify_export_and_compound_handles_garbage_response(monkeypatch):
    agent = make_agent()
    fake = FakeLLM(["not json at all"])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)

    export_fmt, parts = agent._classify_export_and_compound("some message")

    assert export_fmt is None
    assert parts is None


def test_classify_export_intent_still_works_standalone(monkeypatch):
    agent = make_agent()
    fake = FakeLLM([json.dumps({"is_export": True, "format": "xlsx"})])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)

    assert agent._classify_export_intent("export this as excel") == "xlsx"


def test_decompose_compound_question_still_works_standalone(monkeypatch):
    agent = make_agent()
    fake = FakeLLM([json.dumps({"parts": ["part one", "part two"]})])
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: fake)

    parts = agent._decompose_compound_question("part one, and part two?")
    assert parts == ["part one", "part two"]


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


# ── execute_plan: per-step retry on invalid arguments ───────────────────────

@pytest.mark.asyncio
async def test_execute_plan_retries_invalid_arguments_before_succeeding(monkeypatch):
    from app.core.agents.chat import AgentState

    agent = make_agent()

    # First attempt: missing the required "query" field (fails jsonschema
    # validation). Second attempt (the retry this fix added): valid.
    fake_executor_llm = FakeLLM([
        json.dumps({}),
        json.dumps({"query": "invoice"}),
    ])
    monkeypatch.setattr(agent.executor, "get_llm", lambda model_name=None: fake_executor_llm)
    monkeypatch.setattr(agent.executor, "_log_token_usage", lambda *a, **kw: None)

    # Isolate this test to the argument-generation retry loop — don't
    # actually touch the filesystem for tool execution.
    async def fake_exec_fs_tool(tool_name, arguments):
        assert arguments == {"query": "invoice"}
        return {"success": True, "text": "invoice.pdf"}

    monkeypatch.setattr(agent, "_execute_filesystem_tool", fake_exec_fs_tool)

    agent.state = AgentState.EXECUTING
    agent.plan = [{
        "step_id": "step_1",
        "tool": "search_local_files",
        "arguments": {},
        "reason": "find the invoice",
    }]

    events = [e async for e in agent.execute_plan(token_callback=None)]

    assert len(fake_executor_llm.calls) == 2  # one bad attempt + one retry
    retry_user_message = fake_executor_llm.calls[1]["messages"][-1]["content"]
    assert "invalid" in retry_user_message.lower()

    failed = [e for e in events if e.get("status") == "failed"]
    assert not failed, f"plan should have succeeded after the retry, got: {failed}"
    assert any("Execution complete" in e.get("text", "") for e in events)


@pytest.mark.asyncio
async def test_execute_plan_aborts_after_exhausting_retries(monkeypatch):
    from app.core.agents.chat import AgentState

    agent = make_agent()

    # Both attempts invalid (missing required "query") — should abort
    # after the second, not loop forever or hang the plan.
    fake_executor_llm = FakeLLM([
        json.dumps({}),
        json.dumps({}),
    ])
    monkeypatch.setattr(agent.executor, "get_llm", lambda model_name=None: fake_executor_llm)
    monkeypatch.setattr(agent.executor, "_log_token_usage", lambda *a, **kw: None)

    agent.state = AgentState.EXECUTING
    agent.plan = [{
        "step_id": "step_1",
        "tool": "search_local_files",
        "arguments": {},
        "reason": "find the invoice",
    }]

    events = [e async for e in agent.execute_plan(token_callback=None)]

    assert len(fake_executor_llm.calls) == 2  # bounded — doesn't retry forever
    assert agent.state == AgentState.IDLE
    assert agent.plan is None
    assert any(e.get("status") == "failed" for e in events)


# ── _sanitize_one_shot_text: the planner's direct_response/clarifying_ ────
# question/reason fields, which don't stream and can't cheaply regenerate.

@pytest.mark.parametrize("leak,fallback,expected", [
    ('{"answer": "Paris is the capital."}', "fallback", "Paris is the capital."),
    ("Paris is the capital.", "fallback", "Paris is the capital."),
    ("", "fallback", ""),
    ('{"a": 1}', "fallback", "fallback"),  # nothing extractable
])
def test_sanitize_one_shot_text(leak, fallback, expected):
    assert _sanitize_one_shot_text(leak, fallback=fallback) == expected


def test_sanitize_one_shot_text_handles_none():
    assert _sanitize_one_shot_text(None, fallback="fallback") is None


# ── Agent Mode: direct_response / clarifying_question / step reason ───────
# never reach the user as raw JSON either, mirroring the Chat Mode fix.

async def _no_document_context(self, message, attachments, status_callback=None):
    return ""


@pytest.mark.asyncio
async def test_agent_mode_direct_response_json_leak_is_sanitized(monkeypatch):
    agent = make_agent(connection_id="test_direct_response_leak")
    monkeypatch.setattr(ChatAgent, "_get_document_context", _no_document_context)

    leaked_direct_response = json.dumps({"answer": "The capital of France is Paris."})
    monkeypatch.setattr(
        agent.planner, "generate_plan",
        lambda *a, **kw: json.dumps({"plan": [], "direct_response": leaked_direct_response}),
    )

    result = await agent.handle_message("what is the capital of France?", mode="agent")

    assert result == "The capital of France is Paris."
    assert "{" not in result


@pytest.mark.asyncio
async def test_agent_mode_clarifying_question_json_leak_is_sanitized(monkeypatch):
    agent = make_agent(connection_id="test_clarifying_question_leak")
    monkeypatch.setattr(ChatAgent, "_get_document_context", _no_document_context)

    leaked_question = json.dumps({"question": "Which folder should I search in?"})
    monkeypatch.setattr(
        agent.planner, "generate_plan",
        lambda *a, **kw: json.dumps({"plan": [], "clarifying_question": leaked_question}),
    )

    result = await agent.handle_message("find my invoice", mode="agent")

    assert result == "Which folder should I search in?"
    assert "{" not in result


@pytest.mark.asyncio
async def test_agent_mode_direct_response_normal_text_passes_through(monkeypatch):
    agent = make_agent(connection_id="test_direct_response_normal")
    monkeypatch.setattr(ChatAgent, "_get_document_context", _no_document_context)

    monkeypatch.setattr(
        agent.planner, "generate_plan",
        lambda *a, **kw: json.dumps({"plan": [], "direct_response": "The capital of France is Paris."}),
    )

    result = await agent.handle_message("what is the capital of France?", mode="agent")

    assert result == "The capital of France is Paris."
