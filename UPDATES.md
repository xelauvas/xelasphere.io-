# XelaSphere Updates

## Memory System

1. **Delete Memory Fix** - Fixed delete functionality using `dataset.text` instead of escaped innerHTML
2. **Auto-Vanish Notice** - Memory notices fade out after 3 seconds
3. **Memory Buckets System** - Structured storage with categories: preferences, projects, profile, do_not_assume
4. **LLM Bucket Router** - AI-powered routing with keyword fallback (3s timeout)
5. **Memory Entry Enhancements** - Added pinned, version, and timestamp fields
6. **Dedupe + Conflict Resolution** - Newer entries override older conflicting ones
7. **Pin Feature with UI** - Critical memories can be pinned to prevent removal
8. **Memory Anchoring** - Contradiction detection (heuristic + LLM)
9. **Memory Versioning** - Track updates with version numbers

## Session Management

10. **State-Based Session Summary** - Structured format: GOAL, DECISIONS, CONSTRAINTS, DEFINITIONS, OPEN
11. **Diff-Based Summary Updates** - Only adds new facts, prevents drift

## Retrieval System

12. **Improved Retrieval** - 2 recent turns + 2 relevant matches + diversity
13. **MMR-Style Diversity Selection** - Prevents redundant context
14. **Minimum Relevance Threshold** - Filters low-quality matches
15. **Hybrid Retrieval** - BM25 lexical + semantic embeddings + recency scoring (0.6/0.3/0.1)
30. **Two-Stage Retrieval** - Stage 1: BM25 pre-filter to 100 candidates + recency boost. Stage 2: Cosine similarity only on candidates. Reduces compute by 10-50x for large histories.
31. **Candidate Limiting** - Max 2,000 messages per user, 90-day recency window, conversation-scoped by default. User can say "search everything" to override.
32. **Quantized Embeddings** - Int8 quantization with base64 encoding reduces Firestore storage by ~5x (3KB → 550 bytes per embedding). Backwards compatible with raw float arrays.

## Response Processing

16. **Reflection Step** - Lightweight post-response analysis (next steps, memory candidates)
27. **Response Guard** - Server-side enforcement: intercepts vague inputs ("yes please"), detects duplicate responses (>75% similarity), checks fact contradictions
28. **Smart Guard (LLM-Based)** - Replaces regex with fast LLM (Groq) for ambiguity detection + embeddings for semantic duplicate detection

## Prompt Building

17. **Priority-Based Dynamic Context Packing** - Strict priority ordering with budget management
18. **System Behavior Rules** - Stateless vs Stateful mode classification, memory usage rules, context bleed prevention
19. **Ambiguity & Fact Confidence Guard** - Ask clarifying questions for unclear messages, never guess or fabricate facts
20. **Fact Consistency Rule** - Never contradict a previously stated fact in the same session
21. **Pointer Resolution Rule** - Restate numbered/ordinal items before answering ("the first one" → restate it)
22. **List Registry** - Stores numbered lists from AI responses for deterministic pointer resolution
23. **Server-Side Pointer Resolver** - Detects "10", "#10", "the second one", "key point 4", "step 3" and rewrites prompt with actual item text
29. **Header-Style List Extraction** - Extracts "Location:", "Call to Repentance:" style headers as numbered items for pointer resolution

## Artifact System

24. **Incomplete JSON Repair** - Automatically repairs truncated artifact JSON when AI output is cut off
25. **Canonical Store** - Stores authoritative truth (code, solution, TODOs) separate from chat, injected before conversation

## Document RAG System

33. **File Chunks Data Model** - Firestore schema for chunked document storage: `users/{user_id}/files` for metadata, `users/{user_id}/file_chunks` for embedded chunks with page references.
34. **Smart Text Chunking** - Splits documents at ~500-1000 tokens with 10% overlap. Respects page boundaries (PDF), headings, paragraphs, and sentences. Tracks character positions for citations.
35. **File Processor Pipeline** - Automatic chunk → embed → store on upload. Uses batch embedding for efficiency. Supports up to 300 chunks per file, 500k chars max.
36. **File Chunk Retrieval** - Extends hybrid retrieval to include file chunks. Uses BM25 pre-filter + semantic scoring with stored embeddings. Groups results by file with page references.
37. **File Intent Detection** - Pattern-based detection for document-related queries ("in the document", "from the PDF", "page 3"). Auto-retrieves relevant chunks when files are referenced.

## UI Fixes

26. **Down Arrow Icon Fix** - Updated from FA4 to FA5/6 syntax
