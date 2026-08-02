import json
import logging
from typing import List, Dict
from .base import BaseAgent
from app.prompts import build_planner_prompt

logger = logging.getLogger(__name__)

class PlannerAgent(BaseAgent):
    """Responsible for analyzing user intent and deciding which tools to call."""
    
    def generate_plan(self, user_message: str, tools_str: str, entity_context: str, chat_history: List[Dict], token_callback=None) -> str:
        llm = self.get_llm()
        if not llm:
            return json.dumps({"error": "LLM not loaded."})

        system_prompt = build_planner_prompt(tools_str, entity_context)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(chat_history)
        messages.append({"role": "user", "content": user_message})

        try:
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                stream=True,
                max_tokens=1024
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
            return full_response
        except Exception as e:
            logger.error(f"LLM plan generation failed: {e}")
            return json.dumps({"error": "Failed to generate plan."})
