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

    def _log_token_usage(self, llm, messages, response_text: str, source: str, conversation_id: str = None):
        """
        Record real token counts for one LLM call, using the active model's own
        tokenizer (not an estimate) — feeds the Analytics page. Shared by
        ChatAgent, PlannerAgent, and ExecutorAgent so every create_chat_completion
        call site logs consistently.
        """
        try:
            prompt_text = "\n".join(
                m.get("content", "") for m in messages if isinstance(m.get("content"), str)
            )
            prompt_tokens = len(llm.tokenize(prompt_text.encode("utf-8", errors="ignore")))
            completion_tokens = len(llm.tokenize(response_text.encode("utf-8", errors="ignore"))) if response_text else 0

            from app.db.database import SessionLocal
            from app.db.crud import log_token_usage, get_active_model_display_name
            db = SessionLocal()
            try:
                model_name = get_active_model_display_name(db)
                log_token_usage(db, conversation_id, model_name, source, prompt_tokens, completion_tokens)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failed to log token usage: {e}")

