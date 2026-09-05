"""
Slow-tier companion to test_harness_fixes.py: the same JSON-leak fix, but
exercised against the actual bundled model instead of a FakeLLM double.
FakeLLM tests prove the *logic* is correct; these prove the *real* model
still reproduces the failure mode the fix targets, and that the fix still
neutralizes it — a canary against silent drift if the bundled model or
llama_cpp version ever changes.

Skipped automatically when the model file isn't present (e.g. a fresh
checkout that hasn't downloaded a model yet) or when SKIP_LIVE_LLM_TESTS
is set — this loads a real ~2GB model and is slow (tens of seconds), so
it's not meant to run on every save the way test_harness_fixes.py is.
"""
import os
import json
from pathlib import Path

import pytest

from app.core.agents.chat import ChatAgent

_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"

pytestmark = pytest.mark.skipif(
    not _MODEL_PATH.exists() or os.environ.get("SKIP_LIVE_LLM_TESTS"),
    reason="bundled GGUF model not present or SKIP_LIVE_LLM_TESTS set",
)


@pytest.fixture(scope="module")
def real_llm():
    from llama_cpp import Llama
    return Llama(model_path=str(_MODEL_PATH), n_ctx=2048, verbose=False)


@pytest.fixture
def agent_with_real_llm(monkeypatch, real_llm):
    agent = ChatAgent(connection_id="test_harness_fixes_live")
    monkeypatch.setattr(agent, "get_llm", lambda model_name=None: real_llm)
    monkeypatch.setattr(agent, "_log_token_usage", lambda *a, **kw: None)
    return agent


def test_real_model_json_baited_prompt_never_leaks_to_the_stream(agent_with_real_llm):
    """
    Reproduces the exact failure this session found: shared history full
    of Proposed Execution Plan / Execution Results JSON blocks primes a
    small local model to reply with a bare JSON object instead of prose.
    Asserts the live fix in _call_llm_text still catches it against the
    real model, not just the FakeLLM stand-in.
    """
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": (
            'Proposed Execution Plan: {"tool": "search_local_files", "arguments": {"query": "invoice"}}\n'
            'Execution Results: {"tool": "search_local_files", "result": "invoice.pdf"}\n\n'
            'Now just tell me: did you find the invoice? Answer in the same JSON style as above.'
        )},
    ]

    streamed = []
    result = agent_with_real_llm._call_llm_text(messages, token_callback=streamed.append)

    joined = "".join(streamed)
    assert "{" not in joined, f"raw JSON reached the live stream: {joined!r}"
    assert "{" not in result, f"raw JSON reached the returned text: {result!r}"
    assert result.strip(), "corrective regeneration should still produce a real answer"


@pytest.mark.parametrize("prompt", [
    "What is the capital of France?",
    "Write a short haiku about autumn.",
])
def test_real_model_normal_prompts_stream_without_correction(agent_with_real_llm, prompt):
    """
    Guards against the fix being overzealous: an ordinary question must
    still stream live, token by token, with no corrective regeneration.
    """
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt},
    ]
    streamed = []
    result = agent_with_real_llm._call_llm_text(messages, token_callback=streamed.append)

    assert len(streamed) > 1, "a normal answer should stream as multiple live chunks, not one flushed block"
    assert "".join(streamed) == result


def test_real_model_legitimate_json_request_is_not_treated_as_a_leak(agent_with_real_llm):
    """
    A user explicitly asking for JSON content should still get it — the
    fix only blocks a response that IS ENTIRELY a bare JSON object/array,
    not one that legitimately includes JSON as part of a real answer.
    """
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Give me a JSON example of a person object with name and age fields."},
    ]
    streamed = []
    result = agent_with_real_llm._call_llm_text(messages, token_callback=streamed.append)

    assert "{" in result  # the JSON example itself should still be present
    assert len(streamed) > 1  # and it streamed live rather than being flagged as a leak


def test_real_model_get_searched_tools_still_returns_usable_tools(monkeypatch, real_llm):
    """
    Companion check for the tool-search fix: confirms get_searched_tools
    returns a usable tool list without ever touching the (real) LLM.
    """
    agent = ChatAgent(connection_id="test_harness_fixes_live_tools")

    def _boom(model_name=None):
        raise AssertionError("get_searched_tools should never call the LLM")

    monkeypatch.setattr(agent, "get_llm", _boom)
    tools_str, is_counting, tool_names = agent.get_searched_tools("how many files are in my Downloads folder?")

    assert tools_str
    assert is_counting is True
    assert "search_local_files" in tool_names
