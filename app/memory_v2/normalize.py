"""
Memory V2 Normalization & Canonicalization
===========================================
Key normalization and value canonicalization for deduplication and consistency.

Key normalization: Maps variations to canonical forms
Value canonicalization: Standardizes profile field formats
"""

import re
import hashlib
from typing import Any, Dict, Optional, Tuple

# Key aliases → canonical key
KEY_ALIASES = {
    # Timezone variations
    "time zone": "timezone",
    "tz": "timezone",
    "time_zone": "timezone",

    # Name variations
    "first name": "name",
    "firstname": "name",
    "first_name": "name",
    "my name": "name",
    "call me": "name",

    # Response style variations
    "short answers": "response_style",
    "be concise": "response_style",
    "be brief": "response_style",
    "be detailed": "response_style",
    "verbose": "response_style",
    "concise": "response_style",

    # Language variations
    "programming language": "language_preference",
    "coding language": "language_preference",
    "preferred language": "language_preference",

    # Location variations
    "city": "location",
    "country": "location",
    "where i live": "location",
    "based in": "location",

    # Role variations
    "job": "role",
    "job title": "role",
    "occupation": "role",
    "profession": "role",
    "work as": "role",

    # Format variations
    "code style": "format_preference",
    "formatting": "format_preference",
}

# Timezone mappings → IANA format
TIMEZONE_ALIASES = {
    # Common abbreviations
    "est": "America/New_York",
    "edt": "America/New_York",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "gmt": "Europe/London",
    "bst": "Europe/London",
    "cet": "Europe/Paris",
    "cest": "Europe/Paris",
    "ist": "Asia/Kolkata",
    "jst": "Asia/Tokyo",
    "aest": "Australia/Sydney",
    "aedt": "Australia/Sydney",

    # Natural language
    "uk time": "Europe/London",
    "london time": "Europe/London",
    "new york time": "America/New_York",
    "ny time": "America/New_York",
    "la time": "America/Los_Angeles",
    "california time": "America/Los_Angeles",
    "tokyo time": "Asia/Tokyo",
    "paris time": "Europe/Paris",
    "berlin time": "Europe/Berlin",
    "sydney time": "Australia/Sydney",
    "india time": "Asia/Kolkata",
    "indian time": "Asia/Kolkata",
}

# Response style mappings → canonical values
RESPONSE_STYLE_ALIASES = {
    "short": "concise",
    "brief": "concise",
    "terse": "concise",
    "succinct": "concise",
    "long": "detailed",
    "verbose": "detailed",
    "thorough": "detailed",
    "comprehensive": "detailed",
    "casual": "informal",
    "relaxed": "informal",
    "professional": "formal",
    "business": "formal",
}

# Language name normalization
LANGUAGE_ALIASES = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "py": "Python",
    "python": "Python",
    "python3": "Python",
    "rb": "Ruby",
    "ruby": "Ruby",
    "go": "Go",
    "golang": "Go",
    "rs": "Rust",
    "rust": "Rust",
    "c++": "C++",
    "cpp": "C++",
    "c#": "C#",
    "csharp": "C#",
    "java": "Java",
    "kotlin": "Kotlin",
    "kt": "Kotlin",
    "swift": "Swift",
    "php": "PHP",
}


def normalize_key(key: str) -> Tuple[str, str]:
    """
    Normalize a key to its canonical form.

    Args:
        key: Original key string

    Returns:
        (canonical_key, original_key)
    """
    original = key
    normalized = key.lower().strip()

    # Remove common prefixes
    for prefix in ["my ", "i ", "user ", "the "]:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]

    # Replace underscores/hyphens with spaces for matching
    normalized_for_match = normalized.replace("_", " ").replace("-", " ")

    # Check aliases
    if normalized_for_match in KEY_ALIASES:
        return KEY_ALIASES[normalized_for_match], original

    # Return normalized version (underscores, lowercase)
    canonical = normalized.replace(" ", "_").replace("-", "_")
    return canonical, original


def canonicalize_value(key: str, value: Any) -> Any:
    """
    Canonicalize a value based on its key type.

    Args:
        key: The memory key
        value: The raw value

    Returns:
        Canonicalized value
    """
    if not isinstance(value, str):
        return value

    value_lower = value.lower().strip()
    key_lower = key.lower()

    # Timezone canonicalization
    if "timezone" in key_lower or key_lower == "tz":
        if value_lower in TIMEZONE_ALIASES:
            return TIMEZONE_ALIASES[value_lower]
        # Already looks like IANA format
        if "/" in value:
            return value
        return value

    # Response style canonicalization
    if "style" in key_lower or key_lower in ["concise", "detailed", "verbose"]:
        if value_lower in RESPONSE_STYLE_ALIASES:
            return RESPONSE_STYLE_ALIASES[value_lower]
        return value

    # Language preference canonicalization
    if "language" in key_lower and "preference" in key_lower:
        if value_lower in LANGUAGE_ALIASES:
            return LANGUAGE_ALIASES[value_lower]
        return value

    # Name canonicalization (proper case)
    if key_lower == "name" or "name" in key_lower:
        return value.title()

    return value


def generate_project_id(title: str, repo: str = "", stack: str = "") -> str:
    """
    Generate stable project identity for deduplication.

    Args:
        title: Project title/name
        repo: Repository URL or name
        stack: Tech stack description

    Returns:
        Stable hash-based project ID
    """
    # Normalize inputs
    title_norm = title.lower().strip()
    repo_norm = repo.lower().strip().rstrip("/")
    stack_norm = "_".join(sorted(stack.lower().split()))

    # Create composite key
    composite = f"{title_norm}|{repo_norm}|{stack_norm}"

    # Hash for stable ID
    hash_digest = hashlib.sha256(composite.encode()).hexdigest()[:12]
    return f"proj_{hash_digest}"


class ProjectAliasRegistry:
    """
    Registry for project aliases to enable human-friendly merge.

    When user refers to the same project by different names:
    - "my chat app", "the flask project", "xelasphere"
    These can be registered as aliases for the same project_id.

    This enables:
    - Deduplication when user mentions same project differently
    - Merging memories from different aliases into same project
    - Human-friendly display names
    """

    def __init__(self):
        # project_id → set of aliases
        self._aliases: Dict[str, set] = {}
        # alias → project_id (reverse lookup)
        self._reverse: Dict[str, str] = {}

    def register_alias(self, project_id: str, alias: str) -> None:
        """
        Register an alias for a project.

        Args:
            project_id: Canonical project ID
            alias: Human-friendly name/alias
        """
        alias_norm = alias.lower().strip()

        if project_id not in self._aliases:
            self._aliases[project_id] = set()

        self._aliases[project_id].add(alias_norm)
        self._reverse[alias_norm] = project_id

    def resolve_alias(self, alias: str) -> Optional[str]:
        """
        Resolve an alias to its project_id.

        Args:
            alias: Potential alias string

        Returns:
            project_id if found, None otherwise
        """
        alias_norm = alias.lower().strip()
        return self._reverse.get(alias_norm)

    def get_aliases(self, project_id: str) -> set:
        """Get all aliases for a project_id."""
        return self._aliases.get(project_id, set()).copy()

    def find_best_match(self, text: str) -> Optional[Tuple[str, str]]:
        """
        Find the best matching project from text.

        Args:
            text: Text that might contain a project reference

        Returns:
            (project_id, matched_alias) if found, None otherwise
        """
        text_lower = text.lower()

        # Check for exact alias matches first
        for alias, project_id in self._reverse.items():
            if alias in text_lower:
                return (project_id, alias)

        return None

    def merge_projects(self, source_id: str, target_id: str) -> bool:
        """
        Merge source project into target (for human merge).

        All aliases from source become aliases of target.

        Args:
            source_id: Project to merge from
            target_id: Project to merge into

        Returns:
            True if merged successfully
        """
        if source_id not in self._aliases:
            return False

        source_aliases = self._aliases.pop(source_id)

        if target_id not in self._aliases:
            self._aliases[target_id] = set()

        # Move all aliases to target
        for alias in source_aliases:
            self._aliases[target_id].add(alias)
            self._reverse[alias] = target_id

        return True

    def to_dict(self) -> Dict[str, list]:
        """Serialize for storage."""
        return {pid: list(aliases) for pid, aliases in self._aliases.items()}

    @classmethod
    def from_dict(cls, data: Dict[str, list]) -> "ProjectAliasRegistry":
        """Deserialize from storage."""
        registry = cls()
        for project_id, aliases in data.items():
            for alias in aliases:
                registry.register_alias(project_id, alias)
        return registry


def normalize_project_reference(text: str, registry: Optional[ProjectAliasRegistry] = None) -> Tuple[str, str]:
    """
    Normalize a project reference in text.

    If registry provided, tries to match against known project aliases.
    Otherwise generates a new project_id.

    Args:
        text: Text containing project reference
        registry: Optional alias registry for matching

    Returns:
        (project_id, canonical_name)
    """
    # Try to match against registry first
    if registry:
        match = registry.find_best_match(text)
        if match:
            return match

    # Extract project identity and generate ID
    identity = extract_project_identity(text)
    project_id = generate_project_id(
        title=identity["title"],
        repo=identity["repo"],
        stack=identity["stack"]
    )

    return (project_id, identity["title"])


def extract_project_identity(value: str) -> Dict[str, str]:
    """
    Extract project identity components from a value string.

    Args:
        value: Project description string

    Returns:
        Dict with title, repo, stack fields
    """
    result = {"title": "", "repo": "", "stack": ""}

    value_lower = value.lower()

    # Extract repo if present
    repo_patterns = [
        r'github\.com/[\w-]+/[\w-]+',
        r'gitlab\.com/[\w-]+/[\w-]+',
        r'bitbucket\.org/[\w-]+/[\w-]+',
    ]
    for pattern in repo_patterns:
        match = re.search(pattern, value_lower)
        if match:
            result["repo"] = match.group(0)
            break

    # Extract stack keywords
    stack_keywords = [
        "react", "vue", "angular", "svelte",
        "node", "express", "fastapi", "flask", "django",
        "python", "javascript", "typescript", "rust", "go",
        "postgres", "mysql", "mongodb", "redis",
        "docker", "kubernetes", "aws", "gcp", "azure"
    ]
    found_stack = [kw for kw in stack_keywords if kw in value_lower]
    result["stack"] = " ".join(found_stack)

    # Title is the main description (first part before technical details)
    title_match = re.match(r'^([^,.(]+)', value)
    if title_match:
        result["title"] = title_match.group(1).strip()
    else:
        result["title"] = value[:50]

    return result


# Phrases that indicate user doesn't want something remembered
OPT_OUT_PHRASES = [
    r"don'?t\s+(?:save|store|remember)\s+(?:this|that)",  # handles "dont" and "don't"
    r"do\s+not\s+(?:save|store|remember)\s+(?:this|that)",
    r"off\s+the\s+record",
    r"forget\s+(?:this|that|what\s+i\s+(?:just\s+)?said)",
    r"don'?t\s+(?:keep|log)\s+(?:this|that)",  # handles "dont" and "don't"
    r"this\s+is\s+(?:just\s+)?(?:between\s+us|private)",
    r"(?:please\s+)?ignore\s+(?:this|that)",
    r"not\s+for\s+memory",
    r"no\s+memory\s+(?:please|pls)?",
]


def contains_opt_out(text: str) -> bool:
    """
    Check if text contains phrases indicating user doesn't want memory stored.

    Args:
        text: User message text

    Returns:
        True if opt-out phrase detected
    """
    text_lower = text.lower()
    for pattern in OPT_OUT_PHRASES:
        if re.search(pattern, text_lower):
            return True
    return False


def estimate_tokens(text: str) -> int:
    """
    Rough token estimation for memory budget.

    Uses ~4 chars per token heuristic.
    """
    return len(text) // 4 + 1


def truncate_to_budget(
    memories_text: str,
    max_tokens: int = 500,
    preserve_constraints: bool = True
) -> str:
    """
    Truncate memory context to fit within token budget.

    Args:
        memories_text: Full memory context string
        max_tokens: Maximum tokens allowed
        preserve_constraints: Always keep constraint section

    Returns:
        Truncated memory context
    """
    current_tokens = estimate_tokens(memories_text)

    if current_tokens <= max_tokens:
        return memories_text

    lines = memories_text.split("\n")
    result_lines = []
    constraint_lines = []
    current_budget = 0

    # First pass: separate constraints (always keep)
    in_constraint = False
    for line in lines:
        if "<constraint>" in line:
            in_constraint = True
        if in_constraint:
            constraint_lines.append(line)
            if "</constraint>" in line:
                in_constraint = False
        else:
            result_lines.append(line)

    # Calculate constraint budget
    constraint_text = "\n".join(constraint_lines)
    constraint_tokens = estimate_tokens(constraint_text)

    # Remaining budget for other memories
    remaining_budget = max_tokens - constraint_tokens

    # Truncate non-constraint content
    truncated = []
    for line in result_lines:
        line_tokens = estimate_tokens(line)
        if current_budget + line_tokens <= remaining_budget:
            truncated.append(line)
            current_budget += line_tokens
        elif line.startswith("<") and line.endswith(">"):
            # Keep section markers
            truncated.append(line)

    # Combine
    if constraint_lines:
        return "\n".join(truncated + constraint_lines)
    return "\n".join(truncated)
