"""
analytics.py — Token usage analytics for the Analytics page.

All numbers come from app.db.models.TokenUsage, populated by real per-call
token counts (via the active model's own tokenizer) logged in chat.py,
planner.py, and executor.py — nothing here is estimated or simulated, except
the cost-savings figure, which is explicitly a rough illustrative estimate
against typical cloud API pricing (Aegis makes no cloud LLM calls itself).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db import crud

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

# Rough blended $/1K-token rate for a typical small-model cloud API, used only
# to illustrate what running these same tokens through a cloud provider would
# have cost. Not tied to any specific vendor's real pricing.
CLOUD_COST_PER_1K_TOKENS_USD = 0.002


@router.get("")
def get_analytics(db: Session = Depends(get_db)):
    summary = crud.get_analytics_summary(db)
    estimated_saved_usd = round((summary["total_tokens"] / 1000) * CLOUD_COST_PER_1K_TOKENS_USD, 2)

    return {
        "tokens_generated": summary["tokens_generated"],
        "prompt_tokens_processed": summary["prompt_tokens_processed"],
        "estimated_saved_usd": estimated_saved_usd,
        "percent_local": 100,
        "daily": crud.get_token_usage_daily(db, days=7),
        "by_model": crud.get_token_usage_by_model(db),
        "by_source": crud.get_token_usage_by_source(db),
    }
