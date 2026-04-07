# Fenced Artifact Format — Design Spec

**Date:** 2026-04-07
**Status:** Draft
**Problem:** Models fail to produce valid artifact JSON when code is embedded inside JSON strings. JSON-escaping 200+ lines of React/Python inside a `"content"` field is structurally hostile to LLM generation. Unescaped quotes, newlines, and backslashes corrupt the entire payload.

---

## New Format

Metadata is a tiny JSON block. File contents are raw fenced code blocks. No JSON escaping of code ever.

### Create Artifact

```
ACTION: create_artifact

Here's the earth viewer app.

```artifact-meta
{"title": "3D Earth Explorer", "type": "project", "primaryLanguage": "jsx"}
```

```artifact-file:App.jsx:jsx
import React from 'react';
export default function App() {
  return <div>Hello Earth</div>;
}
```

```artifact-file:style.css:css
body { background: #0a0a0a; }
```
```

**Format rules:**

- `artifact-meta` block: one-line JSON object. Fields: `title` (string), `type` (string, default "project"), `primaryLanguage` (string). No file content. No arrays.
- `artifact-file:<path>:<language>` block: raw code. Path and language are colon-separated in the fence tag. Content is everything between the opening and closing triple-backtick fences.
- At least one `artifact-file` block required. `artifact-meta` is required.
- Order: `artifact-meta` first, then `artifact-file` blocks. Chat text can appear before, between, or after blocks.

### Patch Artifact — Unchanged

Patch format stays exactly as-is. Patches are small, JSON works fine.

```
ACTION: patch_artifact

Fixed the cleanup function.

```artifact_patch
{"patches":[{"file":"App.jsx","old_string":"return (<></>) => {","new_string":"return () => {"}]}
```
```

No changes to `extract_artifact_patch_block()`, `apply_patches()`, or patch visual streaming.

---

## Streaming Detection Boundaries

During SSE streaming, chunks arrive at arbitrary byte boundaries. The frontend must detect file block transitions across chunks.

### State Machine

```
IDLE
  │
  ├─ sees "```artifact-meta" ──→ IN_META
  │
IN_META
  │
  ├─ sees closing "```" ──→ META_DONE (parse JSON)
  │
META_DONE
  │
  ├─ sees "```artifact-file:<path>:<lang>" ──→ IN_FILE (open panel, set active file)
  │
IN_FILE
  │
  ├─ sees closing "```" followed by "```artifact-file:" ──→ IN_FILE (switch file)
  ├─ sees closing "```" followed by non-fence text or EOF ──→ DONE (finalize)
  ├─ receives content chunk ──→ IN_FILE (append to CodeMirror)
  │
DONE
```

### Cross-Chunk Boundary Handling

The closing ` ``` ` of one file and opening ` ```artifact-file: ` of the next may arrive in separate chunks. Buffer strategy:

1. Accumulate a small tail buffer (last 50 chars of previous chunk).
2. When a chunk starts with `artifact-file:` or similar, check the tail buffer for an unmatched ` ``` `.
3. If closing ` ``` ` is at end of a chunk with no following fence in the same chunk, hold state as MAYBE_DONE until next chunk arrives. If next chunk starts a new `artifact-file:`, transition to IN_FILE. If next chunk is plain text, transition to DONE.

Timeout: if MAYBE_DONE state persists for 2 seconds with no new chunk, assume DONE.

### Regex Patterns (Frontend)

```javascript
const ARTIFACT_META_RE = /```artifact-meta/i;
const ARTIFACT_FILE_RE = /```artifact-file:([^:]+):(\w+)/i;
const FENCE_CLOSE_RE = /^```\s*$/m;
```

### Regex Patterns (Backend — Python)

```python
FENCED_META_RE = re.compile(r"```artifact-meta\s*\n(.*?)\n\s*```", re.DOTALL | re.IGNORECASE)
FENCED_FILE_RE = re.compile(r"```artifact-file:([^:]+):(\w+)\s*\n([\s\S]*?)```", re.IGNORECASE)
```

---

## Parser Priority

1. **New fenced format first.** Check for `artifact-meta` fence in response text.
2. **Old JSON format fallback.** If no `artifact-meta` found, try old `extract_artifact_block()` (the ` ```artifact {JSON} ``` ` format).
3. **Code block extraction fallback.** If neither format found but ACTION was `create_artifact`, use `artifact_router.route()` with `_construct_artifact_from_code_blocks()`.

This order applies in both:
- `_extract_artifact()` in `action_parser.py`
- Stream done handler in `chat_routes.py`
- Non-stream handler in `chat_routes.py`

---

## Changes By File

### 1. `app/services/chat_engine.py` — System Prompt

Update `DEVELOPER_INSTRUCTIONS` artifact example from:

```
```artifact
{"title":"App Name","type":"project","primaryLanguage":"jsx",
 "files":[{"path":"App.jsx","language":"jsx","content":"..."}]}
```
```

To:

```
```artifact-meta
{"title":"App Name","type":"project","primaryLanguage":"jsx"}
```

```artifact-file:App.jsx:jsx
... your code here ...
```

```artifact-file:style.css:css
... your code here ...
```
```

Keep patch example unchanged.

### 2. `app/artifacts.py` — New Parser

Add `extract_fenced_artifact()` function:

```python
def extract_fenced_artifact(text: str) -> Optional[dict]:
    """Parse the new fenced artifact format (artifact-meta + artifact-file blocks).
    
    Returns normalized artifact payload dict, or None if format not found.
    """
    meta_match = FENCED_META_RE.search(text)
    if not meta_match:
        return None
    
    meta_json = meta_match.group(1).strip()
    meta = json.loads(meta_json)  # Tiny JSON, should never fail
    
    files = []
    for file_match in FENCED_FILE_RE.finditer(text):
        path = file_match.group(1).strip()
        language = file_match.group(2).strip()
        content = file_match.group(3)
        # Strip trailing whitespace but preserve internal formatting
        if content.endswith('\n'):
            content = content[:-1]
        files.append({
            'path': path,
            'language': language,
            'content': content,
            'isBinary': False,
            'byteSize': len(content.encode('utf-8')),
        })
    
    if not files:
        return None
    
    return normalize_artifact_payload({
        'title': meta.get('title', 'Artifact'),
        'type': meta.get('type', 'project'),
        'primaryLanguage': meta.get('primaryLanguage', files[0]['language']),
        'files': files,
    })
```

Keep `extract_artifact_block()` unchanged (old format fallback).

Add combined entry point:

```python
def extract_artifact_any_format(text: str) -> Optional[dict]:
    """Try new fenced format first, fall back to old JSON format."""
    result = extract_fenced_artifact(text)
    if result:
        return result
    return extract_artifact_block(text)
```

### 3. `app/context/action_parser.py` — Updated Extraction

Update `_extract_artifact()` to call new parser first:

```python
def _extract_artifact(text: str):
    from app.artifacts import extract_fenced_artifact, extract_artifact_block
    from app.artifacts import FENCED_META_RE, FENCED_FILE_RE, ARTIFACT_FENCE_PATTERN

    # Try new fenced format first
    payload = extract_fenced_artifact(text)
    if payload:
        # Strip all artifact blocks from chat text
        chat_text = FENCED_META_RE.sub("", text)
        chat_text = FENCED_FILE_RE.sub("", chat_text).strip()
        return payload, chat_text

    # Fall back to old JSON format
    try:
        payload = extract_artifact_block(text)
    except Exception as exc:
        logger.warning(f"ActionParser: artifact extraction failed: {exc}")
        payload = None

    chat_text = ARTIFACT_FENCE_PATTERN.sub("", text).strip()
    if not chat_text or chat_text == text:
        chat_text = _ARTIFACT_UNCLOSED_RE.sub("", text).strip()

    return payload, chat_text
```

### 4. `app/blueprints/chat_routes.py` — Both Endpoints

No structural changes needed. The `ActionParser.parse()` call returns the same `ParsedAction` dataclass. The new format is handled transparently inside `_extract_artifact()`.

The cascade retry logic stays as-is. If a model fails with the new format, the next model tries again with the same prompt (which asks for the new format).

### 5. `app/static/js/codeWindow.js` — Streaming Detection

Replace the current artifact fence detection in the SSE chunk handler:

**Current** (line ~2941):
```javascript
const fenceMatch = streamedContent.match(/```artifact(?!_patch)/i);
```

**New:**
```javascript
// New fenced format: artifact-meta signals start
const metaMatch = streamedContent.match(/```artifact-meta/i);
if (metaMatch) {
    // Parse meta JSON when we have closing fence
    const metaClose = streamedContent.indexOf('```', metaMatch.index + 16);
    if (metaClose !== -1) {
        // Meta complete, look for first file block
        const fileMatch = streamedContent.match(/```artifact-file:([^:]+):(\w+)/i);
        if (fileMatch) {
            artifactStreamActive = true;
            // ... open panel, start streaming code
        }
    }
}
// Fallback: old format detection
if (!artifactStreamActive) {
    const oldFenceMatch = streamedContent.match(/```artifact(?![-_])/i);
    // ... existing old format handling
}
```

File boundary detection during streaming:
```javascript
// When in IN_FILE state and chunk contains closing fence + new file fence
const fileSwitchMatch = chunk.match(/```\s*\n*```artifact-file:([^:]+):(\w+)/i);
if (fileSwitchMatch) {
    // Switch to new file in panel
    currentStreamingFile = fileSwitchMatch[1];
    window.artifactPanel.switchStreamingFile(fileSwitchMatch[1], fileSwitchMatch[2]);
}
```

### 6. `app/static/js/artifactPanel.js` — Minimal Changes

The panel receives the same artifact object structure from the done payload. No changes to `openArtifact()`, `renderVersion()`, or file rendering.

Add one method for streaming file switches:
```javascript
switchStreamingFile(path, language) {
    // Finalize current streaming file, start new one
    this.streamingFiles.push({ path: this.currentStreamingPath, content: this.streamingBuffer });
    this.currentStreamingPath = path;
    this.currentStreamingLanguage = language;
    this.streamingBuffer = '';
    // Update file selector in UI
}
```

---

## Known Limitations

- **Triple-backtick inside file content.** If a file contains markdown with code blocks (e.g., a README.md with ` ``` ` fences), `FENCED_FILE_RE` will match the inner fence as the closing boundary, truncating the file. This is an edge case — artifact files are typically JSX/CSS/Python, not markdown. Can be handled later with a smarter parser (e.g., counting fence depth) if it comes up in production.

## What Does NOT Change

- `extract_artifact_patch_block()` — patches stay JSON
- `apply_patches()` — patch application logic
- Patch visual streaming in frontend
- Firestore artifact storage schema
- Artifact panel rendering (receives same object)
- Version management and persistence
- `ARTIFACT_CAPABLE_MODELS` and capability gating
- `ARTIFACT_MODEL_CASCADE` retry logic

---

## Migration Strategy

1. Deploy new parser with backward compatibility (accepts both formats).
2. Update system prompt to show new format examples.
3. Models that follow instructions will produce new format. Models that don't will produce old format, which still parses.
4. No data migration needed — stored artifacts are already in the normalized format.
5. After confirming new format works reliably, optionally remove old JSON repair code (not urgent, it's harmless).

---

## Success Criteria

- "build me a todo app in react" produces artifact with correct files, opens panel, renders preview. No JSON parse failures.
- "change the background to dark" patches the artifact, persists as v2. Patch matching works.
- Full cascade: if first model fails, next model succeeds with new format.
- Old format responses from any model still parse correctly.
- Streaming shows code appearing live in the Code tab as chunks arrive.
