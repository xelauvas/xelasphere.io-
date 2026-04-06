"""Smart Context Window — context management for XelaSphere chat.

Public API:
  - ArtifactStateTracker, get_tracker: Track what the model has seen
  - ContextBuilder, BuiltContext: Smart prompt assembly
  - ActionParser, ParsedAction, StreamingActionBuffer: Response parsing
  - extract_turn_metadata, build_tier2_summary: Metadata extraction
"""

from app.context.artifact_state_tracker import (
    ArtifactStateTracker,
    FileState,
    get_tracker,
    clear_tracker_cache,
)
from app.context.context_builder import (
    ContextBuilder,
    BuiltContext,
    extract_turn_metadata,
    build_tier2_summary,
    estimate_tokens,
    microcompact_message,
)
from app.context.action_parser import (
    ActionParser,
    ParsedAction,
    StreamingActionBuffer,
)
