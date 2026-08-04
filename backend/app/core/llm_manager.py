import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)

@dataclass
class ModelConfig:
    """Configuration for a specific LLM model."""
    name: str
    repo_id: Optional[str] = None
    filename: Optional[str] = None
    model_path: Optional[str] = None
    chat_format: Optional[str] = None
    # Add other llama_cpp parameters as needed (e.g., n_ctx, n_gpu_layers)
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
        """Register the models we know about from disk and SQLite."""
        _models_dir = Path(__file__).resolve().parent.parent.parent / "models"
        local_gemma_config = ModelConfig(
            name="gemma-local",
            model_path=str(_models_dir / "qwen2.5-3b-instruct-q4_k_m.gguf"),
            chat_format="chatml",
            kwargs={
                "n_ctx": 6144,
                "verbose": False
            }
        )
        self.register_model(local_gemma_config)

        # Sync additional models from SQLite if DB is initialized
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
                            kwargs={"n_ctx": m.context_length, "verbose": False}
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
            raise ValueError(f"Model {model_name} not found in available configurations.")
            
        if model_name not in self.loaded_models:
            self._load_model(model_name)
            
        return self.loaded_models[model_name]
        
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

        # KV cache estimate: 0.20 GB per 1024 context tokens
        n_ctx = (config.kwargs or {}).get("n_ctx", 4096)
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
        
        if config.repo_id and config.filename:
            # Load from huggingface hub
            llm = Llama.from_pretrained(
                repo_id=config.repo_id,
                filename=config.filename,
                chat_format=config.chat_format,
                **kwargs
            )
        elif config.model_path:
            # Load from local file
            llm = Llama(
                model_path=config.model_path,
                chat_format=config.chat_format,
                **kwargs
            )
        else:
            raise ValueError(f"Model config for {model_name} must have either repo_id/filename or model_path")
            
        self.loaded_models[model_name] = llm
        logger.info(f"Model {model_name} loaded successfully.")

