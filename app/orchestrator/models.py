"""
Model dataclass — single source of truth for everything we know about a model.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class Model:
    # Identity
    id: str
    provider: str
    display_name: str

    # Capabilities
    strengths: List[str]
    context_window: int
    supports_vision: bool = False
    supports_streaming: bool = True

    # Cost
    cost_tier: str = "free"       # "free" | "cheap" | "paid"
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0
    free_tier_daily_tokens: int = 0

    # Health — updated by Watcher
    status: str = "healthy"       # "healthy" | "degraded" | "down" | "pending_review"
    last_healthy: Optional[datetime] = None
    latency_p50_ms: int = 0
    consecutive_failures: int = 0

    # Deprecation — updated by Watcher
    is_deprecated: bool = False
    replacement_id: Optional[str] = None

    # Quota — updated by Accountant
    tokens_used_today: int = 0
    requests_used_today: int = 0
    quota_exhausted: bool = False

    # Quality — updated by QualityCollector
    quality_scores: Dict[str, float] = field(default_factory=dict)

    def is_available(self) -> bool:
        return (
            not self.is_deprecated
            and not self.quota_exhausted
            and self.status not in ("down", "pending_review")
        )

    def quality_for(self, job: str) -> float:
        return self.quality_scores.get(job, 0.5)
