#!/usr/bin/env python3
"""Fail CI if new code imports memory_v2 or memory_v3 outside the allowlist.

Per spec §7.5 "Dormancy → deletion":
  - Non-blocking warn on new memory_v2/memory_v3 imports through P2.
  - Blocking at P3 entry.

Usage:
    tools/check-legacy-memory-imports.py [--mode warn|block] [PATHS ...]

Default paths: ['app/']. Default mode: warn (exits 0 with warnings).
In block mode, any import outside the allowlist exits 1.

Allowlist maintenance: edit ALLOWLIST_FILES below. Every file listed is a
deliberate exception — the shims that WILL be removed at P4, the current
memory_v2/v3 tests, the Flask wrapping-template sites, and any embedding-
only utility imports that are separate concerns from the memory-palace
retrieval path.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PATTERN = re.compile(
    r"^(from app\.memory_v[23]|import app\.memory_v[23]|"
    r"from \.memory_v[23]|import memory_v[23])",
    re.M,
)

# Files permitted to import memory_v2 / memory_v3. Paths are relative to
# repo root. Anything not in this list triggers warn/block.
#
# Rationale per file:
#   app/memory_buckets.py    — deprecated shim re-exporting memory_v2;
#                              removed at P4 per spec §3 / §7.5.
#   app/memory_gate.py       — same.
#   app/blueprints/chat_routes.py — M0.18 wrapping template; still uses
#                              legacy imports alongside the mempalace
#                              adapter.
#   app/services/chat_engine.py — has legacy imports (dead, per M0.18
#                              Stage 3 scope-refinement) that will be
#                              removed when chat_engine is revisited.
#   app/blueprints/memory_routes.py — admin routes for memory v2;
#                              mempalace-admin wiring pending a future
#                              milestone.
#   app/file_processor.py / app/chat_session.py / app/hybrid_retrieval.py
#                            — embedding-utility imports (memory_v2
#                              .embeddings.embed_text/embed_batch); these
#                              are shared-utility concerns, not memory-
#                              palace retrieval. Will move when embeddings
#                              get their own module.
ALLOWLIST_FILES = {
    "app/memory_buckets.py",
    "app/memory_gate.py",
    "app/blueprints/chat_routes.py",
    "app/services/chat_engine.py",
    "app/blueprints/memory_routes.py",
    "app/file_processor.py",
    "app/chat_session.py",
    "app/hybrid_retrieval.py",
    "app/services/smart_suggestions.py",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["warn", "block"], default="warn")
    ap.add_argument("paths", nargs="*", default=["app/"])
    args = ap.parse_args()

    hits: list[str] = []

    for root in args.paths:
        for p in Path(root).rglob("*.py"):
            # The modules themselves are naturally excluded — memory_v2
            # and memory_v3 import each other's public surface legitimately.
            if "memory_v2" in p.parts or "memory_v3" in p.parts:
                continue

            # Allowlisted files: legitimate legacy-import sites documented
            # above. Not reported.
            if str(p) in ALLOWLIST_FILES:
                continue

            try:
                text = p.read_text()
            except Exception:
                continue

            for m in PATTERN.finditer(text):
                line = text[: m.start()].count("\n") + 1
                hits.append(f"{p}:{line}: {m.group(0)}")

    if hits:
        print("Legacy memory_v2/memory_v3 imports found outside allowlist:", file=sys.stderr)
        print("\n".join(hits), file=sys.stderr)
        print(
            "\nEither remove the import or (if legitimate) add the file to "
            "ALLOWLIST_FILES in tools/check-legacy-memory-imports.py with a "
            "one-line rationale.",
            file=sys.stderr,
        )

    if args.mode == "block" and hits:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
