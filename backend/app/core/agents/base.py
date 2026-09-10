import logging
from typing import Dict, List, Optional
from app.core.llm_manager import LLMManager

logger = logging.getLogger(__name__)

class BaseAgent:
    """Base class for all conversational and processing agents."""

    # ── Vision (see _attach_vision_images) — shared by every agent that
    # builds a create_chat_completion messages list from a user turn with
    # attachments (ChatAgent's own answers).
    _VISION_IMAGE_EXTS = {"png", "jpg", "jpeg"}
    _MAX_VISION_IMAGES = 4
    _MAX_VISION_IMAGE_BYTES = 15 * 1024 * 1024  # generous for a photo, not a raw scan dump

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

    def get_active_vision_mmproj_path(self) -> str | None:
        """
        Returns the active model's mmproj (vision tower) path if it's a
        vision model whose companion file has actually finished downloading,
        else None. Used to decide whether an attached image should be sent
        to the model as real vision input (see _attach_vision_images) rather
        than just OCR'd text like every other document. The actual lookup
        lives in app.db.crud (get_active_vision_mmproj_path) so the upload
        endpoint (app/api/documents.py), which has no agent instance to call
        this through, can share the exact same logic when deciding whether
        to skip OCR for a vision-handled image.
        """
        try:
            from app.db.database import SessionLocal
            from app.db.crud import get_active_vision_mmproj_path
            with SessionLocal() as db:
                return get_active_vision_mmproj_path(db)
        except Exception as e:
            logger.debug(f"Could not resolve active vision mmproj path: {e}")
            return None

    def _attach_vision_images(self, messages: List[Dict], attachments: Optional[List[Dict]]) -> None:
        """
        Mutates messages[-1] (the current user turn) in place, turning its
        plain-string content into an OpenAI-style content-parts list with any
        attached image(s) embedded as real vision input — but only when the
        active model actually has a vision chat_handler wired (see
        get_active_vision_mmproj_path). Every attachment still goes through
        the OCR/RAG path unconditionally regardless of this — this is purely
        additive, so a non-vision model's behavior is completely unchanged.

        Called by ChatAgent on whichever messages list feeds its own
        create_chat_completion call.
        """
        attached_ids = [a["document_id"] for a in (attachments or []) if a.get("document_id") is not None]
        if not attached_ids or not messages or messages[-1].get("role") != "user":
            return

        mmproj_path = self.get_active_vision_mmproj_path()
        if not mmproj_path:
            return

        from app.db.database import SessionLocal
        from app.db.models import UserDocument
        db = SessionLocal()
        try:
            docs = db.query(UserDocument).filter(UserDocument.id.in_(attached_ids)).all()
        finally:
            db.close()

        images = [d for d in docs if (d.file_type or "").lower() in self._VISION_IMAGE_EXTS]
        images = images[: self._MAX_VISION_IMAGES]
        if not images:
            return

        import os
        import base64

        content_parts: List[Dict] = [{"type": "text", "text": messages[-1]["content"]}]
        for doc in images:
            try:
                if os.path.getsize(doc.file_path) > self._MAX_VISION_IMAGE_BYTES:
                    logger.warning(f"Skipping oversized image for vision: {doc.file_path}")
                    continue
                with open(doc.file_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                ext = doc.file_type.lower()
                mime = "jpeg" if ext == "jpg" else ext
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{mime};base64,{b64}"},
                })
            except OSError as e:
                logger.warning(f"Could not read attached image '{doc.file_path}' for vision: {e}")

        if len(content_parts) > 1:  # at least one image actually made it in
            messages[-1]["content"] = content_parts

    def _log_token_usage(self, llm, messages, response_text: str, source: str, conversation_id: str = None):
        """
        Record real token counts for one LLM call, using the active model's own
        tokenizer (not an estimate) — feeds the Analytics page. Shared by
        ChatAgent and ExecutorAgent so every create_chat_completion call site
        logs consistently.
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

    def get_active_vision_mmproj_path(self) -> str | None:
        """
        Returns the active model's mmproj (vision tower) path if it's a
        vision model whose companion file has actually finished downloading,
        else None. Used to decide whether an attached image should be sent
        to the model as real vision input (see _attach_vision_images) rather
        than just OCR'd text like every other document. The actual lookup
        lives in app.db.crud (get_active_vision_mmproj_path) so the upload
        endpoint (app/api/documents.py), which has no agent instance to call
        this through, can share the exact same logic when deciding whether
        to skip OCR for a vision-handled image.
        """
        try:
            from app.db.database import SessionLocal
            from app.db.crud import get_active_vision_mmproj_path
            with SessionLocal() as db:
                return get_active_vision_mmproj_path(db)
        except Exception as e:
            logger.debug(f"Could not resolve active vision mmproj path: {e}")
            return None

    def _attach_vision_images(self, messages: List[Dict], attachments: Optional[List[Dict]]) -> None:
        """
        Mutates messages[-1] (the current user turn) in place, turning its
        plain-string content into an OpenAI-style content-parts list with any
        attached image(s) embedded as real vision input — but only when the
        active model actually has a vision chat_handler wired (see
        get_active_vision_mmproj_path). Every attachment still goes through
        the OCR/RAG path unconditionally regardless of this — this is purely
        additive, so a non-vision model's behavior is completely unchanged.

        Shared by ChatAgent (Chat Mode's answer) and PlannerAgent
        (Agent Mode's direct_response/plan-or-not decision) — both mode's
        "look at this image and answer" path ultimately calls this on
        whichever messages list feeds their own create_chat_completion call.
        """
        attached_ids = [a["document_id"] for a in (attachments or []) if a.get("document_id") is not None]
        if not attached_ids or not messages or messages[-1].get("role") != "user":
            return

        mmproj_path = self.get_active_vision_mmproj_path()
        if not mmproj_path:
            return

        from app.db.database import SessionLocal
        from app.db.models import UserDocument
        db = SessionLocal()
        try:
            docs = db.query(UserDocument).filter(UserDocument.id.in_(attached_ids)).all()
        finally:
            db.close()

        images = [d for d in docs if (d.file_type or "").lower() in self._VISION_IMAGE_EXTS]
        images = images[: self._MAX_VISION_IMAGES]
        if not images:
            return

        import os
        import base64

        content_parts: List[Dict] = [{"type": "text", "text": messages[-1]["content"]}]
        for doc in images:
            try:
                if os.path.getsize(doc.file_path) > self._MAX_VISION_IMAGE_BYTES:
                    logger.warning(f"Skipping oversized image for vision: {doc.file_path}")
                    continue
                with open(doc.file_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                ext = doc.file_type.lower()
                mime = "jpeg" if ext == "jpg" else ext
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{mime};base64,{b64}"},
                })
            except OSError as e:
                logger.warning(f"Could not read attached image '{doc.file_path}' for vision: {e}")

        if len(content_parts) > 1:  # at least one image actually made it in
            messages[-1]["content"] = content_parts

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

