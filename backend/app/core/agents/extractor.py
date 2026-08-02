import json
import logging
from typing import List, Dict
from .base import BaseAgent
from app.prompts import ENTITY_EXTRACTOR_SYSTEM, build_entity_extractor_user_msg

logger = logging.getLogger(__name__)

class EntityExtractorAgent(BaseAgent):
    """Responsible for reviewing tool execution results and identifying key entities to save to memory."""
    
    def extract_entities(self, tool_results: List[Dict], user_prompt: str = "") -> str:
        llm = self.get_llm()
        if not llm:
            return json.dumps({"entities": []})

        results_text = json.dumps(tool_results, indent=2, ensure_ascii=False)
        messages = [
            {"role": "system", "content": ENTITY_EXTRACTOR_SYSTEM},
            {"role": "user", "content": build_entity_extractor_user_msg(results_text, user_prompt)}
        ]

        try:
            from app.core import context_config as ctx_cfg
            _max_tokens = ctx_cfg.get("extractor").get("max_tokens", 1024)
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=_max_tokens
            )
            return response["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return json.dumps({"entities": []})
