"""
Aegis — Prompt Registry

All LLM system prompts are defined here, organized by function.
Import from this package to use them in the core workflow.

Usage:
    from app.prompts import build_executor_prompt
"""

from app.prompts.executor import build_executor_prompt

__all__ = [
    "build_executor_prompt",
]
