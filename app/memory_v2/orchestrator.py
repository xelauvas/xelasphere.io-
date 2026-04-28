"""
Memory V2 Orchestrator
======================
The ONLY public API for memory operations.

This module is the single entrypoint that the chat application should call.
It enforces all guarantees and prevents bypassing safety checks.

Usage:
    from app.memory_v2 import MemoryOrchestrator

    # Before LLM call
    context = orchestrator.get_context(user_id, user_message, session)

    # After LLM response
    result = orchestrator.post_turn(user_id, user_message, assistant_message, session, context.eval_metadata)

DO NOT import store, retrieval, extractor, judge directly.
All operations go through the orchestrator.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from .schema import MemoryItem, MemoryType, MemoryStatus
from .store import MemoryStoreV2, is_injection_attempt
from .retrieval import MemoryRetriever, RetrievalPlanner, get_memory_context
from .extractor import MemoryExtractor, MemoryCandidate
from .llm_extractor import LLMExtractionClient
from .judge import MemoryJudge, StorageExecutor, JudgmentDecision
from .decay import ConfirmationTracker
from .normalize import contains_opt_out, normalize_key, canonicalize_value
from .safeguards import (
    RateLimiter, validate_value_length, requires_stronger_evidence,
    should_confirm_before_action, is_cold_start, create_audit_entry, ReasonCode
)
from .ops import (
    MemoryFeatureFlags, ShadowMode, CURRENT_POLICY_VERSION, CURRENT_PROMPT_VERSION,
    redact_memory_for_logging, create_safe_audit_entry, SessionContext,
    should_require_profile_confirmation, SLOMetrics
)

logger = logging.getLogger(__name__)


# ============================================================================
# Public Contracts (Input/Output Types)
# ============================================================================

@dataclass
class ConversationState:
    """
    State carried between turns.

    Pass this to get_context and post_turn for continuity.
    """
    pending_confirmation_ids: List[str] = field(default_factory=list)
    last_retrieved_ids: List[str] = field(default_factory=list)
    messages_in_session: int = 0
    language_in_session: Optional[str] = None


@dataclass
class EvalMetadata:
    """
    Evaluation metadata returned with context.

    Use for debugging, dashboards, and pass to post_turn.
    Include in logs for easy correlation with user reports.
    """
    retrieved_ids: List[str]
    retrieved_scores: Dict[str, float]
    truncated: bool
    tokens_used: int
    policy_version: str
    prompt_version: str
    planner_decision: str
    cold_start: bool
    memory_pipeline: str = "v2"  # For log correlation across versions
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_pipeline": self.memory_pipeline,
            "policy_version": self.policy_version,
            "prompt_version": self.prompt_version,
            "retrieved_ids": self.retrieved_ids,
            "retrieved_scores": self.retrieved_scores,
            "truncated": self.truncated,
            "tokens_used": self.tokens_used,
            "planner_decision": self.planner_decision,
            "cold_start": self.cold_start,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ActionRequired:
    """Action that should be taken before proceeding."""
    action_type: str  # "confirm_memory", "confirm_profile", "none"
    memory_id: Optional[str] = None
    suggested_question: Optional[str] = None
    confidence: Optional[float] = None


@dataclass
class MemoryContextResult:
    """
    Result of get_context() - everything needed before LLM call.

    This is the ONLY output format for pre-LLM memory operations.
    """
    # The context to inject into the prompt
    context_block: str

    # Metadata for debugging and post_turn
    eval_metadata: EvalMetadata

    # Optional action needed (e.g., low-confidence confirmation)
    action_required: Optional[ActionRequired] = None

    # Whether memory was disabled (feature flag or opt-out)
    memory_disabled: bool = False
    disable_reason: Optional[str] = None


@dataclass
class WriteDecision:
    """Single write decision for transparency."""
    memory_type: str
    key: str
    decision: str  # "stored", "promoted", "superseded", "rejected", "shadowed"
    reason: str
    confidence: Optional[float] = None


@dataclass
class MemoryWriteResult:
    """
    Result of post_turn() - summary of all write operations.

    This is the ONLY output format for post-LLM memory operations.
    """
    # Counts
    stored: int = 0
    promoted: int = 0
    superseded: int = 0
    quarantined: int = 0
    rejected: int = 0
    shadowed: int = 0  # Would-store in shadow mode

    # Details for debugging
    decisions: List[WriteDecision] = field(default_factory=list)

    # Updated state for next turn
    new_pending_confirmation_ids: List[str] = field(default_factory=list)

    # Optional explanations for user-facing "why" UX
    user_explanations: List[Dict[str, Any]] = field(default_factory=list)

    # Whether writes were disabled
    writes_disabled: bool = False
    disable_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stored": self.stored,
            "promoted": self.promoted,
            "superseded": self.superseded,
            "quarantined": self.quarantined,
            "rejected": self.rejected,
            "shadowed": self.shadowed,
            "decisions": [
                {"type": d.memory_type, "key": d.key, "decision": d.decision, "reason": d.reason}
                for d in self.decisions
            ],
            "new_pending_confirmation_ids": self.new_pending_confirmation_ids,
            "writes_disabled": self.writes_disabled,
        }


# ============================================================================
# Precision Proxies (Helpfulness Metrics)
# ============================================================================

class PrecisionTracker:
    """
    Track precision proxies for memory helpfulness.

    Two metrics:
    1. used_in_output: Did response reflect memory? (heuristic)
    2. helpfulness: User follow-up rate / re-ask rate

    These prevent "gaming" precision by retrieving almost nothing.
    """

    @staticmethod
    def check_memory_used_in_output(
        retrieved_memories: List[MemoryItem],
        assistant_response: str
    ) -> Dict[str, bool]:
        """
        Check which retrieved memories were reflected in output.

        Heuristic: memory key or value mentioned in response.
        """
        response_lower = assistant_response.lower()
        used = {}

        for memory in retrieved_memories:
            key_used = memory.key.lower().replace("_", " ") in response_lower
            value_used = str(memory.value).lower() in response_lower

            # Check if preference affected formatting
            formatting_used = False
            if memory.type == MemoryType.PREFERENCE:
                if "concise" in str(memory.value).lower() and len(assistant_response) < 500:
                    formatting_used = True
                if "detailed" in str(memory.value).lower() and len(assistant_response) > 1000:
                    formatting_used = True

            used[memory.id] = key_used or value_used or formatting_used

        return used

    @staticmethod
    def calculate_precision_proxy(used_map: Dict[str, bool]) -> float:
        """Calculate precision = used / retrieved."""
        if not used_map:
            return 0.0
        used_count = sum(1 for v in used_map.values() if v)
        return used_count / len(used_map)


# ============================================================================
# The Orchestrator
# ============================================================================

class MemoryOrchestrator:
    """
    The ONLY public API for memory operations.

    All memory operations go through this class.
    Direct access to store/retrieval/extractor/judge is forbidden.

    Thread Safety: Create one instance per request (stateless except rate limiter cache).
    """

    def __init__(self, user_id: str, session: Optional[SessionContext] = None):
        """
        Initialize orchestrator for a user.

        Args:
            user_id: User's ID
            session: Optional session context (device, locale, etc.)
        """
        self.user_id = user_id
        self.session = session or SessionContext(user_id=user_id)

        # Internal components (never exposed)
        self._store = MemoryStoreV2(user_id)
        self._retriever = MemoryRetriever(self._store)
        self._planner = RetrievalPlanner()
        # Use LLM-based extraction (smart, language-agnostic) instead of patterns
        self._llm_client = LLMExtractionClient()
        self._extractor = MemoryExtractor(use_llm=True, llm_client=self._llm_client)
        self._judge = MemoryJudge(self._store)
        self._executor = StorageExecutor(self._store)
        self._confirmation_tracker = ConfirmationTracker(self._store)
        self._rate_limiter = RateLimiter(user_id)

    # ========================================================================
    # Public API: get_context (Before LLM)
    # ========================================================================

    def get_context(
        self,
        user_message: str,
        conversation_state: Optional[ConversationState] = None
    ) -> MemoryContextResult:
        """
        Build memory context for LLM prompt.

        This is called BEFORE the LLM generates a response.

        Args:
            user_message: The user's message (ONLY user text, never assistant)
            conversation_state: State from previous turns

        Returns:
            MemoryContextResult with context_block and metadata
        """
        state = conversation_state or ConversationState()

        # ====================================================================
        # Gate 1: Feature flag check
        # ====================================================================
        if not MemoryFeatureFlags.is_read_enabled(self.user_id):
            return MemoryContextResult(
                context_block="",
                eval_metadata=self._empty_eval_metadata("read_disabled"),
                memory_disabled=True,
                disable_reason="Memory reads disabled"
            )

        # ====================================================================
        # Gate 2: Opt-out check
        # ====================================================================
        if contains_opt_out(user_message):
            logger.info(f"Opt-out detected for user {self.user_id}")
            return MemoryContextResult(
                context_block="",
                eval_metadata=self._empty_eval_metadata("opt_out"),
                memory_disabled=True,
                disable_reason="User requested off-the-record"
            )

        # ====================================================================
        # Gate 3: Cold start check
        # ====================================================================
        stats = self._store.get_stats()
        if is_cold_start(stats.get("total", 0), stats.get("active", 0)):
            logger.debug(f"Cold start for user {self.user_id}, skipping retrieval")
            return MemoryContextResult(
                context_block="",
                eval_metadata=self._empty_eval_metadata("cold_start", cold_start=True),
                memory_disabled=False
            )

        # ====================================================================
        # Retrieval: Plan + Execute
        # ====================================================================
        try:
            # Plan retrieval strategy
            plan = self._planner.plan(user_message)

            # Log plan for debugging
            logger.debug(f"Retrieval plan: {plan.reason} for user {self.user_id}")

            if not plan.should_retrieve:
                logger.debug(f"Skipping retrieval: {plan.reason}")
                return MemoryContextResult(
                    context_block="",
                    eval_metadata=self._empty_eval_metadata(f"skip:{plan.reason}"),
                    memory_disabled=False
                )

            # Execute retrieval ONCE, then build context from results
            scored_results = self._retriever.retrieve(user_message)
            scored_memories = []
            for type_memories in scored_results.values():
                scored_memories.extend(type_memories)

            # Build context block from already-retrieved results (no second query)
            context_block, retrieval_meta = self._retriever.get_context_block(
                user_message, _prefetched_results=scored_results
            )

            # Build eval metadata from retrieval metadata
            eval_metadata = EvalMetadata(
                retrieved_ids=retrieval_meta.get("memory_ids", []),
                retrieved_scores={mid: score for mid, score in zip(
                    retrieval_meta.get("memory_ids", []),
                    retrieval_meta.get("scores", [])
                )},
                truncated=retrieval_meta.get("truncated", False),
                tokens_used=len(context_block) // 4,  # Rough token estimate
                policy_version=CURRENT_POLICY_VERSION,
                prompt_version=CURRENT_PROMPT_VERSION,
                planner_decision=plan.reason,
                cold_start=False
            )

            # ================================================================
            # Gate 4: Check for low-confidence action requirements
            # ================================================================
            action_required = self._check_action_required(scored_memories, user_message)

            # Update confirmation tracker state
            if state.pending_confirmation_ids:
                self._confirmation_tracker.set_pending_confirmation(state.pending_confirmation_ids)

            return MemoryContextResult(
                context_block=context_block,
                eval_metadata=eval_metadata,
                action_required=action_required
            )

        except Exception as e:
            logger.error(f"Retrieval failed for {self.user_id}: {e}")
            return MemoryContextResult(
                context_block="",
                eval_metadata=self._empty_eval_metadata("error"),
                memory_disabled=False
            )

    # ========================================================================
    # Public API: post_turn (After LLM)
    # ========================================================================

    def post_turn(
        self,
        user_message: str,
        assistant_message: str,
        conversation_state: Optional[ConversationState] = None,
        eval_metadata: Optional[EvalMetadata] = None
    ) -> MemoryWriteResult:
        """
        Process memory writes and confirmations after LLM response.

        This is called AFTER the LLM generates a response.

        IMPORTANT: Extraction ONLY happens on user_message.
        assistant_message is used ONLY for confirmation detection and precision tracking.

        Args:
            user_message: The user's message (extraction source)
            assistant_message: The assistant's response (NOT for extraction)
            conversation_state: State from previous turns
            eval_metadata: Metadata from get_context (for precision tracking)

        Returns:
            MemoryWriteResult with write summary and updated state
        """
        state = conversation_state or ConversationState()
        result = MemoryWriteResult()

        # ====================================================================
        # Gate 1: Feature flag check
        # ====================================================================
        if not MemoryFeatureFlags.is_write_enabled(self.user_id):
            result.writes_disabled = True
            result.disable_reason = "Memory writes disabled"
            return result

        # ====================================================================
        # Gate 2: Opt-out check
        # ====================================================================
        if contains_opt_out(user_message):
            logger.info(f"Opt-out in post_turn for user {self.user_id}")
            result.writes_disabled = True
            result.disable_reason = "User requested off-the-record"
            return result

        # ====================================================================
        # Gate 3: Rate limiter reset for new message
        # ====================================================================
        self._rate_limiter.reset_message_counter()

        # ====================================================================
        # Step 1: Process confirmations (from user_message only)
        # ====================================================================
        if state.pending_confirmation_ids:
            self._confirmation_tracker.set_pending_confirmation(state.pending_confirmation_ids)

        promoted_ids = self._confirmation_tracker.process_message_for_confirmations(
            user_message=user_message,
            user_id=self.user_id
        )

        for pid in promoted_ids:
            result.promoted += 1
            result.decisions.append(WriteDecision(
                memory_type="",
                key="",
                decision="promoted",
                reason="User confirmed"
            ))

        # ====================================================================
        # Step 2: Extract candidates (from user_message ONLY - never assistant)
        # ====================================================================
        try:
            logger.info(f"🔍 Extracting memories (msg len={len(user_message)})")
            candidates = self._extractor.extract(user_message)
            logger.info(f"🔍 Found {len(candidates)} candidates")
        except Exception as e:
            logger.error(f"Extraction failed: {e}", exc_info=True)
            candidates = []

        # ====================================================================
        # Step 3: Batch prefetch existing keys (avoids N Firestore calls)
        # ====================================================================
        if candidates:
            candidate_keys = [c.key for c in candidates]
            self._judge.prefetch_existing_keys(candidate_keys)

        # ====================================================================
        # Step 3b: Judge and execute each candidate
        # ====================================================================
        new_pending_ids = []

        for candidate in candidates:
            decision = self._process_candidate(candidate, result, state)

            # Track pending confirmations for provisional items
            if decision and decision.needs_confirmation:
                new_pending_ids.append(decision.memory_id)

        result.new_pending_confirmation_ids = new_pending_ids

        # ====================================================================
        # Step 4: Track precision (using assistant_message for heuristic only)
        # ====================================================================
        if eval_metadata and eval_metadata.retrieved_ids:
            self._track_precision(eval_metadata, assistant_message)

        # ====================================================================
        # Step 5: Audit log
        # ====================================================================
        self._log_post_turn_audit(result, user_message)

        return result

    # ========================================================================
    # Internal Methods (Never exposed)
    # ========================================================================

    def _empty_eval_metadata(self, reason: str, cold_start: bool = False) -> EvalMetadata:
        """Create empty eval metadata for disabled/error cases."""
        return EvalMetadata(
            retrieved_ids=[],
            retrieved_scores={},
            truncated=False,
            tokens_used=0,
            policy_version=CURRENT_POLICY_VERSION,
            prompt_version=CURRENT_PROMPT_VERSION,
            planner_decision=reason,
            cold_start=cold_start
        )

    def _build_context_block(self, scored_memories) -> Tuple[str, int, bool]:
        """Build context block from scored memories."""
        from .normalize import estimate_tokens, truncate_to_budget

        if not scored_memories:
            return "", 0, False

        # Format memories
        context = get_memory_context(
            self.user_id,
            query="",  # Already retrieved
            _prefetched_memories=[sm.memory for sm in scored_memories]
        )

        tokens = estimate_tokens(context)
        truncated = False

        # Apply budget
        if tokens > 500:
            context = truncate_to_budget(context, max_tokens=500)
            truncated = True
            tokens = estimate_tokens(context)

        return context, tokens, truncated

    def _check_action_required(self, scored_memories, user_message: str) -> Optional[ActionRequired]:
        """Check if any action is required before proceeding."""
        # Check for scheduling/action intents with low-confidence memories
        action_keywords = ["schedule", "send", "order", "book", "call", "email", "set"]
        is_action_intent = any(kw in user_message.lower() for kw in action_keywords)

        if not is_action_intent:
            return None

        for sm in scored_memories:
            prompt = should_confirm_before_action(sm.memory, "action")
            if prompt:
                return ActionRequired(
                    action_type="confirm_memory",
                    memory_id=prompt.memory_id,
                    suggested_question=prompt.suggested_question,
                    confidence=prompt.confidence
                )

        # Check for new device profile confirmation
        if self.session.is_new_device:
            for sm in scored_memories:
                if should_require_profile_confirmation(self.session, sm.memory.type.value, sm.memory.key):
                    return ActionRequired(
                        action_type="confirm_profile",
                        memory_id=sm.memory.id,
                        suggested_question=f"I have your {sm.memory.key} as {sm.memory.value}. Is that still correct?"
                    )

        return None

    def _process_candidate(self, candidate: MemoryCandidate, result: MemoryWriteResult, state: ConversationState):
        """Process a single extraction candidate."""

        # Gate: Rate limit
        can_write, reason = self._rate_limiter.can_write()
        if not can_write:
            result.rejected += 1
            result.decisions.append(WriteDecision(
                memory_type=candidate.type.value,
                key=candidate.key,
                decision="rejected",
                reason=f"Rate limited: {reason}"
            ))
            return None

        # Gate: Sanitizer (defense-in-depth)
        if is_injection_attempt(str(candidate.value)):
            result.rejected += 1
            result.decisions.append(WriteDecision(
                memory_type=candidate.type.value,
                key=candidate.key,
                decision="rejected",
                reason="Injection pattern detected"
            ))
            logger.warning(f"Injection blocked in orchestrator for {self.user_id}")
            return None

        # Gate: Value length
        # Create temp memory for validation
        temp_memory = MemoryItem(
            type=candidate.type,
            key=candidate.key,
            value=candidate.value
        )
        valid_length, length_reason = validate_value_length(temp_memory)
        if not valid_length:
            result.rejected += 1
            result.decisions.append(WriteDecision(
                memory_type=candidate.type.value,
                key=candidate.key,
                decision="rejected",
                reason=length_reason
            ))
            return None

        # Gate: Profile evidence (high-stakes keys)
        needs_evidence, evidence_reason = requires_stronger_evidence(temp_memory)
        if needs_evidence and candidate.confidence < 0.85:
            # Store as provisional, will need confirmation
            candidate.confidence = min(candidate.confidence, 0.6)

        # Judge decision
        judgment = self._judge.judge(candidate)
        logger.info(f"🧠 Judge decision (key={candidate.key}): {judgment.decision.value}")

        # Shadow mode: log but don't write
        if ShadowMode.is_enabled():
            ShadowMode.log_would_store(
                user_id=self.user_id,
                memory_type=candidate.type.value,
                key=candidate.key,
                value=candidate.value,
                confidence=candidate.confidence,
                decision=judgment.decision.value,
                reason=judgment.reason
            )
            result.shadowed += 1
            result.decisions.append(WriteDecision(
                memory_type=candidate.type.value,
                key=candidate.key,
                decision="shadowed",
                reason=f"Shadow mode: would {judgment.decision.value}",
                confidence=candidate.confidence
            ))
            return judgment

        # Execute based on judgment
        if judgment.decision == JudgmentDecision.STORE:
            exec_result = self._executor.execute(judgment, candidate, self.user_id)
            if exec_result.success:
                self._rate_limiter.record_write()
                result.stored += 1
                result.decisions.append(WriteDecision(
                    memory_type=candidate.type.value,
                    key=candidate.key,
                    decision="stored",
                    reason=judgment.reason,
                    confidence=candidate.confidence
                ))

        elif judgment.decision == JudgmentDecision.STORE_PROVISIONAL:
            exec_result = self._executor.execute(judgment, candidate, self.user_id)
            if exec_result.success:
                self._rate_limiter.record_write()
                result.stored += 1
                result.decisions.append(WriteDecision(
                    memory_type=candidate.type.value,
                    key=candidate.key,
                    decision="stored_provisional",
                    reason=judgment.reason,
                    confidence=candidate.confidence
                ))
                # Mark as needing confirmation
                judgment.needs_confirmation = True
                judgment.memory_id = exec_result.memory_id

        elif judgment.decision == JudgmentDecision.SUPERSEDE:
            exec_result = self._executor.execute(judgment, candidate, self.user_id)
            if exec_result.success:
                self._rate_limiter.record_write()
                self._rate_limiter.record_update()
                result.superseded += 1
                result.stored += 1
                result.decisions.append(WriteDecision(
                    memory_type=candidate.type.value,
                    key=candidate.key,
                    decision="superseded",
                    reason=judgment.reason,
                    confidence=candidate.confidence
                ))

        elif judgment.decision == JudgmentDecision.REJECT:
            result.rejected += 1
            result.decisions.append(WriteDecision(
                memory_type=candidate.type.value,
                key=candidate.key,
                decision="rejected",
                reason=judgment.reason,
                confidence=candidate.confidence
            ))

        return judgment

    def _track_precision(self, eval_metadata: EvalMetadata, assistant_message: str) -> None:
        """Track precision proxy metrics."""
        try:
            # Get the actual memory objects
            memories = []
            for mid in eval_metadata.retrieved_ids:
                mem = self._store.get(mid)
                if mem:
                    memories.append(mem)

            if memories:
                used_map = PrecisionTracker.check_memory_used_in_output(memories, assistant_message)
                precision = PrecisionTracker.calculate_precision_proxy(used_map)

                logger.debug(f"Precision proxy for {self.user_id}: {precision:.2%} ({sum(used_map.values())}/{len(used_map)})")

                # Could emit metric here for dashboards
                # metrics.emit("memory.precision_proxy", precision, user_id=self.user_id)

        except Exception as e:
            logger.debug(f"Precision tracking failed: {e}")

    def _log_post_turn_audit(self, result: MemoryWriteResult, user_message: str) -> None:
        """Log audit entry for post-turn operations."""
        if result.stored + result.promoted + result.superseded + result.quarantined == 0:
            return  # Nothing to log

        audit_entry = create_safe_audit_entry(
            action="post_turn",
            memory_id="batch",
            actor="orchestrator",
            reason="user_message_processing",
            memory_dict={
                "stored": result.stored,
                "promoted": result.promoted,
                "superseded": result.superseded,
                "rejected": result.rejected,
                "shadowed": result.shadowed,
            }
        )
        logger.info(f"Post-turn audit: {audit_entry}")


# ============================================================================
# Convenience: Module-level functions (wrap orchestrator)
# ============================================================================

def get_memory_context_for_prompt(
    user_id: str,
    user_message: str,
    session: Optional[SessionContext] = None,
    conversation_state: Optional[ConversationState] = None
) -> MemoryContextResult:
    """
    Convenience function to get memory context.

    Use this if you don't need to keep the orchestrator instance.
    """
    orchestrator = MemoryOrchestrator(user_id, session)
    return orchestrator.get_context(user_message, conversation_state)


def process_memory_after_turn(
    user_id: str,
    user_message: str,
    assistant_message: str,
    session: Optional[SessionContext] = None,
    conversation_state: Optional[ConversationState] = None,
    eval_metadata: Optional[EvalMetadata] = None
) -> MemoryWriteResult:
    """
    Convenience function to process memory after LLM turn.

    Use this if you don't need to keep the orchestrator instance.
    """
    orchestrator = MemoryOrchestrator(user_id, session)
    return orchestrator.post_turn(user_message, assistant_message, conversation_state, eval_metadata)


# ============================================================================
# Integration Example
# ============================================================================

"""
Example integration in a chat endpoint:

```python
from app.memory_v2 import (
    MemoryOrchestrator, SessionContext, ConversationState
)

@app.route('/chat', methods=['POST'])
def chat():
    user_id = get_current_user_id()
    user_message = request.json['message']

    # Build session context
    session = SessionContext(
        user_id=user_id,
        device_id=get_device_fingerprint(request),
        is_new_device=is_new_device(user_id, request)
    )

    # Get conversation state from session/cache
    conv_state = get_conversation_state(user_id)

    # BEFORE LLM: Get memory context
    orchestrator = MemoryOrchestrator(user_id, session)
    context_result = orchestrator.get_context(user_message, conv_state)

    # Check if action required
    if context_result.action_required:
        if context_result.action_required.action_type == "confirm_memory":
            # Optionally ask confirmation question
            pass

    # Build prompt with memory context
    prompt = build_prompt(
        user_message=user_message,
        memory_context=context_result.context_block,  # Inject this
        system_prompt=SYSTEM_PROMPT
    )

    # Call LLM
    assistant_message = call_llm(prompt)

    # AFTER LLM: Process memory writes
    write_result = orchestrator.post_turn(
        user_message=user_message,
        assistant_message=assistant_message,
        conversation_state=conv_state,
        eval_metadata=context_result.eval_metadata
    )

    # Update conversation state for next turn
    conv_state.pending_confirmation_ids = write_result.new_pending_confirmation_ids
    conv_state.last_retrieved_ids = context_result.eval_metadata.retrieved_ids
    conv_state.messages_in_session += 1
    save_conversation_state(user_id, conv_state)

    return jsonify({
        'response': assistant_message,
        'debug': {
            'memories_retrieved': len(context_result.eval_metadata.retrieved_ids),
            'memories_stored': write_result.stored,
        }
    })
```
"""
