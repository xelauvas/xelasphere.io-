# Artifact Patch Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the AI to surgically patch artifact code using `old_string`/`new_string` pairs instead of regenerating entire files.

**Architecture:** New `` ```artifact_patch`` fence format parsed by `artifacts.py`. The router detects it, `apply_patches()` applies replacements to the current artifact's files, and the result is persisted as a new version. System prompt updated to instruct AI to use patch format for all updates. Frontend unchanged except for toast warnings on failed patches.

**Tech Stack:** Python (Flask backend), Vanilla JS (frontend), Firestore (persistence)

---

### Task 1: Add `extract_artifact_patch_block()` to `artifacts.py`

**Files:**
- Modify: `app/artifacts.py` (add after `extract_artifact_block` at ~line 660)
- Test: `tests/test_artifacts.py`

- [ ] **Step 1: Write failing tests for patch block extraction**

Add to `tests/test_artifacts.py`:

```python
class TestExtractArtifactPatchBlock:
    """Tests for extract_artifact_patch_block function."""

    def test_parse_valid_patch_block(self):
        from app.artifacts import extract_artifact_patch_block
        text = '''Here's the fix.
```artifact_patch
{"patches":[{"file":"App.jsx","old_string":"color: red","new_string":"color: blue"}]}
```'''
        result = extract_artifact_patch_block(text)
        assert result is not None
        assert len(result['patches']) == 1
        assert result['patches'][0]['file'] == 'App.jsx'
        assert result['patches'][0]['old_string'] == 'color: red'
        assert result['patches'][0]['new_string'] == 'color: blue'

    def test_no_patch_block_returns_none(self):
        from app.artifacts import extract_artifact_patch_block
        result = extract_artifact_patch_block('Just some text without a patch block')
        assert result is None

    def test_empty_old_string_rejected(self):
        from app.artifacts import extract_artifact_patch_block
        text = '```artifact_patch\n{"patches":[{"file":"App.jsx","old_string":"","new_string":"hi"}]}\n```'
        result = extract_artifact_patch_block(text)
        assert result is None

    def test_missing_file_field_rejected(self):
        from app.artifacts import extract_artifact_patch_block
        text = '```artifact_patch\n{"patches":[{"old_string":"a","new_string":"b"}]}\n```'
        result = extract_artifact_patch_block(text)
        assert result is None

    def test_multiple_patches(self):
        from app.artifacts import extract_artifact_patch_block
        text = '''```artifact_patch
{"patches":[
  {"file":"App.jsx","old_string":"const a = 1","new_string":"const a = 2"},
  {"file":"style.css","old_string":".red{color:red}","new_string":".red{color:blue}"}
]}
```'''
        result = extract_artifact_patch_block(text)
        assert result is not None
        assert len(result['patches']) == 2

    def test_case_insensitive_fence(self):
        from app.artifacts import extract_artifact_patch_block
        text = '```Artifact_Patch\n{"patches":[{"file":"a.js","old_string":"x","new_string":"y"}]}\n```'
        result = extract_artifact_patch_block(text)
        assert result is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py::TestExtractArtifactPatchBlock -v`
Expected: FAIL — `ImportError: cannot import name 'extract_artifact_patch_block'`

- [ ] **Step 3: Implement `extract_artifact_patch_block()`**

Add to `app/artifacts.py` after the `ARTIFACT_UNCLOSED_PATTERN` regex (line ~41):

```python
ARTIFACT_PATCH_FENCE_PATTERN = re.compile(r"```artifact_patch\s*([\s\S]*?)\s*```", re.IGNORECASE)
```

Add after `extract_artifact_block()` (after line ~660):

```python
def extract_artifact_patch_block(text: str) -> Optional[Dict[str, Any]]:
    """Return parsed patch data if a ```artifact_patch block is present."""
    if not text:
        return None
    match = ARTIFACT_PATCH_FENCE_PATTERN.search(text)
    if not match:
        return None
    raw_json = match.group(1).strip()
    if not raw_json:
        return None
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning("artifact_patch block contains invalid JSON")
        return None
    if not isinstance(payload, dict):
        return None
    patches = payload.get('patches')
    if not isinstance(patches, list) or not patches:
        return None
    # Validate each patch
    for patch in patches:
        if not isinstance(patch, dict):
            return None
        if not patch.get('file') or not isinstance(patch.get('file'), str):
            return None
        if not isinstance(patch.get('old_string'), str) or not patch['old_string']:
            return None
        if not isinstance(patch.get('new_string'), str):
            return None
    return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py::TestExtractArtifactPatchBlock -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add app/artifacts.py tests/test_artifacts.py
git commit -m "feat(artifacts): add extract_artifact_patch_block() for surgical code patches"
```

---

### Task 2: Add `apply_patches()` to `artifacts.py`

**Files:**
- Modify: `app/artifacts.py` (add after `extract_artifact_patch_block`)
- Test: `tests/test_artifacts.py`

- [ ] **Step 1: Write failing tests for patch application**

Add to `tests/test_artifacts.py`:

```python
class TestApplyPatches:
    """Tests for apply_patches function."""

    def test_single_patch_applied(self):
        from app.artifacts import apply_patches
        files = [{'path': 'App.jsx', 'content': 'const color = "red";', 'language': 'jsx'}]
        patches = [{'file': 'App.jsx', 'old_string': '"red"', 'new_string': '"blue"'}]
        patched, results = apply_patches(files, patches)
        assert patched[0]['content'] == 'const color = "blue";'
        assert results[0]['status'] == 'applied'

    def test_patch_file_not_found(self):
        from app.artifacts import apply_patches
        files = [{'path': 'App.jsx', 'content': 'hello', 'language': 'jsx'}]
        patches = [{'file': 'missing.js', 'old_string': 'hello', 'new_string': 'world'}]
        patched, results = apply_patches(files, patches)
        assert results[0]['status'] == 'failed'
        assert 'not found' in results[0]['reason']

    def test_old_string_not_found(self):
        from app.artifacts import apply_patches
        files = [{'path': 'App.jsx', 'content': 'const x = 1;', 'language': 'jsx'}]
        patches = [{'file': 'App.jsx', 'old_string': 'const y = 2;', 'new_string': 'const y = 3;'}]
        patched, results = apply_patches(files, patches)
        assert results[0]['status'] == 'failed'
        assert 'not found' in results[0]['reason']

    def test_ambiguous_match(self):
        from app.artifacts import apply_patches
        files = [{'path': 'App.jsx', 'content': 'aaa bbb aaa', 'language': 'jsx'}]
        patches = [{'file': 'App.jsx', 'old_string': 'aaa', 'new_string': 'ccc'}]
        patched, results = apply_patches(files, patches)
        assert results[0]['status'] == 'failed'
        assert 'multiple' in results[0]['reason'].lower()

    def test_multiple_patches_same_file(self):
        from app.artifacts import apply_patches
        files = [{'path': 'App.jsx', 'content': 'line1\nline2\nline3', 'language': 'jsx'}]
        patches = [
            {'file': 'App.jsx', 'old_string': 'line1', 'new_string': 'LINE1'},
            {'file': 'App.jsx', 'old_string': 'line3', 'new_string': 'LINE3'},
        ]
        patched, results = apply_patches(files, patches)
        assert patched[0]['content'] == 'LINE1\nline2\nLINE3'
        assert all(r['status'] == 'applied' for r in results)

    def test_deletion_with_empty_new_string(self):
        from app.artifacts import apply_patches
        files = [{'path': 'App.jsx', 'content': 'keep\ndelete_me\nkeep', 'language': 'jsx'}]
        patches = [{'file': 'App.jsx', 'old_string': '\ndelete_me', 'new_string': ''}]
        patched, results = apply_patches(files, patches)
        assert patched[0]['content'] == 'keep\nkeep'
        assert results[0]['status'] == 'applied'

    def test_partial_success(self):
        from app.artifacts import apply_patches
        files = [{'path': 'App.jsx', 'content': 'const x = 1;', 'language': 'jsx'}]
        patches = [
            {'file': 'App.jsx', 'old_string': 'const x = 1;', 'new_string': 'const x = 2;'},
            {'file': 'App.jsx', 'old_string': 'nonexistent', 'new_string': 'whatever'},
        ]
        patched, results = apply_patches(files, patches)
        assert patched[0]['content'] == 'const x = 2;'
        assert results[0]['status'] == 'applied'
        assert results[1]['status'] == 'failed'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py::TestApplyPatches -v`
Expected: FAIL — `ImportError: cannot import name 'apply_patches'`

- [ ] **Step 3: Implement `apply_patches()`**

Add to `app/artifacts.py` after `extract_artifact_patch_block()`:

```python
def apply_patches(
    current_files: List[Dict[str, Any]],
    patches: List[Dict[str, str]],
) -> tuple:
    """
    Apply old_string/new_string patches to artifact files.

    Returns:
        (patched_files, results) where results is a list of
        {"file": str, "status": "applied"|"failed", "reason": str}
    """
    import copy
    patched_files = copy.deepcopy(current_files)
    results = []

    # Build a lookup for fast file access
    file_map = {f['path']: f for f in patched_files}

    for patch in patches:
        target_file = patch['file']
        old_string = patch['old_string']
        new_string = patch['new_string']

        if target_file not in file_map:
            results.append({'file': target_file, 'status': 'failed', 'reason': f'File "{target_file}" not found'})
            continue

        file_entry = file_map[target_file]
        content = file_entry.get('content', '')

        count = content.count(old_string)
        if count == 0:
            results.append({'file': target_file, 'status': 'failed', 'reason': f'old_string not found in {target_file}'})
            continue
        if count > 1:
            results.append({'file': target_file, 'status': 'failed', 'reason': f'old_string matches multiple locations in {target_file}'})
            continue

        file_entry['content'] = content.replace(old_string, new_string, 1)
        results.append({'file': target_file, 'status': 'applied'})

    return patched_files, results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py::TestApplyPatches -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add app/artifacts.py tests/test_artifacts.py
git commit -m "feat(artifacts): add apply_patches() for surgical code replacements"
```

---

### Task 3: Update `ArtifactRouter.route()` to detect patch blocks

**Files:**
- Modify: `app/artifacts.py:1218-1270` (the `route` method)

- [ ] **Step 1: Add patch block detection at the top of `route()`**

In `ArtifactRouter.route()` at line 1218, add patch detection **before** the existing `extract_artifact_block` call. Find the block starting at line 1222:

```python
        try:
            search_text = structured.remainder or structured.chat or ""
            logger.info(f"🔍 Searching for artifact block in text (length={len(search_text)}): {search_text[:500]}...")
            artifact_block = extract_artifact_block(search_text)
```

Insert **before** that `try` block:

```python
        # Check for artifact_patch block first (surgical updates)
        search_text_early = structured.remainder or structured.chat or ""
        patch_block = extract_artifact_patch_block(search_text_early)
        if patch_block:
            logger.info(f"🩹 Found artifact_patch block with {len(patch_block.get('patches', []))} patches")
            chat_text = structured.chat.strip() or structured.remainder or ""
            # Strip the patch fence from chat text
            chat_text = ARTIFACT_PATCH_FENCE_PATTERN.sub("", chat_text).strip()
            return ArtifactDecision(
                render_mode="artifact_patch",
                chat_payload=chat_text or "Applied code patches",
                artifact_payload=patch_block,
                contains_code=True,
                desired_action=ArtifactPreference.UPDATE,
            )
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add app/artifacts.py
git commit -m "feat(artifacts): detect artifact_patch blocks in ArtifactRouter.route()"
```

---

### Task 4: Handle `artifact_patch` in the streaming chat endpoint

**Files:**
- Modify: `app/blueprints/chat_routes.py:666-710` (streaming done handler)

- [ ] **Step 1: Add patch handling after artifact routing**

Find the block in the streaming `generate()` function where `routing.render_mode` is checked (around line 680):

```python
                        if routing.render_mode and routing.render_mode != 'inline':
                            render_mode = routing.render_mode
```

After `render_mode = routing.render_mode` and before the `if routing.artifact_payload:` block, add a patch handler:

```python
                            # Handle artifact_patch mode — apply patches to current artifact
                            if routing.render_mode == 'artifact_patch' and routing.artifact_payload:
                                patch_data = routing.artifact_payload
                                current_files = _load_current_artifact_files(user_id, session_id)
                                if current_files:
                                    from app.artifacts import apply_patches
                                    patched_files, patch_results = apply_patches(current_files, patch_data.get('patches', []))
                                    applied_count = sum(1 for r in patch_results if r['status'] == 'applied')
                                    failed_count = sum(1 for r in patch_results if r['status'] == 'failed')
                                    logger.info(f"🩹 Patch results: {applied_count} applied, {failed_count} failed")

                                    if applied_count > 0:
                                        # Build artifact payload from patched files
                                        primary_lang = patched_files[0].get('language', 'code') if patched_files else 'code'
                                        artifact_id = f'artifact-{int(time.time())}'
                                        version_id = f'version-{int(time.time())}'
                                        # Reuse the artifact_payload format for normal persistence
                                        routing.artifact_payload = {
                                            'title': 'Patched Artifact',
                                            'type': 'project',
                                            'primaryLanguage': primary_lang,
                                            'files': patched_files,
                                        }
                                        render_mode = 'artifact'

                                    if failed_count > 0:
                                        failed_msgs = [r['reason'] for r in patch_results if r['status'] == 'failed']
                                        artifact_error = f"{failed_count} patch(es) failed: {'; '.join(failed_msgs)}"
                                else:
                                    artifact_error = "No active artifact found to patch"
                                    render_mode = 'inline'
```

Also add a helper function before `generate()` (around line 620):

```python
    def _load_current_artifact_files(uid, sid):
        """Load current artifact files for patching."""
        try:
            context = load_current_artifact_context(uid, sid)
            if not context:
                return None
            # load_current_artifact_context returns formatted text, we need raw files
            # Use ArtifactStore directly
            store = artifact_store
            artifacts_data = store.list_artifacts(uid, sid)
            artifacts = artifacts_data.get('artifacts', [])
            active = next((a for a in artifacts if a.get('isActive')), artifacts[0] if artifacts else None)
            if not active:
                return None
            full = store.get_artifact(uid, sid, active['artifactId'])
            if not full:
                return None
            version = full.get('activeVersion')
            if not version:
                return None
            return version.get('files', [])
        except Exception as e:
            logger.warning(f"Failed to load artifact files for patching: {e}")
            return None
```

- [ ] **Step 2: Ensure `artifact_error` variable exists in the done handler scope**

Find the variable declarations around line 666-670. Add `artifact_error = None` if not already present:

```python
                    artifact_data = None
                    render_mode = 'inline'
                    chat_summary = full_content
                    routing = None
                    artifact_error = None
```

Then in the done payload (around line 714-721), include it:

```python
                    done_payload = {
                        'type': 'done',
                        ...
                        'artifact_error': artifact_error,
                    }
```

- [ ] **Step 3: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(artifacts): handle artifact_patch mode in streaming endpoint"
```

---

### Task 5: Handle `artifact_patch` in the non-streaming chat endpoint

**Files:**
- Modify: `app/blueprints/chat_routes.py:1747-1760` (non-streaming artifact processing)

- [ ] **Step 1: Add patch handling before persist_decision**

Find the block around line 1747:

```python
        if routing_decision.render_mode != "inline" and routing_decision.artifact_payload:
```

Add a patch handler before this block:

```python
        # Handle artifact_patch — apply patches to current artifact
        if routing_decision.render_mode == 'artifact_patch' and routing_decision.artifact_payload:
            patch_data = routing_decision.artifact_payload
            current_files = None
            try:
                store = artifact_store
                artifacts_data = store.list_artifacts(user_id, session_id)
                artifacts = artifacts_data.get('artifacts', [])
                active = next((a for a in artifacts if a.get('isActive')), artifacts[0] if artifacts else None)
                if active:
                    full = store.get_artifact(user_id, session_id, active['artifactId'])
                    if full and full.get('activeVersion'):
                        current_files = full['activeVersion'].get('files', [])
            except Exception as e:
                logger.warning(f"Failed to load artifact for patching: {e}")

            if current_files:
                patched_files, patch_results = apply_patches(current_files, patch_data.get('patches', []))
                applied_count = sum(1 for r in patch_results if r['status'] == 'applied')
                failed_count = sum(1 for r in patch_results if r['status'] == 'failed')
                logger.info(f"🩹 [NonStream] Patch results: {applied_count} applied, {failed_count} failed")

                if applied_count > 0:
                    primary_lang = patched_files[0].get('language', 'code') if patched_files else 'code'
                    routing_decision = ArtifactDecision(
                        render_mode='artifact',
                        chat_payload=routing_decision.chat_payload,
                        artifact_payload={
                            'title': full.get('title', 'Patched Artifact'),
                            'type': full.get('type', 'project'),
                            'primaryLanguage': primary_lang,
                            'files': patched_files,
                        },
                        contains_code=True,
                        desired_action=ArtifactPreference.UPDATE,
                    )

                if failed_count > 0:
                    failed_msgs = [r['reason'] for r in patch_results if r['status'] == 'failed']
                    routing_decision.error_message = f"{failed_count} patch(es) failed: {'; '.join(failed_msgs)}"
```

- [ ] **Step 2: Add import for `apply_patches` at top of file**

Find the imports from `app.artifacts` (around line 57-59) and add `apply_patches`:

```python
from app.artifacts import (
    extract_artifact_block, extract_artifact_patch_block,
    detect_artifact_update_intent, load_current_artifact_context,
    apply_patches,
)
```

- [ ] **Step 3: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(artifacts): handle artifact_patch mode in non-streaming endpoint"
```

---

### Task 6: Update system prompt to instruct AI to use patch format

**Files:**
- Modify: `app/services/chat_engine.py:650-657` (artifact update instructions)

- [ ] **Step 1: Replace the "COMPLETE updated artifact" instruction**

Find the block at lines 650-657:

```python
    "ALWAYS:\n"
    "- Output the COMPLETE updated artifact with fix applied\n"
    "- Preserve file structure and unchanged code\n"
    "- Use output_mode='artifact' in META block\n\n"
```

Replace with:

```python
    "ALWAYS use the ```artifact_patch format for updates (NEVER regenerate the full artifact):\n"
    "```artifact_patch\n"
    "{\"patches\":[{\"file\":\"App.jsx\",\"old_string\":\"exact text to find\",\"new_string\":\"replacement text\"}]}\n"
    "```\n\n"
    "Rules for patches:\n"
    "- old_string must be EXACT text from the current code (include 2-4 surrounding lines for uniqueness)\n"
    "- new_string is the replacement (empty string for deletions)\n"
    "- Multiple patches allowed in one block\n"
    "- Write a brief summary of what you changed before the patch block\n"
    "- Use output_mode='artifact_patch' in META block\n\n"
```

- [ ] **Step 2: Commit**

```bash
git add app/services/chat_engine.py
git commit -m "feat(artifacts): update system prompt to use artifact_patch format for updates"
```

---

### Task 7: Skip streaming code view for patch blocks + show patch warnings

**Files:**
- Modify: `app/static/js/codeWindow.js:2810-2825` (streaming fence detection)

- [ ] **Step 1: Update fence detection to handle artifact_patch**

Find the fence detection at line 2812:

```javascript
const fenceIdx = streamedContent.search(/```artifact\b/i);
```

Change it to exclude `artifact_patch` — patches are small, no streaming view needed:

```javascript
// Match ```artifact but NOT ```artifact_patch
const fenceIdx = streamedContent.search(/```artifact(?!_patch)\b/i);
```

- [ ] **Step 2: Show toast for patch warnings in non-streaming response**

Find the `artifact_error` toast at line 3360-3363. It already handles this:

```javascript
if (data.artifact_error && window.Toast) {
    window.Toast.warning('Artifact update failed: ' + data.artifact_error + '. Try asking again.', 6000);
}
```

This will automatically show patch failure warnings since we set `artifact_error` in the backend for failed patches. No change needed.

- [ ] **Step 3: Bump cache version**

In `app/templates/index.html`, bump the codeWindow.js version:

```html
<script src="{{ url_for('static', filename='js/codeWindow.js') }}?v=14"></script>
```

- [ ] **Step 4: Commit**

```bash
git add app/static/js/codeWindow.js app/templates/index.html
git commit -m "feat(artifacts): skip streaming view for patch blocks + patch error warnings"
```

---

### Task 8: Integration test

**Files:** None (testing only)

- [ ] **Step 1: Restart the service**

```bash
sudo systemctl restart xelasphere.service
```

- [ ] **Step 2: Test artifact patch flow**

1. Open XelaSphere, hard refresh
2. Create a new artifact: "build me a counter app in react"
3. Wait for it to render in the preview
4. Ask: "fix the styling to use dark mode"
5. Verify:
   - The AI outputs a brief summary + `` ```artifact_patch`` block (not full code)
   - The artifact updates to a new version with the changes applied
   - Preview shows the updated artifact
   - The pill shows the new version

- [ ] **Step 3: Test failed patch handling**

1. Ask the AI to fix something that doesn't exist in the code
2. Verify a toast warning appears about the failed patch

- [ ] **Step 4: Commit any fixes needed**
