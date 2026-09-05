import json
import logging
import re
from typing import List, Dict, Optional
from .base import BaseAgent
from app.prompts import build_planner_prompt

logger = logging.getLogger(__name__)


def _build_plan_grammar(tool_names: List[str]):
    """
    Grammar-constrains the plan JSON so every step's "tool" field can ONLY be
    a name from `tool_names` — the exact set the model was actually offered
    this turn. This makes inventing a tool name structurally impossible
    (the sampler simply cannot produce those tokens), rather than catching it
    after the fact with a validation-error message once the plan is already
    generated. "plan": [] + "direct_response" stays a fully legal shape, so
    the model always has a real way to say "I don't have a tool for this"
    instead of being forced to pick something from the enum just to satisfy
    the schema.

    Returns None if there are no tools to constrain against (grammar with an
    empty enum is unsatisfiable) or if grammar compilation fails for any
    reason — callers fall back to plain JSON-object mode in that case.
    """
    if not tool_names:
        return None
    try:
        from llama_cpp import LlamaGrammar
    except ImportError:
        return None

    schema = {
        "type": "object",
        "properties": {
            "direct_response": {"type": "string"},
            "clarifying_question": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string"},
                        "tool": {"type": "string", "enum": list(tool_names)},
                        "reason": {"type": "string"},
                        "arguments": {"type": "object"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "foreach": {"type": ["string", "null"]},
                        "fetch_scope": {"type": "string", "enum": ["single", "sample", "exhaustive"]},
                    },
                    "required": ["step_id", "tool", "arguments"],
                },
            },
        },
    }
    try:
        return LlamaGrammar.from_json_schema(json.dumps(schema))
    except Exception as e:
        logger.warning(f"[PlannerAgent] Grammar compile failed, falling back to json_object mode: {e}")
        return None

# Tool name prefixes that are clearly read-only list/search operations.
# These are safe to force to exhaustive when counting intent is detected.
_LIST_TOOL_PREFIXES = (
    "list_", "search_", "get_commits", "get_issues", "get_pull_requests",
    "list_commits", "list_issues", "list_repositories", "list_files",
)


def _override_scope_for_counting(plan_json: str) -> str:
    """
    Post-process the LLM plan JSON.
    If the query was a counting query and a LIST-type tool still has
    fetch_scope = 'single' or 'sample', force it to 'exhaustive'.
    """
    try:
        plan = json.loads(plan_json)
    except Exception:
        return plan_json

    steps = plan.get("plan", [])
    changed = False
    for step in steps:
        tool = step.get("tool", "")
        scope = step.get("fetch_scope", "single")
        is_list_tool = any(tool.startswith(p) or tool == p.rstrip("_") for p in _LIST_TOOL_PREFIXES)
        if is_list_tool and scope in ("single", "sample"):
            step["fetch_scope"] = "exhaustive"
            changed = True
            logger.info(
                f"[PlannerAgent] Auto-upgraded fetch_scope on '{tool}' "
                f"from '{scope}' → 'exhaustive' (counting intent detected)"
            )

    return json.dumps(plan) if changed else plan_json


class PlannerAgent(BaseAgent):
    """Responsible for analyzing user intent and deciding which tools to call."""

    def generate_plan(
        self,
        user_message: str,
        tools_str: str,
        entity_context: str,
        chat_history: List[Dict],
        token_callback=None,
        is_counting: bool = False,
        tool_names: Optional[List[str]] = None,
        attachments: Optional[List[Dict]] = None,
    ) -> str:
        llm = self.get_llm()
        if not llm:
            return json.dumps({"error": "LLM not loaded."})

        # Inject a deterministic hint at the top of the user message so the
        # LLM receives an explicit in-context reminder immediately before its
        # JSON output — this matters far more than a distant system-prompt rule.
        if is_counting:
            augmented_message = (
                "[SYSTEM HINT] The user's query requires a TOTAL or COUNT. "
                "ALL list/search steps MUST have fetch_scope = \"exhaustive\". "
                "Using \"single\" or \"sample\" here would give a wrong answer.\n\n"
                + user_message
            )
        else:
            augmented_message = user_message

        system_prompt = build_planner_prompt(tools_str, entity_context)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(chat_history)
        messages.append({"role": "user", "content": augmented_message})
        # Real vision input for Agent Mode: if an image is attached this turn
        # and the active model has a vision chat_handler wired, the planner
        # gets to actually see it — this is what lets "what's in this
        # image?" produce a direct_response grounded in the real image
        # instead of just OCR'd text (see BaseAgent._attach_vision_images).
        # Grammar-constrained sampling below is unaffected: the grammar
        # constrains which tokens can be emitted, not what fed the model's
        # context, so it composes fine with multimodal input.
        self._attach_vision_images(messages, attachments)

        grammar = _build_plan_grammar(tool_names or [])
        base_kwargs = dict(messages=messages, temperature=0.1, stream=True, max_tokens=1024)

        def _run_completion(use_grammar: bool):
            kwargs = dict(base_kwargs)
            if use_grammar and grammar is not None:
                kwargs["grammar"] = grammar
            else:
                # No tool set to constrain against, grammar compile failed, or
                # this is the plain-JSON-mode fallback retry.
                kwargs["response_format"] = {"type": "json_object"}
            response = llm.create_chat_completion(**kwargs)
            full_response = ""
            print("\n--- PLANNER OUTPUT STREAM ---")
            for chunk in response:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        token = delta["content"]
                        full_response += token
                        print(token, end="", flush=True)
                        if token_callback:
                            token_callback(token)
            print("\n-----------------------------\n")
            return full_response

        try:
            try:
                full_response = _run_completion(use_grammar=True)
            except Exception as e:
                if grammar is not None:
                    logger.warning(f"[PlannerAgent] Grammar-constrained generation failed, retrying without it: {e}")
                    full_response = _run_completion(use_grammar=False)
                else:
                    raise

            self._log_token_usage(llm, messages, full_response, "agent")

            # Second safety net: override scope on list tools if counting intent
            if is_counting:
                full_response = _override_scope_for_counting(full_response)

            return full_response

        except Exception as e:
            logger.error(f"LLM plan generation failed: {e}")
            return json.dumps({"error": "Failed to generate plan."})
