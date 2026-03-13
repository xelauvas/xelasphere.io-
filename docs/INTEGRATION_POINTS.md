# Artifacts Integration Points Documentation

Generated: 2025-12-29

## Overview

This document identifies all integration points where the Claude-style Artifacts feature hooks into the existing XelaSphere chat application. It provides exact file locations, line numbers (approximate), code snippets, and architectural reasoning for each integration.

---

## Backend Integration Points

### 1. Module Imports (views.py)

**File:** `app/views.py`
**Lines:** 75-80

```python
from .artifacts import (
    ArtifactPreference,
    ArtifactRouter,
    ArtifactStore,
    parse_structured_response,
)
```

**Purpose:** Import artifact-specific utilities for parsing, routing, and persistence.

**Impact:** Makes artifact functionality available throughout the views module.

---

### 2. Artifact Service Initialization (views.py)

**File:** `app/views.py`
**Lines:** 572-573

```python
artifact_router = ArtifactRouter()
artifact_store = ArtifactStore(db)
```

**Purpose:** Create singleton instances of routing and storage services.

**Architectural Decision:**
- **Singleton Pattern:** Ensures consistent state across requests
- **Dependency Injection:** `ArtifactStore(db)` receives Firestore client
- **Module-Level Initialization:** Services available to all route handlers

**Alternative Approaches Considered:**
- Per-request initialization (rejected: overhead)
- Flask extension pattern (rejected: overkill for current needs)

---

### 3. System Prompt Enhancement (views.py)

**File:** `app/views.py`
**Lines:** 104-125

```python
DEVELOPER_INSTRUCTIONS = (
    "Act as a helpful backend assistant for this application. "
    "Follow the UI tone, keep reasoning internal, and never leak secrets. "
    "Every response must follow this structure:\n"
    "<META>{\"kind\":\"inline\"|\"artifact\",\"title\":\"...\",\"contains_code\":bool}</META>\n"
    "<CHAT>Concise natural-language reply (≤8 lines).</CHAT>\n"
    "<ARTIFACT>\n"
    "  When the user asks you to build/create/generate/edit code or multi-file projects, "
    "  output a single ```artifact { ... } ``` JSON block that lists every file in full, including "
    "  path, language, content, optional entrypoint, and meta instructions. "
    "  Do not include any prose before or after the fenced block.\n"
    "  For general discussion or very small snippets (≤12 lines / ≤800 bytes), leave this section empty "
    "  and answer inline in <CHAT> only.\n"
    "</ARTIFACT>\n"
    "Never send partial diffs—always emit the complete artifact when building or updating."
)
```

**Purpose:** Instruct AI models to output structured responses compatible with artifact parsing.

**Integration Point:** Concatenated with `SYSTEM_INSTRUCTIONS` before sending to AI API.

**Critical Requirements:**
- AI must output valid JSON in ```artifact fence
- Files array must be complete (no diffs)
- Small snippets should be inline

---

### 4. Message Processing Hook (views.py)

**File:** `app/views.py`
**Function:** `send_message_endpoint(session_id)`
**Lines:** ~2900-2950

```python
# Parse AI response into structured format
structured = parse_structured_response(raw_response)

# Route decision: inline vs artifact
routing_decision = artifact_router.route(
    structured=structured,
    user_prompt=user_prompt,
    provider_flagged_code=is_code_response
)

# Handle artifact payload
artifact_payload = None
artifact_reference = None

if routing_decision.render_mode != "inline" and routing_decision.artifact_payload:
    draft_payload = routing_decision.artifact_payload.copy()

    try:
        # Attempt to persist to Firestore
        persisted_artifact = artifact_store.persist_decision(
            user_id=user_id,
            session_id=session_id,
            decision=routing_decision,
            source_message_id=response_message_id,
            created_by='assistant'
        )
        artifact_payload = persisted_artifact
        is_pending = False

    except Exception as exc:
        logger.warning(f"Artifact persistence failed: {exc}")
        # Fallback: create draft artifact for offline rendering
        artifact_payload = build_draft_artifact(draft_payload, response_message_id)
        is_pending = True

    # Build lightweight reference for message embedding
    artifact_reference = build_artifact_reference_payload(
        artifact_payload,
        fallback_summary=response_text,
        pending=is_pending
    )
```

**Purpose:** Detect, parse, persist, and prepare artifacts for client delivery.

**Flow:**
1. Parse structured response (META/CHAT/ARTIFACT blocks)
2. Route decision based on content
3. Persist to Firestore (or create draft if offline)
4. Build reference object for message

**Error Handling:**
- Graceful degradation to draft mode
- Logs warnings without breaking message flow
- Client receives artifact even if persistence fails

---

### 5. Message Data Enrichment (views.py)

**File:** `app/views.py`
**Lines:** ~2982-2984

```python
# Add artifact to message payload
if artifact_payload and not persisted:
    chat_data['artifact_draft'] = artifact_payload
```

**Purpose:** Embed full artifact data in message for offline access.

**Conditional Logic:**
- Only include if persistence failed (`not persisted`)
- Allows client to render artifact without server round-trip
- Draft artifacts marked with `pending: true`

---

### 6. Response JSON Construction (views.py)

**File:** `app/views.py`
**Lines:** ~3039

```python
response_payload = {
    'status': 'success',
    'user_message': {...},
    'assistant_message': {
        'message_id': response_message_id,
        'role': 'assistant',
        'content': chat_text,
        'formatted_content': formatted_chat_text,
        'timestamp': datetime.now().isoformat(),
        'model': model,
        'contains_code': routing_decision.contains_code,
        'artifact': artifact_payload,           # Full artifact (if exists)
        'artifact_reference': artifact_reference  # Lightweight ref
    },
    'session': {...}
}
```

**Purpose:** Include artifact data in API response for client rendering.

**Data Separation:**
- `artifact`: Full payload (includes files, versions)
- `artifact_reference`: Lightweight metadata for list views

---

### 7. Artifact API Routes (views.py)

**File:** `app/views.py`
**Lines:** 3071-3148

#### 7.1 List Artifacts

```python
@app_routes.route('/chat/<session_id>/artifacts', methods=['GET'])
@error_handler
@login_required
def list_session_artifacts(session_id: str):
    user_id = session['user']['uid']
    try:
        payload = artifact_store.list_artifacts(user_id, session_id)
        return jsonify(payload)
    except Exception as exc:
        logger.error("Failed to load artifacts for user %s session %s: %s", user_id, session_id, exc)
        return jsonify({'error': 'Failed to load artifacts.'}), 500
```

**Purpose:** Retrieve all artifacts for a chat session.

**Response Format:**
```json
{
  "artifacts": [
    {
      "artifactId": "art_abc123",
      "title": "Web Server",
      "type": "project",
      "versionCount": 3,
      "updatedAt": "2025-12-29T12:00:00Z",
      ...
    }
  ]
}
```

#### 7.2 Get Artifact

```python
@app_routes.route('/chat/<session_id>/artifacts/<artifact_id>', methods=['GET'])
@error_handler
@login_required
def get_session_artifact(session_id: str, artifact_id: str):
    user_id = session['user']['uid']
    try:
        artifact = artifact_store.get_artifact(user_id, session_id, artifact_id)
        if not artifact:
            return jsonify({'error': 'Artifact not found.'}), 404
        return jsonify({'artifact': artifact})
    except Exception as exc:
        logger.error("Failed to fetch artifact %s: %s", artifact_id, exc)
        return jsonify({'error': 'Failed to load artifact.'}), 500
```

**Purpose:** Fetch complete artifact with versions metadata and active version files.

**Response Includes:**
- Artifact metadata
- All versions (metadata only)
- Active version with full file contents

#### 7.3 Get Specific Version

```python
@app_routes.route('/chat/<session_id>/artifacts/<artifact_id>/versions/<version_id>', methods=['GET'])
@error_handler
@login_required
def get_artifact_version(session_id: str, artifact_id: str, version_id: str):
    user_id = session['user']['uid']
    try:
        version = artifact_store.get_artifact_version(
            user_id, session_id, artifact_id, version_id, include_files=True
        )
        if not version:
            return jsonify({'error': 'Version not found.'}), 404
        return jsonify({'version': version})
    except Exception as exc:
        logger.error("Failed to fetch version %s/%s: %s", artifact_id, version_id, exc)
        return jsonify({'error': 'Failed to load artifact version.'}), 500
```

**Purpose:** Lazy-load specific version when user switches in UI.

**Performance Optimization:** Only fetches needed version instead of all versions.

#### 7.4 Download Version ZIP

```python
@app_routes.route('/chat/<session_id>/artifacts/<artifact_id>/versions/<version_id>/zip', methods=['GET'])
@error_handler
@login_required
def download_artifact_version_zip(session_id: str, artifact_id: str, version_id: str):
    user_id = session['user']['uid']
    try:
        zip_bytes = artifact_store.download_version_zip(user_id, session_id, artifact_id, version_id)
        if not zip_bytes:
            return jsonify({'error': 'Version not found.'}), 404

        response = current_app.response_class(zip_bytes, mimetype='application/zip')
        response.headers['Content-Disposition'] = f'attachment; filename="{artifact_id}_{version_id}.zip"'
        return response
    except Exception as exc:
        logger.error("Failed to download artifact zip: %s", exc)
        return jsonify({'error': 'Failed to prepare download.'}), 500
```

**Purpose:** Generate and stream ZIP archive of artifact version.

**Implementation:** Uses Python's `zipfile` module in-memory (no temp files).

#### 7.5 Set Active Version

```python
@app_routes.route('/chat/<session_id>/artifacts/active', methods=['POST'])
@error_handler
@login_required
def set_active_artifact(session_id: str):
    user_id = session['user']['uid']
    data = request.get_json() or {}
    artifact_id = data.get('artifact_id')
    version_id = data.get('version_id')

    if not artifact_id or not version_id:
        return jsonify({'error': 'artifact_id and version_id are required.'}), 400

    try:
        updated = artifact_store.set_active_version(user_id, session_id, artifact_id, version_id)
        if not updated:
            return jsonify({'error': 'Artifact or version not found.'}), 404
        return jsonify({'artifact': updated})
    except Exception as exc:
        logger.error("Failed to set active version: %s", exc)
        return jsonify({'error': 'Failed to update artifact state.'}), 500
```

**Purpose:** Pin a specific version as active (user preference persistence).

**Use Case:** User switches to older version, closes panel, reopens → last viewed version shown.

---

### 8. Artifact Utilities (utilities.py)

**File:** `app/utilities.py`
**Line:** 14

```python
from .artifacts import contains_code_block
```

**Integration Point:** Reuse artifact code detection in formatting pipeline.

**Purpose:** Share heuristics between artifact router and general text formatting.

---

### 9. Helper Functions (views.py)

**File:** `app/views.py`
**Lines:** 2643-2680

#### 9.1 Build Artifact Reference

```python
def build_artifact_reference_payload(payload: Dict[str, Any], fallback_summary: str, pending: bool) -> Dict[str, Any]:
    """Build lightweight artifact reference for message embedding."""
    return {
        'artifactId': payload.get('artifactId'),
        'versionId': payload.get('activeVersionId') or payload.get('versionId'),
        'versionNumber': payload.get('versionCount') or 1,
        'title': payload.get('title') or 'Artifact',
        'primaryLanguage': payload.get('primaryLanguage') or 'code',
        'entrypoint': payload.get('entrypointPath'),
        'summary': payload.get('summary') or fallback_summary,
        'pending': pending
    }
```

**Purpose:** Create minimal metadata object to avoid message bloat.

**Optimization:** Messages only store reference; full artifact fetched on demand.

#### 9.2 Build Draft Artifact

```python
def build_draft_artifact(payload: Dict[str, Any], message_id: str) -> Dict[str, Any]:
    """Create complete artifact structure with synthetic IDs for offline mode."""
    artifact_id = f"draft_{uuid.uuid4().hex}"
    version_id = f"ver_{uuid.uuid4().hex}"

    return {
        'artifactId': artifact_id,
        'conversationId': session.get('current_session_id'),
        'title': payload['title'],
        'type': 'project',
        'entrypointPath': payload['entrypoint'],
        'meta': payload.get('meta', {}),
        'createdAt': datetime.now().isoformat(),
        'updatedAt': datetime.now().isoformat(),
        'versionCount': 1,
        'activeVersionId': version_id,
        'createdBy': 'assistant',
        'isActive': True,
        'pending': True,
        'activeVersion': {
            'versionId': version_id,
            'versionNumber': 1,
            'entrypointPath': payload['entrypoint'],
            'primaryLanguage': payload.get('primaryLanguage', 'code'),
            'summary': payload.get('summary', ''),
            'fileCount': len(payload.get('files', [])),
            'totalBytes': payload.get('totalBytes', 0),
            'files': payload.get('files', []),
            'createdAt': datetime.now().isoformat(),
            'sourceMessageId': message_id
        },
        'versions': [{...}]  # Same as activeVersion
    }
```

**Purpose:** Provide full artifact structure when Firestore unavailable.

**Use Case:** Network interruption, Firestore downtime, rate limiting.

---

## Frontend Integration Points

### 10. Artifact Panel Initialization (index.html)

**File:** `app/templates/index.html`
**Line:** 591

```javascript
<script src="{{ url_for('static', filename='js/artifactPanel.js') }}"></script>
```

**Purpose:** Load artifact panel module before main application script.

**Loading Order:**
1. speechSynthesisModule.js
2. artifactPanel.js ← Artifact panel
3. codeWindow.js
4. script.js (main)
5. sidebarHandler.js
6. startNewChatModule.js

**Reasoning:** Panel must be initialized before message rendering.

---

### 11. Panel HTML Structure (index.html)

**File:** `app/templates/index.html`
**Lines:** 444-518

```html
<div id="artifact-panel" class="artifact-panel" aria-hidden="true">
    <!-- Resize handle -->
    <div id="artifact-panel-resize" class="artifact-panel__resize-handle"></div>

    <!-- Header -->
    <div class="artifact-panel__header">
        <div class="artifact-panel__header-left">
            <p id="artifact-panel-status" class="artifact-panel__status"></p>
            <h3 id="artifact-panel-title" class="artifact-panel__title">Artifact</h3>
            <div class="artifact-panel__meta-row">
                <span id="artifact-panel-language" class="artifact-panel__type-badge"></span>
                <label class="artifact-panel__version-label">
                    <span>Version</span>
                    <select id="artifact-version-select"></select>
                </label>
            </div>
        </div>
        <div class="artifact-panel__header-actions">
            <!-- Tab toggles -->
            <div class="artifact-panel__view-toggle" role="tablist">
                <button data-artifact-tab="preview">Preview</button>
                <button data-artifact-tab="code">Code</button>
                <button data-artifact-tab="info">Info</button>
            </div>
            <!-- Toolbar -->
            <div class="artifact-panel__toolbar">
                <button id="artifact-refresh-btn">Refresh</button>
                <button id="artifact-copy-version-btn">Copy</button>
                <button id="artifact-download-btn">Download ZIP</button>
                <button id="artifact-publish-btn">Publish</button>
                <button id="artifact-panel-close">Close</button>
            </div>
        </div>
    </div>

    <!-- Body -->
    <div class="artifact-panel__body">
        <!-- Preview tab -->
        <section data-artifact-panel="preview" class="artifact-panel__section is-active">
            <div class="artifact-preview-frame">
                <iframe id="artifact-preview-frame" sandbox="allow-scripts"></iframe>
            </div>
        </section>

        <!-- Code tab -->
        <section data-artifact-panel="code" class="artifact-panel__section">
            <div class="artifact-code">
                <div id="artifact-file-tree" class="artifact-code__tree"></div>
                <div class="artifact-code__viewer">
                    <div class="artifact-code__viewer-header">
                        <span id="artifact-current-file"></span>
                        <div class="artifact-code__actions">
                            <button id="artifact-copy-file-btn">Copy file</button>
                            <button id="artifact-copy-all-btn">Copy all</button>
                        </div>
                    </div>
                    <pre class="artifact-code__block"><code id="artifact-code-block"></code></pre>
                </div>
            </div>
        </section>

        <!-- Info tab -->
        <section data-artifact-panel="info" class="artifact-panel__section">
            <dl id="artifact-info-list" class="artifact-info-list"></dl>
        </section>
    </div>
</div>
```

**Purpose:** Define artifact panel UI structure.

**Key Elements:**
- Resizable handle for width adjustment
- Three-tab interface (Preview/Code/Info)
- Version dropdown
- Action toolbar
- Sandboxed iframe for preview

---

### 12. Panel Styles (artifact-panel.css)

**File:** `app/static/css/artifact-panel.css`
**Lines:** 1-401

**Key Style Features:**
- Fixed positioning (right edge)
- Slide-in animation (`transform: translateX(100%)` → `translateX(0)`)
- CSS variables for width (`--artifact-panel-width`, `--artifact-panel-offset`)
- Dark theme with glassmorphism
- BEM naming convention

**Integration with Body:**
```css
body.artifact-panel-open {
    /* Push temp chat button left when panel open */
}

.artifact-panel.open {
    transform: translateX(0);
}
```

---

### 13. Panel JavaScript Module (artifactPanel.js)

**File:** `app/static/js/artifactPanel.js`
**Lines:** 1-788

#### 13.1 Global Initialization

```javascript
window.artifactPanel = new ArtifactPanel();
```

**Purpose:** Expose singleton instance to global scope for cross-module access.

**Usage in Other Modules:**
```javascript
if (window.artifactPanel) {
    window.artifactPanel.attachPreview(messageEl, reference, options);
}
```

#### 13.2 ArtifactPanel Class

**Constructor:**
```javascript
constructor() {
    // DOM element references
    this.panel = document.getElementById('artifact-panel');
    this.titleEl = document.getElementById('artifact-panel-title');
    // ... 20+ element references

    // State management
    this.currentArtifact = null;
    this.currentVersion = null;
    this.currentReference = null;
    this.currentSessionId = null;
    this.activeTab = 'preview';

    // Caching
    this.versionCache = new Map();  // versionId → version data
    this.drafts = new Map();         // sessionId:artifactId → draft payload
    this.cache = new Map();          // sessionId:artifactId → artifact

    // Configuration
    this.defaultWidth = 860;
    this.minWidth = 600;
    this.currentWidth = this.loadStoredWidth();

    // Bind events
    this.bindEvents();
}
```

**Purpose:** Initialize panel state and DOM bindings.

#### 13.3 Key Methods

**attachPreview(messageEl, reference, options)**
```javascript
attachPreview(messageEl, reference, options = {}) {
    if (!this.supportsArtifact(reference, options.payload)) return;

    // Check if small snippet → render inline
    if (this.isSmallSnippet(options.payload)) {
        this.renderInlineSnippet(messageEl, options.payload, reference);
        return;
    }

    // Create artifact pill button
    const pill = document.createElement('button');
    pill.className = 'artifact-chat-pill';
    pill.dataset.artifactId = reference.artifactId;
    pill.innerHTML = `...`;

    // Handle click → open panel
    pill.addEventListener('click', () => {
        if (options.payload) {
            this.registerDraft(reference, options.payload, options.sessionId);
            this.openArtifact(options.payload, { sessionId, reference });
        } else {
            this.openFromReference(reference, options.sessionId);
        }
    });

    // Append to message footer
    const footer = this.ensureFooter(messageEl);
    footer.appendChild(pill);
}
```

**Purpose:** Attach artifact preview button to message bubble.

**openArtifact(artifact, { sessionId, reference })**
```javascript
openArtifact(artifact, { sessionId = null, reference = null } = {}) {
    if (!artifact) return;

    // Store in state
    this.currentArtifact = artifact;
    this.currentSessionId = sessionId;
    this.currentReference = reference;

    // Cache artifact
    if (sessionId && artifact.artifactId) {
        this.cache.set(
            this.buildCacheKey(sessionId, artifact.artifactId),
            artifact
        );
    }

    // Get active version
    const activeVersion = artifact.activeVersion || artifact.versions?.[0];
    if (activeVersion) {
        this.versionCache.set(activeVersion.versionId, activeVersion);
        this.currentVersion = activeVersion;
    }

    // Update UI
    this.populateVersionSelect(artifact);
    this.updateHeader(artifact);
    this.renderVersion(this.currentVersion);

    // Switch to preview tab
    this.switchTab('preview');

    // Show panel
    this.panel.classList.add('open');
    this.panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('artifact-panel-open');
    this.updateOffset(true);
}
```

**Purpose:** Open panel with artifact data.

**renderVersion(version)**
```javascript
renderVersion(version) {
    if (!version) return;

    this.currentVersion = version;
    version.files = version.files || [];

    // Render all tabs
    this.renderPreview(version);    // HTML/Markdown/Plain renderer
    this.renderFileTree(version);   // Folder structure
    this.renderCodeViewer(version, version.files[0]);  // Syntax-highlighted code
    this.renderInfo(version);       // Metadata table

    // Update status
    if (!version.files.length) {
        this.setStatus('This artifact has no files yet.', 'error');
    } else {
        this.setStatus('', 'idle');
    }
}
```

**Purpose:** Populate all tabs with version data.

---

### 14. Message Rendering Integration (startNewChatModule.js)

**File:** `app/static/js/startNewChatModule.js`
**Lines:** ~Multiple locations

#### 14.1 Render Assistant Message Hook

```javascript
async function renderAssistantMessage(messageData) {
    const messageEl = createMessageElement('assistant', messageData);

    // Stream text content
    await streamTextContent(messageEl, messageData.content);

    // Apply syntax highlighting if code detected
    if (messageData.contains_code) {
        Prism.highlightAllUnder(messageEl);
    }

    // Attach artifact preview ← INTEGRATION POINT
    if (messageData.artifact_reference && window.artifactPanel) {
        window.artifactPanel.attachPreview(messageEl, messageData.artifact_reference, {
            payload: messageData.artifact,
            sessionId: currentSessionId
        });
    }

    return messageEl;
}
```

**Purpose:** Attach artifact pill after message rendering completes.

#### 14.2 Load Chat History Hook

```javascript
function loadChatHistory(sessionId) {
    fetch(`/chat/${sessionId}/messages`)
        .then(response => response.json())
        .then(data => {
            data.messages.forEach(messageData => {
                const messageEl = renderMessage(messageData);

                // Restore artifact references from history ← INTEGRATION POINT
                if (messageData.artifact_reference && window.artifactPanel) {
                    window.artifactPanel.attachPreview(
                        messageEl,
                        messageData.artifact_reference,
                        {
                            payload: messageData.artifact_draft,  // Use draft if exists
                            sessionId: sessionId
                        }
                    );
                }
            });
        });
}
```

**Purpose:** Restore artifact pills when replaying message history.

---

### 15. CSS Variable Integration (styles.css)

**File:** `app/static/css/styles.css`
**Location:** Various selectors

```css
:root {
    --artifact-panel-width: 720px;   /* Set by artifactPanel.js */
    --artifact-panel-offset: 0px;    /* Set when panel opens */
}

.temp-chat-launcher {
    right: calc(20px + var(--artifact-panel-offset));
    transition: right 0.25s ease;
}

.content-wrapper {
    margin-right: var(--artifact-panel-offset);
}
```

**Purpose:** Dynamically adjust layout when panel opens.

**JavaScript Control:**
```javascript
document.documentElement.style.setProperty('--artifact-panel-width', `${width}px`);
document.documentElement.style.setProperty('--artifact-panel-offset', active ? `${width}px` : '0px');
```

---

## Data Flow Integration

### 16. Request → Response → Render Pipeline

```
User Input (frontend)
    │
    ├─ attachmentStore.getAll()
    ├─ Build payload
    └─ POST /chat/<session>/messages
        │
        ▼
Flask Route Handler (backend)
    │
    ├─ Validate auth
    ├─ Extract prompt
    ├─ send_prompt() → AI API
    └─ Response received
        │
        ▼
Artifact Processing (backend)
    │
    ├─ parse_structured_response()
    ├─ artifact_router.route()
    ├─ artifact_store.persist_decision()
    └─ build_artifact_reference_payload()
        │
        ▼
Firestore Write (backend)
    │
    ├─ Save user message
    ├─ Save assistant message (with artifact_reference)
    ├─ Create/update artifact document
    └─ Write version and files
        │
        ▼
JSON Response (backend → frontend)
    │
    └─ {user_message, assistant_message: {artifact, artifact_reference}}
        │
        ▼
Message Rendering (frontend)
    │
    ├─ Render user message
    ├─ Stream assistant message
    └─ artifactPanel.attachPreview()
        │
        ▼
User Clicks Pill (frontend)
    │
    ├─ Check draft cache
    ├─ Or fetch from server: GET /chat/<session>/artifacts/<id>
    └─ artifactPanel.openArtifact()
        │
        ▼
Artifact Panel Display (frontend)
    │
    ├─ Populate version dropdown
    ├─ Render preview (HTML/Markdown/Plain)
    ├─ Render file tree
    └─ Render code viewer
```

---

## Testing Integration Points

### 17. Unit Tests (test_artifacts.py)

**File:** `tests/test_artifacts.py`
**Lines:** All

**Test Coverage:**
- Artifact block parsing
- Payload validation
- Routing decisions
- Store operations (create/update)
- Error handling

**Integration:** Run with `pytest tests/test_artifacts.py`

---

## Security Integration Points

### 18. Firestore Security Rules (firestore.rules)

**File:** `firestore.rules`
**Lines:** 1-26

```javascript
match /artifacts/{artifactId} {
  allow read, update, delete: if canAccessArtifact(userId) && validArtifactType(request.resource.data);
  allow create: if canAccessArtifact(userId) && validArtifactType(request.resource.data);
}
```

**Purpose:** Enforce user ownership and type validation at database level.

### 19. Content Security Policy (app/__init__.py)

**File:** `app/__init__.py`
**Lines:** 58-71

```python
csp_directives = [
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com 'unsafe-inline'",
    "style-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com 'unsafe-inline'",
    "img-src 'self' data: https:",
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com",
    "connect-src 'self' https://api.groq.com https://openrouter.ai https://cdn.jsdelivr.net",
    "frame-src 'self' data: blob:",  # ← Allows artifact iframe rendering
    "frame-ancestors 'none'",
    "object-src 'none'",
    "form-action 'self'",
    "base-uri 'self'"
]
```

**Purpose:** Allow iframe rendering while restricting other vectors.

---

## Summary of Integration Points

| Component | Integration Type | File Location | Status |
|-----------|------------------|---------------|--------|
| Backend Module Import | Python import | views.py:75-80 | ✓ Complete |
| Service Initialization | Singleton creation | views.py:572-573 | ✓ Complete |
| System Prompt | AI instruction | views.py:104-125 | ✓ Complete |
| Message Processing | Route handler hook | views.py:~2900 | ✓ Complete |
| API Routes | REST endpoints | views.py:3071-3148 | ✓ Complete |
| Frontend Panel HTML | DOM structure | index.html:444-518 | ✓ Complete |
| Frontend Panel JS | Module initialization | artifactPanel.js:1-788 | ✓ Complete |
| Frontend Panel CSS | Styling | artifact-panel.css:1-401 | ✓ Complete |
| Message Rendering | UI attachment | startNewChatModule.js | ✓ Complete |
| CSS Variables | Layout adjustment | styles.css | ✓ Complete |
| Security Rules | Firestore access control | firestore.rules | ✓ Complete |
| CSP Headers | Content policy | __init__.py:58-71 | ✓ Complete |
| Unit Tests | Automated testing | test_artifacts.py | ✓ Complete |

---

**Document Version:** 1.0
**Last Updated:** 2025-12-29
**Maintainer:** Claude Code Assistant
