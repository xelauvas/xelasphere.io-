"""
Memory V2 - ChatGPT/Claude-like Memory Architecture
====================================================

PUBLIC API (the ONLY things you should import):
===============================================

    from app.memory_v2 import (
        MemoryOrchestrator,
        SessionContext,
        ConversationState,
    )

    orchestrator = MemoryOrchestrator(user_id, session)
    context = orchestrator.get_context(user_message, conversation_state)
    result = orchestrator.post_turn(user_message, assistant_message, state, context.eval_metadata)

That's it. Everything else is internal.

For admin/ops only (not for chat integration):
    from app.memory_v2 import MemoryFeatureFlags, ShadowMode, get_system_status

DO NOT import _internal modules directly. They are unstable and bypass safety gates.
"""

from .schema import (
    MemoryItem,
    MemoryType,
    MemoryStatus,
    MemoryScope,
    MemoryEvidence,
    MemoryLifecycle,
    create_memory,
    DEFAULT_TTLS,
    PROVISIONAL_TTL,
    DECAY_HALF_LIFE_DAYS,
    ALLOWED_CONSTRAINT_KEYS,
    TYPE_WEIGHTS,
)
from .store import MemoryStoreV2
from .retrieval import (
    MemoryRetriever,
    RetrievalPlanner,
    RetrievalPlan,
    ScoredMemory,
    get_memory_context,
    DEFAULT_TOP_K,
    MIN_CONFIDENCE_THRESHOLD,
)
from .extractor import (
    MemoryExtractor,
    MemoryCandidate,
    ExplicitMemoryParser,
)
from .judge import (
    MemoryJudge,
    JudgmentDecision,
    JudgmentResult,
    StorageExecutor,
)
from .conflict import (
    ConflictResolver,
    ConflictStrategy,
    ConflictInfo,
    resolve_memory_conflict,
)
from .decay import (
    DecayManager,
    ConfirmationTracker,
    run_scheduled_cleanup,
    DECAY_CONFIG,
)
from .controls import (
    MemoryControls,
    ControlCommand,
    CommandResult,
    process_memory_command,
    is_memory_command,
)
from .normalize import (
    normalize_key,
    canonicalize_value,
    contains_opt_out,
    generate_project_id,
    extract_project_identity,
    estimate_tokens,
    truncate_to_budget,
    ProjectAliasRegistry,
    normalize_project_reference,
)
from .safeguards import (
    RateLimiter,
    RateLimitConfig,
    validate_value_length,
    requires_stronger_evidence,
    should_confirm_before_action,
    is_cold_start,
    create_audit_entry,
    AuditEntry,
    ReasonCode,
    ConfirmationPrompt,
    CONFIRMATION_THRESHOLD,
)
from .jobs import (
    run_quarantine_scan,
    run_full_maintenance,
    run_batch_maintenance,
    get_memory_health_stats,
)
from .ops import (
    SLOMetrics,
    SLO_THRESHOLDS,
    check_slo_alerts,
    MemoryFeatureFlags,
    CURRENT_POLICY_VERSION,
    CURRENT_SCHEMA_VERSION,
    CURRENT_PROMPT_VERSION,
    migrate_memory,
    run_batch_migration,
    ShadowMode,
    MemoryExplanation,
    build_memory_explanation,
    SessionContext,
    should_require_profile_confirmation,
    redact_pii,
    redact_memory_for_logging,
    create_safe_audit_entry,
    get_system_status,
)
from .orchestrator import (
    MemoryOrchestrator,
    ConversationState,
    EvalMetadata,
    ActionRequired,
    MemoryContextResult,
    MemoryWriteResult,
    WriteDecision,
    PrecisionTracker,
    get_memory_context_for_prompt,
    process_memory_after_turn,
)

__all__ = [
    # =========================================
    # PUBLIC API - Use these for chat integration
    # =========================================
    'MemoryOrchestrator',
    'SessionContext',
    'ConversationState',

    # =========================================
    # ADMIN/OPS API - For dashboards and maintenance only
    # =========================================
    'MemoryFeatureFlags',
    'ShadowMode',
    'get_system_status',
    'run_full_maintenance',
    'check_slo_alerts',
    'SLOMetrics',
]

# ============================================================================
# INTERNAL EXPORTS (available but not in __all__)
# ============================================================================
# These are importable for admin tools, migrations, and testing,
# but are NOT part of the stable public API. Use at your own risk.
#
# To access: from app.memory_v2.schema import MemoryItem
# NOT: from app.memory_v2 import MemoryItem (will still work but discouraged)
# ============================================================================

_INTERNAL_EXPORTS = [
    # Schema (for migrations/admin)
    'MemoryItem',
    'MemoryType',
    'MemoryStatus',
    'MemoryScope',
    'MemoryEvidence',
    'MemoryLifecycle',
    'create_memory',
    'DEFAULT_TTLS',
    'PROVISIONAL_TTL',
    'DECAY_HALF_LIFE_DAYS',
    'ALLOWED_CONSTRAINT_KEYS',
    'TYPE_WEIGHTS',
    # Store (for migrations/admin)
    'MemoryStoreV2',
    # Retrieval (for testing)
    'MemoryRetriever',
    'RetrievalPlanner',
    'RetrievalPlan',
    'ScoredMemory',
    'get_memory_context',
    'DEFAULT_TOP_K',
    'MIN_CONFIDENCE_THRESHOLD',
    # Extractor (for testing)
    'MemoryExtractor',
    'MemoryCandidate',
    'ExplicitMemoryParser',
    # Judge (for testing)
    'MemoryJudge',
    'JudgmentDecision',
    'JudgmentResult',
    'StorageExecutor',
    # Conflict (for testing)
    'ConflictResolver',
    'ConflictStrategy',
    'ConflictInfo',
    'resolve_memory_conflict',
    # Decay (for maintenance jobs)
    'DecayManager',
    'ConfirmationTracker',
    'run_scheduled_cleanup',
    'DECAY_CONFIG',
    # Controls (for user command processing)
    'MemoryControls',
    'ControlCommand',
    'CommandResult',
    'process_memory_command',
    'is_memory_command',
    # Normalize (for migrations)
    'normalize_key',
    'canonicalize_value',
    'contains_opt_out',
    'generate_project_id',
    'extract_project_identity',
    'estimate_tokens',
    'truncate_to_budget',
    'ProjectAliasRegistry',
    'normalize_project_reference',
    # Safeguards (for testing)
    'RateLimiter',
    'RateLimitConfig',
    'validate_value_length',
    'requires_stronger_evidence',
    'should_confirm_before_action',
    'is_cold_start',
    'create_audit_entry',
    'AuditEntry',
    'ReasonCode',
    'ConfirmationPrompt',
    'CONFIRMATION_THRESHOLD',
    # Jobs (for cron/admin)
    'run_quarantine_scan',
    'run_batch_maintenance',
    'get_memory_health_stats',
    # Ops (for dashboards)
    'SLO_THRESHOLDS',
    'MemoryFeatureFlags',
    'CURRENT_POLICY_VERSION',
    'CURRENT_SCHEMA_VERSION',
    'CURRENT_PROMPT_VERSION',
    'migrate_memory',
    'run_batch_migration',
    'ShadowMode',
    'MemoryExplanation',
    'build_memory_explanation',
    'SessionContext',
    'should_require_profile_confirmation',
    'redact_pii',
    'redact_memory_for_logging',
    'create_safe_audit_entry',
    'get_system_status',
]
