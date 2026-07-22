import logging
from app.core.llm_manager import LLMManager

logger = logging.getLogger(__name__)

class BaseAgent:
    """Base class for all conversational and processing agents."""
    def __init__(self, llm_manager: LLMManager):
        self.llm_manager = llm_manager

    def get_llm(self, model_name: str = "gemma-local"):
        try:
            return self.llm_manager.get_model(model_name)
        except Exception as e:
            logger.error(f"Failed to load LLM '{model_name}': {e}")
            return None
