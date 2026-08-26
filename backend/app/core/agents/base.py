import logging
from app.core.llm_manager import LLMManager

logger = logging.getLogger(__name__)

class BaseAgent:
    """Base class for all conversational and processing agents."""
    def __init__(self, llm_manager: LLMManager):
        self.llm_manager = llm_manager

    def get_llm(self, model_name: str = None):
        """
        Get the active LLM. If model_name is None, look up the first active
        downloaded model from SQLite. This avoids hardcoding file paths.
        """
        try:
            # Dynamically resolve which model to use from the DB
            if model_name is None:
                from app.db.database import SessionLocal
                from app.db.models import ModelRegistry
                with SessionLocal() as db:
                    active = db.query(ModelRegistry).filter(
                        ModelRegistry.status == "downloaded",
                        ModelRegistry.is_active == True
                    ).first()
                    if not active:
                        # Fall back to any downloaded model
                        active = db.query(ModelRegistry).filter(
                            ModelRegistry.status == "downloaded"
                        ).first()
                    if not active:
                        logger.error("No downloaded model found. Please download a model from the LLM Panel.")
                        return None
                    model_name = active.name

            return self.llm_manager.get_model(model_name)
        except Exception as e:
            logger.error(f"Failed to load LLM '{model_name}': {e}")
            return None

