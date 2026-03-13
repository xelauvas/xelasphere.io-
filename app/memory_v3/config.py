"""
Memory V3 - Configuration & Presets
====================================
Success thresholds, customer presets, and tuning parameters.
"""

import os
from dataclasses import dataclass
from typing import Dict, Any
from enum import Enum


# =============================================================================
# SUCCESS THRESHOLDS (KPIs)
# =============================================================================

@dataclass
class SuccessThresholds:
    """
    KPI thresholds for determining rollout success.

    All values are relative changes vs control group.
    Negative = improvement (fewer turns/re-asks is good).
    """
    # Primary KPIs
    follow_up_rate_min_improvement: float = -0.10   # -10% minimum improvement
    follow_up_rate_target: float = -0.20           # -20% target

    re_ask_rate_min_improvement: float = -0.05     # -5% minimum
    re_ask_rate_target: float = -0.15              # -15% target

    # Secondary KPIs
    conversation_turns_max_increase: float = 0.05  # Allow up to +5%
    conversation_turns_target: float = -0.10       # -10% ideal

    # Safety thresholds
    anti_gaming_max_rate: float = 0.02             # Max 2% triggers
    correction_spike_threshold: float = 0.10       # Max 10% increase in corrections
    cost_spike_threshold: float = 0.15             # Max 15% cost increase

    def check_ready_to_ramp(self, treatment_metrics: Dict, control_metrics: Dict) -> Dict[str, Any]:
        """
        Check if metrics meet thresholds for ramping.

        Returns dict with pass/fail for each threshold.
        """
        results = {"ready": True, "checks": {}}

        # Follow-up rate
        t_follow = treatment_metrics.get("follow_up_rate", 0)
        c_follow = control_metrics.get("follow_up_rate", 0.2)
        follow_delta = (t_follow - c_follow) / max(c_follow, 0.01)

        results["checks"]["follow_up_rate"] = {
            "treatment": t_follow,
            "control": c_follow,
            "delta": round(follow_delta, 3),
            "threshold": self.follow_up_rate_min_improvement,
            "passed": follow_delta <= self.follow_up_rate_min_improvement,
        }
        if follow_delta > 0:  # Getting worse
            results["ready"] = False

        # Re-ask rate
        t_reask = treatment_metrics.get("re_ask_rate", 0)
        c_reask = control_metrics.get("re_ask_rate", 0.1)
        reask_delta = (t_reask - c_reask) / max(c_reask, 0.01)

        results["checks"]["re_ask_rate"] = {
            "treatment": t_reask,
            "control": c_reask,
            "delta": round(reask_delta, 3),
            "threshold": self.re_ask_rate_min_improvement,
            "passed": reask_delta <= 0,  # Any improvement is good
        }
        if reask_delta > 0.05:  # >5% worse is a problem
            results["ready"] = False

        # Anti-gaming
        anti_gaming = treatment_metrics.get("anti_gaming_rate", 0)
        results["checks"]["anti_gaming"] = {
            "value": anti_gaming,
            "threshold": self.anti_gaming_max_rate,
            "passed": anti_gaming < self.anti_gaming_max_rate,
        }
        if anti_gaming >= self.anti_gaming_max_rate:
            results["ready"] = False

        return results


# Default thresholds
DEFAULT_THRESHOLDS = SuccessThresholds()


# =============================================================================
# CUSTOMER TYPE PRESETS
# =============================================================================

class CustomerType(Enum):
    """Pre-configured customer types with optimal V3 settings."""
    SUPPORT_BOT = "support_bot"
    DEV_COPILOT = "dev_copilot"
    SALES_ASSISTANT = "sales_assistant"
    EDUCATION = "education"
    GENERAL = "general"


@dataclass
class CustomerPreset:
    """V3 configuration preset for a customer type."""
    name: str
    intensity: float
    description: str

    # Optional overrides
    frustrated_cooldown: int = 60
    max_frustrated_per_conversation: int = 5

    # Behavior emphasis
    emphasize_reassurance: bool = False
    emphasize_directness: bool = False
    emphasize_thoroughness: bool = False


# Customer presets
CUSTOMER_PRESETS: Dict[CustomerType, CustomerPreset] = {
    CustomerType.SUPPORT_BOT: CustomerPreset(
        name="Support Bot",
        intensity=0.7,
        description="Higher reassurance, more clarifying questions. Patients frustrated users.",
        emphasize_reassurance=True,
        frustrated_cooldown=90,  # More lenient with frustrated users
        max_frustrated_per_conversation=7,
    ),

    CustomerType.DEV_COPILOT: CustomerPreset(
        name="Developer Copilot",
        intensity=0.5,
        description="Direct, minimal fluff. Assumes technical competence.",
        emphasize_directness=True,
        frustrated_cooldown=45,  # Devs get frustrated faster, recover faster
    ),

    CustomerType.SALES_ASSISTANT: CustomerPreset(
        name="Sales Assistant",
        intensity=0.7,
        description="Tone-aware, polished responses. Never terse.",
        emphasize_reassurance=True,
        emphasize_thoroughness=True,
    ),

    CustomerType.EDUCATION: CustomerPreset(
        name="Education / Tutoring",
        intensity=0.8,
        description="Very thorough explanations, lots of examples, checking for understanding.",
        emphasize_thoroughness=True,
        emphasize_reassurance=True,
        max_frustrated_per_conversation=10,  # Students may struggle more
    ),

    CustomerType.GENERAL: CustomerPreset(
        name="General Purpose",
        intensity=0.6,
        description="Balanced defaults. Good starting point.",
    ),
}


def get_preset(customer_type: CustomerType) -> CustomerPreset:
    """Get preset configuration for a customer type."""
    return CUSTOMER_PRESETS.get(customer_type, CUSTOMER_PRESETS[CustomerType.GENERAL])


def get_preset_by_name(name: str) -> CustomerPreset:
    """Get preset by string name."""
    name_lower = name.lower().replace(" ", "_").replace("-", "_")

    for ct in CustomerType:
        if ct.value == name_lower:
            return CUSTOMER_PRESETS[ct]

    return CUSTOMER_PRESETS[CustomerType.GENERAL]


# =============================================================================
# ENVIRONMENT CONFIGURATION
# =============================================================================

def get_v3_config() -> Dict[str, Any]:
    """
    Get current V3 configuration from environment.

    Returns all config values with their current settings.
    """
    return {
        "enabled": os.getenv("MEMORY_V3_ENABLED", "true").lower() in ("true", "1", "yes"),
        "rollout_phase": os.getenv("V3_ROLLOUT_PHASE", "shadow"),
        "rollout_percent": float(os.getenv("V3_ROLLOUT_PERCENT", "0")),
        "intensity": float(os.getenv("V3_INTENSITY", "0.6")),
        "customer_type": os.getenv("V3_CUSTOMER_TYPE", "general"),
    }


def apply_preset(preset: CustomerPreset) -> Dict[str, str]:
    """
    Generate environment variable settings for a preset.

    Returns dict of env var name -> value.
    """
    return {
        "V3_INTENSITY": str(preset.intensity),
        "V3_FRUSTRATED_COOLDOWN": str(preset.frustrated_cooldown),
        "V3_MAX_FRUSTRATED": str(preset.max_frustrated_per_conversation),
    }


# =============================================================================
# QUICK REFERENCE
# =============================================================================

INTENSITY_GUIDE = """
V3 Intensity Quick Reference
============================

Level   | What's Injected                    | Use Case
--------|------------------------------------|-----------------------
0.0     | Nothing (disabled)                 | Kill switch
0.1-0.3 | Context only (task type)           | Minimal steering
0.4-0.5 | + State (pace, mode, certainty)    | Light guidance
0.6     | + Behavior adjustments (DEFAULT)   | Standard V3
0.7     | + Reassurance, tradeoffs           | Support-focused
0.8-1.0 | All hints + safety guards          | Maximum adaptation

Prompt Block at Each Level:
---------------------------
0.3 (Low):
  context_mode: DEBUG
  intensity: 0.3
  adjustments_enabled: false

0.5 (Medium):
  context_mode: DEBUG
  state:
    pace: rushed
    mode: executing
    certainty: confident
  intensity: 0.5
  adjustments_enabled: false

0.6+ (High):
  context_mode: DEBUG
  state:
    pace: rushed
    mode: executing
    certainty: confident
  intensity: 0.6
  adjustments_enabled: true
  behavior_adjustments:
    - concise
    - direct

Tuning Tips:
- Too chatty? Drop by 0.1
- Too robotic? Raise by 0.1
- Keep changes small (±0.1)
- Wait 48-72h between changes

Safety Note:
At intensity >= 0.8, "concise" mode automatically includes
"include_one_line_context" to prevent over-terse responses.
"""

CUSTOMER_GUIDE = """
Customer Type Quick Reference
=============================

support_bot (0.7)
  - High reassurance
  - More patience with frustration
  - Good for: Customer service, help desks

dev_copilot (0.5)
  - Direct, minimal explanation
  - Assumes competence
  - Good for: IDE integrations, CLI tools

sales_assistant (0.7)
  - Polished, tone-aware
  - Never terse
  - Good for: CRM integrations, outreach

education (0.8)
  - Very thorough
  - Lots of examples
  - Good for: Tutoring, learning platforms

general (0.6)
  - Balanced defaults
  - Good starting point
"""
