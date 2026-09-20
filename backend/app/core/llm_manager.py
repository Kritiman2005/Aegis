import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)


DEFAULT_N_CTX = 8192


def resolve_effective_n_ctx(context_length: Optional[int], model_name: Optional[str] = None) -> int:
    """
    The REAL context window a model will actually be loaded with — not its
    native/trained max. Each model has its OWN cap override
    (ModelRegistry.context_cap, set per-model from Memory Hub's per-model
    "Context Window Cap" card — different models legitimately want
    different caps, so this is never a single app-wide value) which wins
    over the safe 8192-token default when set, but is always clamped to
    that model's own native context_length — asking for more than a model
    was actually trained on has no ceiling worth honoring. Looked up fresh
    from SQLite (not any in-memory cache) so a cap change takes effect
    without needing the process to notice a stale snapshot. Shared by
    LLMManager (the actual load call, via _resolve_n_ctx below) and by API
    endpoints that need to report this same real ceiling for a model that
    ISN'T currently loaded (so they don't have to load it just to ask) —
    see models_hub.list_downloaded_models's effective_context_length and
    context_config.get_hardware_status's is_active fallback.
    """
    ceiling = context_length or DEFAULT_N_CTX
    override = None
    if model_name:
        try:
            from app.db.database import SessionLocal
            from app.db.models import ModelRegistry
            with SessionLocal() as db:
                m = db.query(ModelRegistry).filter(ModelRegistry.name == model_name).first()
                if m:
                    override = m.context_cap
        except Exception as e:
            logger.debug(f"Could not look up context_cap for '{model_name}': {e}")
    if override:
        return min(override, ceiling)
    return min(ceiling, DEFAULT_N_CTX)


@dataclass
class ModelConfig:
    """Configuration for a specific LLM model."""
    name: str
    repo_id: Optional[str] = None
    filename: Optional[str] = None
    model_path: Optional[str] = None
    chat_format: Optional[str] = None
    # The model's real trained max context (read from its own GGUF metadata at
    # download time) — informational ceiling used by _load_model to pick a safe
    # n_ctx; NOT baked directly into kwargs, so large values still get capped.
    context_length: Optional[int] = None
    # Path to this model's paired mmproj (CLIP-style vision tower) file, if
    # it's a vision model whose companion download has finished — see
    # _load_model, which uses this to build an MTMDChatHandler.
    mmproj_path: Optional[str] = None
    # Add other llama_cpp parameters as needed (e.g., n_gpu_layers)
    kwargs: Optional[Dict[str, Any]] = None

class LLMManager:
    """Manages loading and interaction with different LLMs."""
    
    def __init__(self):
        # Store configurations for available models
        self.available_models: Dict[str, ModelConfig] = {}
        # Cache for loaded models so we don't reload them into memory
        self.loaded_models: Dict[str, Any] = {}
        
        # Register the default models
        self._register_default_models()
        
    def _register_default_models(self):
        """Register downloaded models from SQLite. No hardcoded defaults."""
        # Sync downloaded models from SQLite
        try:
            from app.db.database import SessionLocal
            from app.db.models import ModelRegistry
            with SessionLocal() as db:
                models = db.query(ModelRegistry).filter(ModelRegistry.status == "downloaded").all()
                for m in models:
                    if m.name not in self.available_models:
                        cfg = ModelConfig(
                            name=m.name,
                            repo_id=m.repo_id,
                            filename=m.filename,
                            model_path=m.file_path,
                            chat_format=m.chat_format,
                            context_length=m.context_length,
                            mmproj_path=m.mmproj_path if m.mmproj_status == "downloaded" else None,
                            kwargs={"verbose": False}
                        )
                        self.register_model(cfg)
        except Exception as e:
            logger.debug(f"SQLite model sync skipped: {e}")

        
    def register_model(self, config: ModelConfig):
        """Add a new model configuration."""
        self.available_models[config.name] = config
        
    def get_model(self, model_name: str) -> Any:
        """Get a loaded model, loading it if necessary."""
        if model_name not in self.available_models:
            # This manager is a long-lived singleton created at process start, so a
            # model downloaded later in the same session won't be in the in-memory
            # snapshot yet. Re-check SQLite before giving up — self-heals without
            # requiring a backend restart after every download.
            self._sync_model_from_db(model_name)

        if model_name not in self.available_models:
            raise ValueError(f"Model {model_name} not found in available configurations.")

        if model_name not in self.loaded_models:
            self._load_model(model_name)

        return self.loaded_models[model_name]

    def _sync_model_from_db(self, model_name: str) -> None:
        """Pull a single model's config from SQLite in case it was downloaded after this process started."""
        try:
            from app.db.database import SessionLocal
            from app.db.models import ModelRegistry
            with SessionLocal() as db:
                m = db.query(ModelRegistry).filter(
                    ModelRegistry.name == model_name,
                    ModelRegistry.status == "downloaded"
                ).first()
                if m:
                    self.register_model(ModelConfig(
                        name=m.name,
                        repo_id=m.repo_id,
                        filename=m.filename,
                        model_path=m.file_path,
                        chat_format=m.chat_format,
                        context_length=m.context_length,
                        mmproj_path=m.mmproj_path if m.mmproj_status == "downloaded" else None,
                        kwargs={"verbose": False}
                    ))
        except Exception as e:
            logger.debug(f"Could not sync model '{model_name}' from DB: {e}")
        
    def _resolve_n_ctx(self, config: "ModelConfig") -> int:
        """
        Decide the actual n_ctx to load a model with — see module-level
        resolve_effective_n_ctx for the real rule. Shared by the RAM
        estimator and the actual load call so both agree on the same number.
        """
        return resolve_effective_n_ctx(config.context_length, config.name)

    def _estimate_ram_required_gb(self, config: "ModelConfig") -> float:
        """
        Estimate how much RAM (in GB) loading this model will require at runtime.

        Breakdown:
          - Weight footprint: actual GGUF file size on disk (Q4_K_M ≈ weight bytes).
          - KV cache: 2 * n_ctx * n_layers * head_dim * 2 bytes (fp16).
            We use a conservative proxy: 0.20 GB per 1024 tokens of context.
          - OS + Electron + FastAPI overhead: 2.5 GB fixed margin.
            (macOS + Chromium shell routinely hold 3–5 GB; we use 2.5 as the
             floor so we don't reject machines that are actually fine.)
        """
        import os
        GB = 1024 ** 3

        # Weight footprint from disk
        weight_gb = 0.0
        if config.model_path:
            try:
                weight_gb = os.path.getsize(config.model_path) / GB
            except OSError:
                weight_gb = 2.0  # conservative fallback if file not found yet

        # A vision model's mmproj (CLIP vision tower) is loaded into RAM
        # alongside the main weights, not instead of them — add its real
        # on-disk size too, or the estimate silently undercounts a vision
        # model's actual footprint.
        if config.mmproj_path:
            try:
                weight_gb += os.path.getsize(config.mmproj_path) / GB
            except OSError:
                pass

        # KV cache estimate: 0.20 GB per 1024 context tokens.
        n_ctx = self._resolve_n_ctx(config)
        kv_cache_gb = (n_ctx / 1024) * 0.20

        # Fixed overhead for OS + Electron + backend processes
        overhead_gb = 2.5

        return weight_gb + kv_cache_gb + overhead_gb

    def _check_available_ram(self, model_name: str, config: "ModelConfig") -> None:
        """
        Warn (do not hard-fail) if available system RAM is likely insufficient
        to load the model without swapping.

        Checks *available* memory (not total installed RAM) so it accounts for
        what other apps the user already has open at load time.
        """
        try:
            import psutil
            available_gb = psutil.virtual_memory().available / (1024 ** 3)
            required_gb  = self._estimate_ram_required_gb(config)

            logger.info(
                f"[RAM Check] Model '{model_name}': estimated need {required_gb:.1f} GB, "
                f"available now {available_gb:.1f} GB"
            )

            if available_gb < required_gb:
                logger.warning(
                    f"[RAM Check] WARNING: available RAM ({available_gb:.1f} GB) is below the "
                    f"estimated requirement for '{model_name}' ({required_gb:.1f} GB). "
                    f"Loading will proceed but performance may degrade severely due to swapping. "
                    f"Close other applications or choose a smaller/more-quantized model."
                )
            elif available_gb < required_gb + 1.0:
                # Tight but might work — warn anyway
                logger.warning(
                    f"[RAM Check] TIGHT: available RAM ({available_gb:.1f} GB) is close to the "
                    f"estimated requirement for '{model_name}' ({required_gb:.1f} GB). "
                    f"Consider closing other applications before loading."
                )
        except ImportError:
            logger.debug("[RAM Check] psutil not installed — skipping RAM check.")
        except Exception as e:
            logger.debug(f"[RAM Check] Could not read system memory: {e}")

    def unload_model(self, model_name: str) -> bool:
        """
        Unload a model from memory and force garbage collection to free RAM immediately.
        Returns True if unloaded, False if it wasn't loaded.
        """
        if model_name in self.loaded_models:
            del self.loaded_models[model_name]
            import gc
            gc.collect()
            logger.info(f"Model {model_name} unloaded successfully.")
            return True
        return False

    def _load_model(self, model_name: str):
        """Actually load the model into memory."""
        try:
            from llama_cpp import Llama
        except ImportError:
            raise ImportError("llama-cpp-python is not installed.")

        config = self.available_models[model_name]

        # Check available RAM before committing to load.
        # This is a warn-only guard — we do not hard-fail, because some machines
        # report conservative available figures while macOS compression offsets real
        # pressure. The warning gives the user actionable information.
        self._check_available_ram(model_name, config)

        logger.info(f"Loading model: {model_name}")
            
        kwargs = config.kwargs or {}

        # Inject dynamic hardware config
        from app.core import context_config
        hw_cfg = context_config.get("hardware")
        if "n_gpu_layers" in hw_cfg:
            kwargs["n_gpu_layers"] = hw_cfg["n_gpu_layers"]
        if "n_threads" in hw_cfg:
            kwargs["n_threads"] = hw_cfg["n_threads"]

        # n_ctx: the model's real trained context (from its GGUF metadata),
        # capped to a hardware-safe default unless the user explicitly overrode
        # it — see _resolve_n_ctx. Not baked in at registration time so this
        # always reflects the current hardware config.
        kwargs["n_ctx"] = self._resolve_n_ctx(config)


        # A vision model's chat_handler (built from its mmproj/vision-tower
        # file) takes over chat templating entirely — passing chat_format as
        # well would be ambiguous about which one wins, so it's dropped in
        # that case rather than passed alongside chat_handler.
        chat_handler = None
        if config.mmproj_path and os.path.exists(config.mmproj_path):
            from llama_cpp.llama_chat_format import MTMDChatHandler
            chat_handler = MTMDChatHandler(clip_model_path=config.mmproj_path, verbose=False)
            logger.info(f"Loading '{model_name}' with vision support (mmproj: {config.mmproj_path})")

        if config.model_path and os.path.exists(config.model_path):
            # Load from local file (this uses the file we downloaded directly via httpx, avoiding hf-hub SSL issues)
            llm = Llama(
                model_path=config.model_path,
                chat_format=None if chat_handler else config.chat_format,
                chat_handler=chat_handler,
                **kwargs
            )
        elif config.repo_id and config.filename:
            # Fallback to huggingface hub
            llm = Llama.from_pretrained(
                repo_id=config.repo_id,
                filename=config.filename,
                chat_format=config.chat_format,
                **kwargs
            )
        else:
            raise ValueError(f"Model config for {model_name} must have either model_path or repo_id/filename")
            
        self.loaded_models[model_name] = llm
        logger.info(f"Model {model_name} loaded successfully.")

