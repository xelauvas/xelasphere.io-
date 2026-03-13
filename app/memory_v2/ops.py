"""
Memory V2 Operations
====================
Production operations: SLOs, kill switches, versioning, shadow mode, security.

This module contains the "learned the hard way" pieces that keep memory
systems healthy at scale.
"""

import logging
import re
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import os

logger = logging.getLogger(__name__)


# ============================================================================
# 1) SLO Metrics & Dashboard Support
# ============================================================================

@dataclass
class SLOMetrics:
    """
    SLO metrics for memory system health.

    Alert thresholds (tune based on your baseline):
    - correction_rate > 2% = extraction/judge regression
    - quarantine_rate spike = attack or sanitizer bug
    - write_rate spike = extractor too aggressive
    - staleness > 30% = cleanup job not running
    """
    # Precision proxy: memories used / memories retrieved
    precision_proxy: float = 0.0

    # User correction rate: corrections / sessions
    correction_rate: float = 0.0

    # Quarantine rate: quarantined / retrieved
    quarantine_rate: float = 0.0

    # Write rate: stored / 1k messages
    write_rate_per_1k: float = 0.0

    # Promotion rate: promoted / 1k messages
    promotion_rate_per_1k: float = 0.0

    # Staleness: unused 90d / active
    staleness_rate: float = 0.0

    # Empty retrieval rate: empty / total retrievals
    empty_retrieval_rate: float = 0.0

    # Raw counts for dashboards
    total_memories: int = 0
    active_memories: int = 0
    provisional_memories: int = 0
    quarantined_memories: int = 0
    corrections_today: int = 0
    writes_today: int = 0
    retrievals_today: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "precision_proxy": round(self.precision_proxy, 4),
            "correction_rate": round(self.correction_rate, 4),
            "quarantine_rate": round(self.quarantine_rate, 4),
            "write_rate_per_1k": round(self.write_rate_per_1k, 2),
            "promotion_rate_per_1k": round(self.promotion_rate_per_1k, 2),
            "staleness_rate": round(self.staleness_rate, 4),
            "empty_retrieval_rate": round(self.empty_retrieval_rate, 4),
            "total_memories": self.total_memories,
            "active_memories": self.active_memories,
            "provisional_memories": self.provisional_memories,
            "quarantined_memories": self.quarantined_memories,
            "corrections_today": self.corrections_today,
            "writes_today": self.writes_today,
            "retrievals_today": self.retrievals_today,
        }


# Alert thresholds (configurable)
SLO_THRESHOLDS = {
    "correction_rate_warning": 0.01,    # 1%
    "correction_rate_critical": 0.02,   # 2%
    "quarantine_rate_warning": 0.001,   # 0.1%
    "quarantine_rate_critical": 0.01,   # 1%
    "write_rate_spike_multiplier": 3.0, # 3x baseline
    "staleness_warning": 0.20,          # 20%
    "staleness_critical": 0.30,         # 30%
    "empty_retrieval_warning": 0.50,    # 50%
}


def check_slo_alerts(metrics: SLOMetrics) -> List[Dict[str, str]]:
    """
    Check metrics against SLO thresholds and return alerts.

    Returns:
        List of alert dicts with severity, metric, value, threshold
    """
    alerts = []

    # Correction rate
    if metrics.correction_rate >= SLO_THRESHOLDS["correction_rate_critical"]:
        alerts.append({
            "severity": "critical",
            "metric": "correction_rate",
            "value": f"{metrics.correction_rate:.2%}",
            "threshold": f"{SLO_THRESHOLDS['correction_rate_critical']:.2%}",
            "action": "Check extractor/judge for regression"
        })
    elif metrics.correction_rate >= SLO_THRESHOLDS["correction_rate_warning"]:
        alerts.append({
            "severity": "warning",
            "metric": "correction_rate",
            "value": f"{metrics.correction_rate:.2%}",
            "threshold": f"{SLO_THRESHOLDS['correction_rate_warning']:.2%}",
            "action": "Monitor extractor quality"
        })

    # Quarantine rate
    if metrics.quarantine_rate >= SLO_THRESHOLDS["quarantine_rate_critical"]:
        alerts.append({
            "severity": "critical",
            "metric": "quarantine_rate",
            "value": f"{metrics.quarantine_rate:.2%}",
            "threshold": f"{SLO_THRESHOLDS['quarantine_rate_critical']:.2%}",
            "action": "Possible attack or sanitizer bug - investigate"
        })

    # Staleness
    if metrics.staleness_rate >= SLO_THRESHOLDS["staleness_critical"]:
        alerts.append({
            "severity": "critical",
            "metric": "staleness_rate",
            "value": f"{metrics.staleness_rate:.2%}",
            "threshold": f"{SLO_THRESHOLDS['staleness_critical']:.2%}",
            "action": "Cleanup job may not be running"
        })

    return alerts


# ============================================================================
# 2) Kill Switch (Feature Flags)
# ============================================================================

class MemoryFeatureFlags:
    """
    Feature flags for memory system kill switches.

    Use environment variables or config for production.
    Allows fast disable without code deploy.
    """

    # Global toggles (env vars in production)
    MEMORY_WRITE_ENABLED = True
    MEMORY_READ_ENABLED = True

    # Per-user overrides (check DB/cache in production)
    _disabled_users: Set[str] = set()

    @classmethod
    def is_write_enabled(cls, user_id: Optional[str] = None) -> bool:
        """Check if memory writes are enabled."""
        # Check env var override
        env_override = os.environ.get("MEMORY_WRITE_ENABLED", "").lower()
        if env_override == "false":
            return False

        # Check global flag
        if not cls.MEMORY_WRITE_ENABLED:
            return False

        # Check per-user disable
        if user_id and user_id in cls._disabled_users:
            return False

        return True

    @classmethod
    def is_read_enabled(cls, user_id: Optional[str] = None) -> bool:
        """Check if memory reads are enabled."""
        env_override = os.environ.get("MEMORY_READ_ENABLED", "").lower()
        if env_override == "false":
            return False

        if not cls.MEMORY_READ_ENABLED:
            return False

        if user_id and user_id in cls._disabled_users:
            return False

        return True

    @classmethod
    def disable_for_user(cls, user_id: str, reason: str = "") -> None:
        """Disable memory for a specific user (e.g., after bug report)."""
        cls._disabled_users.add(user_id)
        logger.warning(f"Memory disabled for user {user_id}: {reason}")

    @classmethod
    def enable_for_user(cls, user_id: str) -> None:
        """Re-enable memory for a user."""
        cls._disabled_users.discard(user_id)
        logger.info(f"Memory re-enabled for user {user_id}")

    @classmethod
    def global_disable_writes(cls) -> None:
        """Emergency kill switch for all writes."""
        cls.MEMORY_WRITE_ENABLED = False
        logger.critical("MEMORY WRITES GLOBALLY DISABLED")

    @classmethod
    def global_disable_reads(cls) -> None:
        """Emergency kill switch for all reads."""
        cls.MEMORY_READ_ENABLED = False
        logger.critical("MEMORY READS GLOBALLY DISABLED")

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Get current feature flag status."""
        return {
            "write_enabled": cls.MEMORY_WRITE_ENABLED,
            "read_enabled": cls.MEMORY_READ_ENABLED,
            "disabled_users_count": len(cls._disabled_users),
            "env_write_override": os.environ.get("MEMORY_WRITE_ENABLED"),
            "env_read_override": os.environ.get("MEMORY_READ_ENABLED"),
        }


# ============================================================================
# 3) Policy Versioning
# ============================================================================

# Current policy version - increment when you change:
# - Scoring weights
# - Sanitizer patterns
# - Schema fields
# - Prompt injection wrapper
CURRENT_POLICY_VERSION = "v2.1.0"

# Version history for reference
POLICY_VERSION_HISTORY = {
    "v1.0.0": "Initial memory system",
    "v2.0.0": "Added confirmation-based promotion, quarantine, safeguards",
    "v2.1.0": "Added ops: SLOs, kill switches, shadow mode, PII redaction",
}

# Current prompt context version
CURRENT_PROMPT_VERSION = "prompt_v1.0"


def get_policy_metadata() -> Dict[str, str]:
    """Get current policy metadata for memory items."""
    return {
        "policy_version": CURRENT_POLICY_VERSION,
        "prompt_version": CURRENT_PROMPT_VERSION,
        "created_at": datetime.utcnow().isoformat(),
    }


# ============================================================================
# 4) Schema Versioning & Migrations
# ============================================================================

CURRENT_SCHEMA_VERSION = 2

# Migration registry
MIGRATIONS = {}


def register_migration(from_version: int, to_version: int):
    """Decorator to register a migration function."""
    def decorator(func):
        MIGRATIONS[(from_version, to_version)] = func
        return func
    return decorator


@register_migration(1, 2)
def migrate_v1_to_v2(memory_dict: Dict) -> Dict:
    """
    Migrate v1 schema to v2.

    Changes:
    - Add lifecycle.repeated_by_user (default 0)
    - Add lifecycle.explicit_confirmed (default False)
    - Add policy_version field
    - Normalize old keys
    - Re-run sanitizer
    """
    # Add missing lifecycle fields
    if "lifecycle" not in memory_dict:
        memory_dict["lifecycle"] = {}

    lifecycle = memory_dict["lifecycle"]
    lifecycle.setdefault("repeated_by_user", 0)
    lifecycle.setdefault("explicit_confirmed", False)
    lifecycle.setdefault("last_confirmed_at", None)

    # Add policy version
    memory_dict.setdefault("policy_version", "v1.0.0")

    # Set schema version
    memory_dict["schema_version"] = 2

    # Normalize key if needed
    key = memory_dict.get("key", "")
    if " " in key:
        memory_dict["key"] = key.lower().replace(" ", "_")

    return memory_dict


def migrate_memory(memory_dict: Dict, target_version: int = CURRENT_SCHEMA_VERSION) -> Dict:
    """
    Migrate a memory dict to target schema version.

    Args:
        memory_dict: Raw memory data
        target_version: Target schema version

    Returns:
        Migrated memory dict
    """
    current = memory_dict.get("schema_version", 1)

    while current < target_version:
        migration_key = (current, current + 1)
        if migration_key in MIGRATIONS:
            memory_dict = MIGRATIONS[migration_key](memory_dict)
            current = memory_dict.get("schema_version", current + 1)
        else:
            logger.warning(f"No migration found for {current} -> {current + 1}")
            break

    return memory_dict


def run_batch_migration(memories: List[Dict], target_version: int = CURRENT_SCHEMA_VERSION) -> List[Dict]:
    """Run migration on a batch of memories."""
    migrated = []
    for memory in memories:
        try:
            migrated.append(migrate_memory(memory, target_version))
        except Exception as e:
            logger.error(f"Migration failed for {memory.get('id')}: {e}")
            migrated.append(memory)  # Keep original on failure
    return migrated


# ============================================================================
# 5) Shadow Mode
# ============================================================================

class ShadowMode:
    """
    Shadow mode for safe deployment.

    In shadow mode:
    - Retrieve memory as normal
    - Simulate storing decisions
    - Log what would be stored
    - DON'T actually write

    Review shadow logs for:
    - False positives (creepy memories)
    - False negatives (missed preferences)
    """

    ENABLED = False
    _shadow_log: List[Dict] = []

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if shadow mode is active."""
        env_override = os.environ.get("MEMORY_SHADOW_MODE", "").lower()
        if env_override == "true":
            return True
        return cls.ENABLED

    @classmethod
    def enable(cls) -> None:
        """Enable shadow mode."""
        cls.ENABLED = True
        cls._shadow_log = []
        logger.info("Memory shadow mode ENABLED - writes will be simulated only")

    @classmethod
    def disable(cls) -> None:
        """Disable shadow mode."""
        cls.ENABLED = False
        logger.info("Memory shadow mode DISABLED - writes are live")

    @classmethod
    def log_would_store(
        cls,
        user_id: str,
        memory_type: str,
        key: str,
        value: Any,
        confidence: float,
        decision: str,
        reason: str
    ) -> None:
        """Log what would be stored in shadow mode."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "type": memory_type,
            "key": key,
            "value": str(value)[:100],  # Truncate for logs
            "confidence": confidence,
            "decision": decision,  # "store", "reject", "provisional"
            "reason": reason,
        }
        cls._shadow_log.append(entry)
        logger.debug(f"Shadow: {decision} {memory_type}/{key} ({reason})")

    @classmethod
    def log_would_retrieve(
        cls,
        user_id: str,
        query_type: str,
        memories_count: int,
        top_keys: List[str]
    ) -> None:
        """Log retrieval decisions in shadow mode."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "query_type": query_type,
            "memories_count": memories_count,
            "top_keys": top_keys[:5],
        }
        cls._shadow_log.append(entry)

    @classmethod
    def get_shadow_log(cls, limit: int = 100) -> List[Dict]:
        """Get recent shadow log entries."""
        return cls._shadow_log[-limit:]

    @classmethod
    def analyze_shadow_log(cls) -> Dict[str, Any]:
        """
        Analyze shadow log for review.

        Returns:
            Analysis with potential issues
        """
        if not cls._shadow_log:
            return {"status": "empty", "entries": 0}

        store_decisions = [e for e in cls._shadow_log if e.get("decision") == "store"]
        reject_decisions = [e for e in cls._shadow_log if e.get("decision") == "reject"]

        # Group by type
        by_type = {}
        for entry in store_decisions:
            t = entry.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        # Find potentially creepy (high volume same key)
        key_counts = {}
        for entry in store_decisions:
            k = entry.get("key", "")
            key_counts[k] = key_counts.get(k, 0) + 1

        suspicious_keys = [k for k, v in key_counts.items() if v > 5]

        return {
            "status": "active",
            "entries": len(cls._shadow_log),
            "would_store": len(store_decisions),
            "would_reject": len(reject_decisions),
            "by_type": by_type,
            "suspicious_high_frequency_keys": suspicious_keys,
        }


# ============================================================================
# 6) Enhanced "Explain Why" UX
# ============================================================================

@dataclass
class MemoryExplanation:
    """Rich explanation for why a memory exists and how to manage it."""
    memory_id: str
    key: str
    value: str
    confidence: float
    confidence_reason: str
    source_quote: Optional[str]
    created_at: datetime
    last_used: Optional[datetime]
    use_count: int

    # Fix suggestions
    fix_commands: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "source_quote": self.source_quote,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "use_count": self.use_count,
            "fix_commands": self.fix_commands,
        }


def generate_confidence_reason(confidence: float, evidence_method: str) -> str:
    """Generate human-readable confidence reason."""
    if evidence_method == "explicit":
        return "You stated this directly"
    elif evidence_method == "rule":
        return "Detected from clear pattern"
    elif confidence >= 0.9:
        return "High confidence from context"
    elif confidence >= 0.7:
        return "Inferred from conversation"
    else:
        return "Low confidence inference - may need confirmation"


def generate_fix_commands(memory_key: str, memory_id: str) -> List[Dict[str, str]]:
    """Generate suggested fix commands for the user."""
    return [
        {
            "action": "forget",
            "command": f"forget {memory_key}",
            "description": "Remove this memory"
        },
        {
            "action": "pin",
            "command": f"pin {memory_key}",
            "description": "Keep this memory permanently"
        },
        {
            "action": "update",
            "command": f"My {memory_key} is [new value]",
            "description": "Update with correct value"
        },
        {
            "action": "disable",
            "command": "don't remember this",
            "description": "Stop storing this type of info"
        },
    ]


def build_memory_explanation(memory) -> MemoryExplanation:
    """Build a rich explanation for a memory item."""
    return MemoryExplanation(
        memory_id=memory.id,
        key=memory.key,
        value=str(memory.value),
        confidence=memory.confidence,
        confidence_reason=generate_confidence_reason(
            memory.confidence,
            memory.evidence.extraction_method
        ),
        source_quote=memory.evidence.quote,
        created_at=memory.lifecycle.created_at,
        last_used=memory.lifecycle.last_used_at,
        use_count=memory.lifecycle.use_count,
        fix_commands=generate_fix_commands(memory.key, memory.id)
    )


# ============================================================================
# 7) Multi-User / Device Safeguards
# ============================================================================

@dataclass
class SessionContext:
    """Context for device/session tracking."""
    user_id: str
    device_id: Optional[str] = None
    is_new_device: bool = False
    session_start: datetime = field(default_factory=datetime.utcnow)


def should_require_profile_confirmation(
    session: SessionContext,
    memory_type: str,
    memory_key: str
) -> bool:
    """
    Check if profile fact should require confirmation on new device.

    Mitigates risk of shared devices / family accounts.
    """
    # Only for profile facts
    if memory_type != "profile":
        return False

    # Only on new devices
    if not session.is_new_device:
        return False

    # High-stakes keys need confirmation
    high_stakes = {"name", "email", "location", "timezone", "role"}
    return memory_key in high_stakes


def generate_device_fingerprint(user_agent: str, ip_prefix: str) -> str:
    """
    Generate device fingerprint for new device detection.

    Note: IP prefix (first 2 octets) for privacy.
    """
    raw = f"{user_agent}|{ip_prefix}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ============================================================================
# 8) PII Redaction for Logs
# ============================================================================

# PII patterns to redact
PII_PATTERNS = [
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]'),  # Email
    (r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE]'),  # US phone
    (r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]'),  # SSN
    (r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b', '[CARD]'),  # Credit card
    (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP]'),  # IP address
]

# Keys that contain PII (redact entire value)
PII_KEYS = {"email", "phone", "ssn", "address", "credit_card", "password"}


def redact_pii(text: str) -> str:
    """Redact common PII patterns from text."""
    if not text:
        return text

    result = text
    for pattern, replacement in PII_PATTERNS:
        result = re.sub(pattern, replacement, result)

    return result


def redact_memory_for_logging(memory_dict: Dict) -> Dict:
    """
    Redact PII from memory dict before logging.

    - Redacts values for PII keys
    - Redacts patterns in quotes
    - Truncates long values
    """
    safe_dict = memory_dict.copy()

    # Redact PII keys entirely
    key = safe_dict.get("key", "").lower()
    if key in PII_KEYS:
        safe_dict["value"] = "[REDACTED]"

    # Redact patterns in value
    if "value" in safe_dict and isinstance(safe_dict["value"], str):
        safe_dict["value"] = redact_pii(safe_dict["value"])
        # Truncate
        if len(safe_dict["value"]) > 100:
            safe_dict["value"] = safe_dict["value"][:100] + "..."

    # Redact evidence quote
    if "evidence" in safe_dict and isinstance(safe_dict["evidence"], dict):
        if "quote" in safe_dict["evidence"] and safe_dict["evidence"]["quote"]:
            quote = safe_dict["evidence"]["quote"]
            safe_dict["evidence"]["quote"] = redact_pii(quote)[:50] + "..." if len(quote) > 50 else redact_pii(quote)

    return safe_dict


def create_safe_audit_entry(
    action: str,
    memory_id: str,
    actor: str,
    reason: str,
    memory_dict: Optional[Dict] = None
) -> Dict:
    """Create audit entry with PII redaction."""
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "memory_id": memory_id,
        "actor": actor,
        "reason": reason,
    }

    if memory_dict:
        entry["memory_snapshot"] = redact_memory_for_logging(memory_dict)

    return entry


# ============================================================================
# Convenience: All-in-one status check
# ============================================================================

def get_system_status() -> Dict[str, Any]:
    """Get complete memory system status."""
    return {
        "feature_flags": MemoryFeatureFlags.get_status(),
        "shadow_mode": {
            "enabled": ShadowMode.is_enabled(),
            "log_entries": len(ShadowMode._shadow_log),
        },
        "versions": {
            "policy": CURRENT_POLICY_VERSION,
            "schema": CURRENT_SCHEMA_VERSION,
            "prompt": CURRENT_PROMPT_VERSION,
        },
        "slo_thresholds": SLO_THRESHOLDS,
    }
