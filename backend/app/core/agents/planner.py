import json
import logging
import re
from typing import List, Dict
from .base import BaseAgent
from app.prompts import build_planner_prompt

logger = logging.getLogger(__name__)

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

        try:
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                stream=True,
                max_tokens=1024,
            )
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

            # Second safety net: override scope on list tools if counting intent
            if is_counting:
                full_response = _override_scope_for_counting(full_response)

            return full_response

        except Exception as e:
            logger.error(f"LLM plan generation failed: {e}")
            return json.dumps({"error": "Failed to generate plan."})
