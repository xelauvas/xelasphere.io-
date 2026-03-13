"""
Memory V2 User Controls
========================
User commands for managing their memories.

Commands:
- show/list: View stored memories (with 'explain why' details)
- forget: Delete specific memories
- pin/unpin: Mark memories as permanent
- clear: Clear all memories
- export: Export memories as JSON
- stats: View memory statistics
- search: Search memories
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .schema import MemoryItem, MemoryType, MemoryStatus
from .store import MemoryStoreV2

logger = logging.getLogger(__name__)


class ControlCommand(str, Enum):
    """User control commands."""
    SHOW = "show"
    SHOW_ALL = "show_all"
    FORGET = "forget"
    FORGET_ALL = "forget_all"
    PIN = "pin"
    UNPIN = "unpin"
    EXPORT = "export"
    STATS = "stats"
    SEARCH = "search"


@dataclass
class CommandResult:
    """Result of executing a control command."""
    success: bool
    message: str
    data: Optional[Any] = None


class MemoryControls:
    """
    Handles user commands for memory management.

    Parses natural language commands and executes them.
    """

    # Command patterns
    COMMAND_PATTERNS = {
        ControlCommand.SHOW: [
            r"^(?:show|list|view|what do you (?:know|remember))\s*(?:my\s+)?(?:memories?)?",
            r"^what (?:have you|do you) (?:remember|know) about me",
        ],
        ControlCommand.SHOW_ALL: [
            r"^show all (?:my )?memories",
            r"^list all (?:my )?memories",
        ],
        ControlCommand.FORGET: [
            r"^forget (?:that |about )?(.+)",
            r"^delete (?:the )?memory (?:about |for )?(.+)",
            r"^remove (?:the )?memory (?:about |for )?(.+)",
        ],
        ControlCommand.FORGET_ALL: [
            r"^(?:forget|delete|clear|remove) (?:all|everything)",
            r"^(?:wipe|reset) (?:my )?memory",
        ],
        ControlCommand.PIN: [
            r"^pin (?:the )?memory (?:about |for )?(.+)",
            r"^(?:always )?remember (?:that )?(.+)",
        ],
        ControlCommand.UNPIN: [
            r"^unpin (?:the )?memory (?:about |for )?(.+)",
        ],
        ControlCommand.EXPORT: [
            r"^export (?:my )?memories",
            r"^download (?:my )?memories",
        ],
        ControlCommand.STATS: [
            r"^(?:memory )?stats",
            r"^how many memories",
        ],
        ControlCommand.SEARCH: [
            r"^search (?:my )?memories? for (.+)",
            r"^find (?:memories? (?:about |for ))?(.+)",
        ],
    }

    def __init__(self, store: MemoryStoreV2):
        self.store = store

    def parse_command(self, text: str) -> Optional[Tuple[ControlCommand, Optional[str]]]:
        """
        Parse user text for a memory command.

        Args:
            text: User's message

        Returns:
            (command, argument) or None if not a command
        """
        text_lower = text.lower().strip()

        for command, patterns in self.COMMAND_PATTERNS.items():
            for pattern in patterns:
                match = re.match(pattern, text_lower, re.IGNORECASE)
                if match:
                    # Extract argument if present
                    arg = match.group(1) if match.lastindex else None
                    return command, arg

        return None

    def execute(self, command: ControlCommand, arg: Optional[str] = None) -> CommandResult:
        """
        Execute a memory control command.

        Args:
            command: The command to execute
            arg: Optional argument

        Returns:
            CommandResult with outcome
        """
        handlers = {
            ControlCommand.SHOW: self._handle_show,
            ControlCommand.SHOW_ALL: self._handle_show_all,
            ControlCommand.FORGET: self._handle_forget,
            ControlCommand.FORGET_ALL: self._handle_forget_all,
            ControlCommand.PIN: self._handle_pin,
            ControlCommand.UNPIN: self._handle_unpin,
            ControlCommand.EXPORT: self._handle_export,
            ControlCommand.STATS: self._handle_stats,
            ControlCommand.SEARCH: self._handle_search,
        }

        handler = handlers.get(command)
        if handler:
            return handler(arg)

        return CommandResult(
            success=False,
            message=f"Unknown command: {command}"
        )

    def _handle_show(self, arg: Optional[str]) -> CommandResult:
        """Show active memories with 'explain why' details."""
        memories = self.store.get_all(include_expired=False)

        if not memories:
            return CommandResult(
                success=True,
                message="I don't have any memories stored for you yet.",
                data=[]
            )

        # Format for display with explanations
        formatted = self._format_memories_with_why(memories)
        return CommandResult(
            success=True,
            message=f"Here's what I remember about you:\n\n{formatted}",
            data=[m.to_dict() for m in memories]
        )

    def _handle_show_all(self, arg: Optional[str]) -> CommandResult:
        """Show all memories including expired."""
        memories = self.store.get_all(include_expired=True)

        if not memories:
            return CommandResult(
                success=True,
                message="No memories found.",
                data=[]
            )

        formatted = self._format_memories(memories, include_status=True)
        return CommandResult(
            success=True,
            message=f"All memories (including expired):\n\n{formatted}",
            data=[m.to_dict() for m in memories]
        )

    def _handle_forget(self, arg: Optional[str]) -> CommandResult:
        """Forget specific memory."""
        if not arg:
            return CommandResult(
                success=False,
                message="Please specify what you'd like me to forget."
            )

        # Search for matching memory
        memories = self.store.get_all()
        matches = []

        for memory in memories:
            if self._matches_query(memory, arg):
                matches.append(memory)

        if not matches:
            return CommandResult(
                success=False,
                message=f"I couldn't find any memory matching '{arg}'."
            )

        # Delete matches
        deleted = 0
        for memory in matches:
            if self.store.delete(memory.id):
                deleted += 1

        return CommandResult(
            success=True,
            message=f"Done! I've forgotten {deleted} memory item(s) about '{arg}'.",
            data={"deleted": deleted, "query": arg}
        )

    def _handle_forget_all(self, arg: Optional[str]) -> CommandResult:
        """Clear all memories."""
        count = self.store.clear_all(hard_delete=False)

        return CommandResult(
            success=True,
            message=f"I've cleared all {count} memories. Starting fresh!",
            data={"cleared": count}
        )

    def _handle_pin(self, arg: Optional[str]) -> CommandResult:
        """Pin a memory (prevent auto-expiration)."""
        if not arg:
            return CommandResult(
                success=False,
                message="Please specify what to pin."
            )

        # Find matching memory
        memories = self.store.get_all()
        matches = [m for m in memories if self._matches_query(m, arg)]

        if not matches:
            return CommandResult(
                success=False,
                message=f"No memory found matching '{arg}'."
            )

        # Pin the first match
        memory = matches[0]
        success = self.store.update(memory.id, {"pinned": True})

        if success:
            return CommandResult(
                success=True,
                message=f"Pinned memory: '{memory.key}: {memory.value}'. It will never be automatically deleted.",
                data={"pinned": memory.id}
            )

        return CommandResult(
            success=False,
            message="Failed to pin memory."
        )

    def _handle_unpin(self, arg: Optional[str]) -> CommandResult:
        """Unpin a memory."""
        if not arg:
            return CommandResult(
                success=False,
                message="Please specify what to unpin."
            )

        memories = self.store.get_all()
        matches = [m for m in memories if self._matches_query(m, arg) and m.pinned]

        if not matches:
            return CommandResult(
                success=False,
                message=f"No pinned memory found matching '{arg}'."
            )

        memory = matches[0]
        success = self.store.update(memory.id, {"pinned": False})

        if success:
            return CommandResult(
                success=True,
                message=f"Unpinned: '{memory.key}: {memory.value}'.",
                data={"unpinned": memory.id}
            )

        return CommandResult(
            success=False,
            message="Failed to unpin memory."
        )

    def _handle_export(self, arg: Optional[str]) -> CommandResult:
        """Export all memories as JSON."""
        memories = self.store.get_all(include_expired=True)

        export_data = {
            "user_id": self.store.user_id,
            "exported_at": __import__("datetime").datetime.utcnow().isoformat(),
            "memories": [m.to_dict() for m in memories]
        }

        return CommandResult(
            success=True,
            message=f"Exported {len(memories)} memories.",
            data=export_data
        )

    def _handle_stats(self, arg: Optional[str]) -> CommandResult:
        """Get memory statistics."""
        stats = self.store.get_stats()

        message = f"""Memory Statistics:
- Total memories: {stats.get('total', 0)}
- Active: {stats.get('active', 0)}
- Provisional: {stats.get('provisional', 0)}
- Pinned: {stats.get('pinned', 0)}

By type:
"""
        for type_name, count in stats.get('by_type', {}).items():
            message += f"- {type_name}: {count}\n"

        return CommandResult(
            success=True,
            message=message,
            data=stats
        )

    def _handle_search(self, arg: Optional[str]) -> CommandResult:
        """Search memories."""
        if not arg:
            return CommandResult(
                success=False,
                message="Please specify what to search for."
            )

        memories = self.store.get_all()
        matches = [m for m in memories if self._matches_query(m, arg)]

        if not matches:
            return CommandResult(
                success=True,
                message=f"No memories found matching '{arg}'.",
                data=[]
            )

        formatted = self._format_memories(matches)
        return CommandResult(
            success=True,
            message=f"Found {len(matches)} memories matching '{arg}':\n\n{formatted}",
            data=[m.to_dict() for m in matches]
        )

    def _matches_query(self, memory: MemoryItem, query: str) -> bool:
        """Check if a memory matches a search query."""
        query_lower = query.lower()
        return (
            query_lower in memory.key.lower() or
            query_lower in str(memory.value).lower() or
            any(query_lower in topic.lower() for topic in memory.scope.include_topics)
        )

    def _format_memories(
        self,
        memories: List[MemoryItem],
        include_status: bool = False
    ) -> str:
        """Format memories for display (basic format)."""
        if not memories:
            return "(none)"

        # Group by type
        by_type: Dict[str, List[MemoryItem]] = {}
        for memory in memories:
            type_name = memory.type.value
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(memory)

        lines = []
        for type_name, type_memories in by_type.items():
            lines.append(f"**{type_name.title()}:**")
            for memory in type_memories:
                pin_marker = "📌 " if memory.pinned else ""
                status_marker = f" [{memory.status.value}]" if include_status else ""
                lines.append(f"  {pin_marker}• {memory.key}: {memory.value}{status_marker}")
            lines.append("")

        return "\n".join(lines)

    def _format_memories_with_why(self, memories: List[MemoryItem]) -> str:
        """
        Format memories with 'explain why' details for transparency.

        Shows:
        - Last retrieved at
        - Score breakdown (confidence, use count)
        - Evidence snippet (original quote)
        - Status (active/provisional)
        """
        if not memories:
            return "(none)"

        # Group by type
        by_type: Dict[str, List[MemoryItem]] = {}
        for memory in memories:
            type_name = memory.type.value
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(memory)

        lines = []
        for type_name, type_memories in by_type.items():
            lines.append(f"**{type_name.title()}:**")
            for memory in type_memories:
                pin_marker = "📌 " if memory.pinned else ""
                lines.append(f"  {pin_marker}• **{memory.key}**: {memory.value}")

                # Why this memory (details)
                details = []

                # Confidence
                conf_pct = int(memory.confidence * 100)
                details.append(f"confidence: {conf_pct}%")

                # Status
                if memory.status == MemoryStatus.PROVISIONAL:
                    details.append("provisional (needs confirmation)")
                elif memory.pinned:
                    details.append("pinned (permanent)")

                # Evidence
                if memory.evidence.quote:
                    quote = memory.evidence.quote[:50] + "..." if len(memory.evidence.quote) > 50 else memory.evidence.quote
                    details.append(f"from: \"{quote}\"")

                # Usage stats
                if memory.lifecycle.use_count > 0:
                    details.append(f"used {memory.lifecycle.use_count}x")

                # Last retrieved
                if memory.lifecycle.last_used_at:
                    days_ago = (datetime.utcnow() - memory.lifecycle.last_used_at).days
                    if days_ago == 0:
                        details.append("last used: today")
                    elif days_ago == 1:
                        details.append("last used: yesterday")
                    else:
                        details.append(f"last used: {days_ago} days ago")

                # Confirmation status (for provisional)
                if memory.status == MemoryStatus.PROVISIONAL:
                    if memory.lifecycle.explicit_confirmed:
                        details.append("confirmed by user")
                    elif memory.lifecycle.repeated_by_user > 0:
                        details.append(f"restated {memory.lifecycle.repeated_by_user}x")

                lines.append(f"    _({', '.join(details)})_")
            lines.append("")

        return "\n".join(lines)


def process_memory_command(user_id: str, text: str) -> Optional[CommandResult]:
    """
    Check if text is a memory command and execute it.

    Args:
        user_id: User's ID
        text: User's message

    Returns:
        CommandResult if it was a command, None otherwise
    """
    store = MemoryStoreV2(user_id)
    controls = MemoryControls(store)

    parsed = controls.parse_command(text)
    if not parsed:
        return None

    command, arg = parsed
    return controls.execute(command, arg)


def is_memory_command(text: str) -> bool:
    """Check if text looks like a memory control command."""
    text_lower = text.lower().strip()

    command_starts = [
        "show", "list", "view", "what do you know", "what do you remember",
        "forget", "delete memory", "remove memory", "clear", "wipe",
        "pin", "unpin", "export", "download", "memory stats", "search memories"
    ]

    return any(text_lower.startswith(start) for start in command_starts)
