import json
import logging
from typing import Dict, Any

from .base import BaseAgent
from app.prompts import build_executor_prompt

logger = logging.getLogger(__name__)

class ExecutorAgent(BaseAgent):
    """Responsible for generating exact JSON arguments for a single tool call."""
    
    def generate_arguments(self, 
                           tool_name: str, 
                           tool_schema: dict, 
                           overall_plan: list, 
                           step_reason: str, 
                           prior_results: dict, 
                           entity_context: str,
                           user_request: str) -> dict:
        
        llm = self.get_llm()
        if not llm:
            logger.error("LLM not loaded for ExecutorAgent.")
            return {}

        tool_schema_str = json.dumps(tool_schema, indent=2)
        overall_plan_str = json.dumps(overall_plan, indent=2)
        prior_results_str = json.dumps(prior_results, indent=2) if prior_results else "No previous steps executed yet."

        system_prompt = build_executor_prompt(
            tool_name=tool_name,
            tool_schema=tool_schema_str,
            overall_plan=overall_plan_str,
            step_reason=step_reason,
            prior_results=prior_results_str,
            entity_context=entity_context
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Original User Request: {user_request}\n\nPlease generate the exact arguments for `{tool_name}`."}
        ]
        
        try:
            # Deterministic, one-shot attempt
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0 # Strict deterministic output
            )
            
            raw_content = response.get("choices", [])[0].get("message", {}).get("content", "{}")
            return json.loads(raw_content)
        except Exception as e:
            logger.error(f"Executor LLM failed to generate arguments: {e}")
            return {}
