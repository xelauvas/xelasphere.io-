# Chat Message Flow Documentation

Generated: 2025-12-29

## Overview

This document traces the complete lifecycle of a chat message in XelaSphere, from user input through AI processing, artifact detection, Firestore persistence, and client-side rendering. The system supports text messages, file attachments, artifact generation, and real-time streaming.

---

## High-Level Message Flow

```
┌──────────────┐
│  User Input  │
└──────┬───────┘
       │
       ▼
┌────────────────────┐
│  Client Composer   │ ← Attachment handling, CSRF token
└──────┬─────────────┘
       │ POST /chat/<session_id>/messages
       ▼
┌────────────────────────────┐
│  Flask Route Handler       │ ← Authentication, rate limiting
│  @app_routes.route()       │
└──────┬─────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│  Prompt Construction             │ ← Merge message + files
└──────┬───────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│  AI Model API Call               │ ← Groq/OpenRouter
│  send_prompt()                   │
└──────┬───────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│  Response Parsing                │
│  parse_structured_response()     │
└──────┬───────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│  Artifact Routing                │
│  artifact_router.route()         │
└──────┬───────────────────────────┘
       │
       ├─ Inline Response ──────────┐
       │                            │
       └─ Artifact Detected ────────┤
                                    │
                                    ▼
                ┌───────────────────────────────┐
                │  Artifact Persistence         │
                │  artifact_store.persist()     │
                └───────┬───────────────────────┘
                        │
                        ▼
        ┌──────────────────────────────────────┐
        │  Firestore Write (Messages + Artifacts)
        └───────┬──────────────────────────────┘
                │
                ▼
        ┌──────────────────┐
        │  JSON Response   │
        └───────┬──────────┘
                │
                ▼
        ┌────────────────────────┐
        │  Client Rendering      │
        │  - Stream text         │
        │  - Attach artifact pill│
        │  - Update UI           │
        └────────────────────────┘
```

---

## Detailed Flow Stages

### 1. User Input Stage

**Location:** `templates/index.html`, `static/script.js`, `static/js/startNewChatModule.js`

#### 1.1 Composer UI

**HTML Structure:**
```html
<footer class="chat-shell__composer">
  <div class="input-container">
    <div class="input-row">
      <!-- Actions menu (file upload, web search) -->
      <div class="actions-menu">
        <button class="actions-toggle"></button>
        <div class="actions-popover">
          <form id="upload-form">
            <input type="file" id="file-upload" multiple>
          </form>
          <label for="enable-search">Web Search</label>
        </div>
      </div>

      <!-- Message input -->
      <div class="textarea-container">
        <textarea id="prompt" placeholder="Ask anything"></textarea>
      </div>

      <!-- Send button -->
      <button id="send-button">
        <i class="fas fa-arrow-up"></i>
      </button>
    </div>
  </div>
</footer>
```

#### 1.2 File Attachment Handling

**Process:**
1. User selects files via file input
2. Files added to `attachmentStore` (global state)
3. Preview thumbnails rendered in attachment tray
4. On send, files converted to data URLs

**Code:** `static/script.js`
```javascript
const attachmentStore = (() => {
    let pending = [];

    return {
        add(file) {
            const attachment = {
                id: generateId(),
                file,
                name: file.name,
                type: file.type,
                isImage: isImageFile(file),
                dataUrl: null
            };
            pending.push(attachment);
            return attachment;
        },

        async consumeForSending(toDataUrl) {
            const attachments = [];
            for (const item of pending) {
                if (item.isImage && !item.dataUrl) {
                    item.dataUrl = await toDataUrl(item.file);
                }
                attachments.push(item);
            }
            pending = [];
            return attachments;
        }
    };
})();
```

#### 1.3 Message Composition

**Trigger:** User clicks send button or presses Enter

**Pre-send Checks:**
- Content not empty (or has attachments)
- Not currently sending another message
- CSRF token available

**Data Preparation:**
```javascript
const userPrompt = document.getElementById('prompt').value.trim();
const attachments = await getPendingAttachmentsForSend();
const webSearchEnabled = document.getElementById('enable-search').checked;

const payload = {
    prompt: userPrompt,
    user_id: currentUserId,
    model: selectedModel,
    attachments: attachments,
    enable_search: webSearchEnabled
};
```

---

### 2. Network Request Stage

**Location:** `static/js/startNewChatModule.js`

#### 2.1 HTTP Request

**Method:** POST
**Endpoint:** `/chat/<session_id>/messages`
**Headers:**
- `Content-Type: application/json`
- `X-CSRFToken: <csrf_token>`

**Request Body:**
```json
{
  "prompt": "Create a simple Python web server",
  "user_id": "user123",
  "model": "llama-3.1-70b-versatile",
  "attachments": [
    {
      "id": "att-1234",
      "name": "example.png",
      "type": "image/png",
      "isImage": true,
      "dataUrl": "data:image/png;base64,..."
    }
  ],
  "enable_search": false
}
```

#### 2.2 Request Handling

**Code Flow:**
```javascript
fetch(`/chat/${currentSessionId}/messages`, {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRFToken()
    },
    body: JSON.stringify(payload),
    signal: abortController.signal  // For abort capability
})
.then(response => response.json())
.then(data => {
    // Handle response
    renderUserMessage(data.user_message);
    streamAssistantResponse(data.assistant_message);
})
.catch(error => {
    // Handle errors
    displayErrorMessage(error);
});
```

---

### 3. Server-Side Processing

**Location:** `app/views.py`

#### 3.1 Route Handler

**Function:** `send_message_endpoint(session_id)`
**Line:** ~2750

**Decorators:**
- `@app_routes.route('/chat/<session_id>/messages', methods=['POST'])`
- `@limiter.limit("30 per minute")`
- `@error_handler`
- `@login_required`

**Validation Steps:**
1. Check authentication (`session['user']['uid']`)
2. Verify session ownership
3. Validate session exists in Firestore
4. Rate limit check
5. CSRF token validation (automatic)

#### 3.2 Data Extraction

```python
user_id = session['user']['uid']
data = request.get_json()

user_prompt = data.get('prompt', '').strip()
model = data.get('model') or DEFAULT_MODEL
attachments = data.get('attachments', [])
enable_search = data.get('enable_search', False)
```

#### 3.3 Message ID Generation

```python
import uuid

user_message_id = str(uuid.uuid4())
response_message_id = str(uuid.uuid4())
```

#### 3.4 Prompt Construction

**Simple Prompt:**
```python
final_prompt = user_prompt
```

**With Attachments:**
```python
attachment_context = []
for att in attachments:
    if att.get('isImage') and att.get('dataUrl'):
        # Extract text from image (OCR)
        extracted_text = extract_text_from_data_url(att['dataUrl'])
        attachment_context.append(f"[Image: {att['name']}]\n{extracted_text}")
    else:
        attachment_context.append(f"[File: {att['name']}]")

final_prompt = f"{user_prompt}\n\n" + "\n\n".join(attachment_context)
```

**With Web Search:**
```python
if enable_search:
    search_results = perform_web_search(user_prompt)
    final_prompt = f"{user_prompt}\n\nSearch Results:\n{search_results}"
```

#### 3.5 System Prompt Integration

**Developer Instructions (for artifacts):**
```python
DEVELOPER_INSTRUCTIONS = (
    "Act as a helpful backend assistant for this application. "
    "Follow the UI tone, keep reasoning internal, and never leak secrets. "
    "Every response must follow this structure:\n"
    "<META>{\"kind\":\"inline\"|\"artifact\",\"title\":\"...\",\"contains_code\":bool}</META>\n"
    "<CHAT>Concise natural-language reply (≤8 lines).</CHAT>\n"
    "<ARTIFACT>\n"
    "  When the user asks you to build/create/generate/edit code or multi-file projects, "
    "  output a single ```artifact { ... } ``` JSON block that lists every file in full.\n"
    "</ARTIFACT>\n"
)

messages = [
    {"role": "system", "content": SYSTEM_INSTRUCTIONS + DEVELOPER_INSTRUCTIONS},
    {"role": "user", "content": final_prompt}
]
```

---

### 4. AI Model Communication

**Location:** `app/utilities.py`

#### 4.1 Model Resolution

```python
from .model_catalog import resolve_model_id, MODEL_PROVIDERS

resolved_model = resolve_model_id(model or DEFAULT_MODEL)
provider = MODEL_PROVIDERS.get(resolved_model, "groq")
```

#### 4.2 API Configuration

**Groq API:**
```python
url = "https://api.groq.com/openai/v1/chat/completions"
headers = {
    "Authorization": f"Bearer {Config.GROQ_API_KEY}",
    "Content-Type": "application/json"
}

data = {
    "messages": messages,
    "model": resolved_model,
    "max_tokens": 2048,
    "temperature": 0.7
}
```

**OpenRouter API:**
```python
url = "https://openrouter.ai/api/v1/chat/completions"
headers = {
    "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": Config.OPENROUTER_SITE_URL,
    "X-Title": Config.OPENROUTER_APP_NAME
}
```

#### 4.3 API Call

```python
try:
    response = requests.post(url, json=data, headers=headers, timeout=15)
    response.raise_for_status()

    model_response = response.json()
    message_content = model_response['choices'][0]['message']['content']

    return message_content, is_code(message_content)

except requests.Timeout:
    return {'error': 'Request timed out', 'error_code': 'timeout'}, False

except requests.HTTPError as e:
    return {'error': f"HTTP error: {str(e)}", 'error_code': 'http_error'}, False
```

---

### 5. Response Processing

**Location:** `app/views.py` (send_message_endpoint)

#### 5.1 Parse Structured Response

```python
from .artifacts import parse_structured_response, ArtifactRouter

raw_response = send_prompt(final_prompt, model=model)

structured = parse_structured_response(raw_response)
# Returns: StructuredResponse(meta, chat, artifact, remainder)
```

**Structured Response Fields:**
- `meta`: Dictionary from `<META>` block (contains `kind`, `title`, `contains_code`)
- `chat`: Text from `<CHAT>` block
- `artifact`: Text from `<ARTIFACT>` block
- `remainder`: Everything else (fallback content)

#### 5.2 Artifact Routing

```python
artifact_router = ArtifactRouter()

routing_decision = artifact_router.route(
    structured=structured,
    user_prompt=user_prompt,
    provider_flagged_code=is_code_response
)

# Returns: ArtifactDecision(
#   render_mode: "inline" | "artifact",
#   chat_payload: str,
#   artifact_payload: dict | None,
#   contains_code: bool,
#   desired_action: ArtifactPreference
# )
```

**Decision Logic:**
1. Check for ```artifact block in response
2. If found, parse and validate JSON
3. Normalize payload (validate paths, compute sizes, etc.)
4. Determine if CREATE or UPDATE based on user prompt
5. For small snippets (≤12 lines, ≤800 bytes), render inline instead

#### 5.3 Artifact Persistence

```python
artifact_payload = None
artifact_reference = None

if routing_decision.artifact_payload:
    # Attempt to persist to Firestore
    try:
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
        # Offline/error: create draft artifact
        logger.warning(f"Failed to persist artifact: {exc}")
        draft_payload = routing_decision.artifact_payload.copy()
        artifact_payload = build_draft_artifact(draft_payload, response_message_id)
        is_pending = True

    # Build lightweight reference for message
    artifact_reference = build_artifact_reference_payload(
        artifact_payload,
        fallback_summary=chat_text,
        pending=is_pending
    )
```

**Draft Artifact Structure:**
```python
def build_draft_artifact(payload, message_id):
    return {
        'artifactId': f"draft_{uuid.uuid4().hex}",
        'conversationId': session_id,
        'title': payload['title'],
        'type': 'project',
        'entrypointPath': payload['entrypoint'],
        'meta': payload.get('meta', {}),
        'createdAt': datetime.now().isoformat(),
        'updatedAt': datetime.now().isoformat(),
        'versionCount': 1,
        'activeVersionId': f"ver_{uuid.uuid4().hex}",
        'createdBy': 'assistant',
        'isActive': True,
        'pending': True,
        'activeVersion': {
            'versionId': f"ver_{uuid.uuid4().hex}",
            'versionNumber': 1,
            'entrypointPath': payload['entrypoint'],
            'primaryLanguage': payload['primaryLanguage'],
            'summary': payload['summary'],
            'fileCount': len(payload['files']),
            'totalBytes': payload['totalBytes'],
            'files': payload['files'],
            'createdAt': datetime.now().isoformat()
        },
        'versions': [...]
    }
```

---

### 6. Firestore Persistence

**Location:** `app/artifacts.py` (ArtifactStore class)

#### 6.1 Persist Decision

```python
def persist_decision(self, user_id, session_id, decision, source_message_id, created_by='assistant'):
    if not decision.artifact_payload:
        return None

    payload = decision.artifact_payload
    preference = decision.desired_action

    # Check for active artifact in session
    active_snapshot = self._get_active_snapshot(user_id, session_id)

    if preference == ArtifactPreference.CREATE or not active_snapshot:
        return self._create_artifact(user_id, session_id, payload, source_message_id, created_by)
    else:
        return self._append_version(active_snapshot, payload, source_message_id)
```

#### 6.2 Create New Artifact

**Transactional Write:**
```python
from firebase_admin import firestore as admin_firestore

@admin_firestore.transactional
def create_artifact_txn(transaction):
    # Write artifact document
    transaction.set(artifact_ref, artifact_doc)

    # Write initial version
    transaction.set(
        artifact_ref.collection('versions').document(version_id),
        version_doc
    )

create_artifact_txn(db.transaction())

# Write files in batch (outside transaction)
batch = db.batch()
for file in payload['files']:
    file_ref = version_ref.collection('files').document()
    batch.set(file_ref, {
        'path': file['path'],
        'language': file['language'],
        'content': file['content'],
        'byteSize': file['byteSize']
    })
batch.commit()
```

#### 6.3 Append New Version

**Update Existing Artifact:**
```python
@admin_firestore.transactional
def append_version_txn(transaction):
    current = transaction.get(artifact_ref)
    data = current.to_dict()
    next_version_number = data.get('versionCount', 0) + 1

    updates = {
        'title': payload['title'],
        'entrypointPath': payload['entrypoint'],
        'updatedAt': firestore.SERVER_TIMESTAMP,
        'versionCount': next_version_number,
        'activeVersionId': version_id
    }

    transaction.update(artifact_ref, updates)
    transaction.set(
        artifact_ref.collection('versions').document(version_id),
        version_doc
    )

append_version_txn(db.transaction())
# Write files in batch
```

#### 6.4 Message Persistence

**User Message:**
```python
user_message_data = {
    'message_id': user_message_id,
    'role': 'user',
    'content': user_prompt,
    'timestamp': firestore.SERVER_TIMESTAMP,
    'attachments': attachments
}

session_ref.collection('messages').document(user_message_id).set(user_message_data)
```

**Assistant Message:**
```python
assistant_message_data = {
    'message_id': response_message_id,
    'role': 'assistant',
    'content': chat_text,
    'formatted_content': formatted_chat_text,
    'timestamp': firestore.SERVER_TIMESTAMP,
    'model': model,
    'contains_code': routing_decision.contains_code,
    'artifact_reference': artifact_reference  # Lightweight reference
}

# Include full artifact payload for offline access
if artifact_payload and not persisted:
    assistant_message_data['artifact_draft'] = artifact_payload

session_ref.collection('messages').document(response_message_id).set(assistant_message_data)
```

**Session Update:**
```python
session_ref.update({
    'message_count': firestore.Increment(2),  # User + assistant
    'last_updated': firestore.SERVER_TIMESTAMP,
    'model': model
})
```

---

### 7. Response Delivery

**Location:** `app/views.py` (send_message_endpoint)

#### 7.1 Build Response JSON

```python
response_payload = {
    'status': 'success',
    'user_message': {
        'message_id': user_message_id,
        'role': 'user',
        'content': user_prompt,
        'timestamp': datetime.now().isoformat(),
        'attachments': attachments
    },
    'assistant_message': {
        'message_id': response_message_id,
        'role': 'assistant',
        'content': chat_text,
        'formatted_content': formatted_chat_text,
        'timestamp': datetime.now().isoformat(),
        'model': model,
        'contains_code': routing_decision.contains_code,
        'artifact': artifact_payload,
        'artifact_reference': artifact_reference
    },
    'session': {
        'session_id': session_id,
        'message_count': new_message_count
    }
}

return jsonify(response_payload), 200
```

---

### 8. Client-Side Rendering

**Location:** `static/js/startNewChatModule.js`

#### 8.1 Render User Message

```javascript
function renderUserMessage(messageData) {
    const messageEl = document.createElement('div');
    messageEl.className = 'message user-message';
    messageEl.dataset.messageId = messageData.message_id;

    messageEl.innerHTML = `
        <div class="message-avatar">
            <img src="${userProfilePicture}" alt="User">
        </div>
        <div class="message-bubble">
            <div class="message-content">${escapeHtml(messageData.content)}</div>
            ${renderAttachments(messageData.attachments)}
        </div>
    `;

    chatDisplay.appendChild(messageEl);
    scrollToBottom();
}
```

#### 8.2 Stream Assistant Response

```javascript
async function streamAssistantResponse(messageData) {
    const messageEl = document.createElement('div');
    messageEl.className = 'message assistant-message';
    messageEl.dataset.messageId = messageData.message_id;

    const contentEl = document.createElement('div');
    contentEl.className = 'message-content';

    messageEl.innerHTML = `
        <div class="message-avatar">
            <img src="/static/images/logo.png" alt="AI">
        </div>
        <div class="message-bubble"></div>
    `;

    messageEl.querySelector('.message-bubble').appendChild(contentEl);
    chatDisplay.appendChild(messageEl);

    // Stream text character by character
    const fullText = messageData.content;
    await typeTextWorker.streamText(contentEl, fullText, {
        onComplete: () => {
            // Apply syntax highlighting if code detected
            if (messageData.contains_code) {
                Prism.highlightAllUnder(messageEl);
            }

            // Attach artifact preview
            if (messageData.artifact_reference) {
                attachArtifactPreview(messageEl, messageData);
            }
        }
    });
}
```

#### 8.3 Attach Artifact Preview

```javascript
function attachArtifactPreview(messageEl, messageData) {
    const { artifact, artifact_reference } = messageData;

    if (window.artifactPanel) {
        window.artifactPanel.attachPreview(messageEl, artifact_reference, {
            payload: artifact,
            sessionId: currentSessionId
        });
    }
}
```

**ArtifactPanel.attachPreview() Logic:**
1. Check if artifact is supported (not plain text)
2. Check if it's a small snippet (render inline if so)
3. Create artifact pill button in message footer
4. Store artifact in cache/draft store
5. On pill click, open artifact panel

**Artifact Pill HTML:**
```html
<div class="message-bubble-footer">
    <button class="artifact-chat-pill" data-artifact-id="art_abc123">
        <span class="artifact-chat-pill__icon">
            <i class="fas fa-code"></i>
        </span>
        <span class="artifact-chat-pill__title">Simple Web Server</span>
        <span class="artifact-chat-pill__meta">python · v1</span>
    </button>
</div>
```

---

## Special Flow Cases

### Case 1: First Message in Session

**Trigger:** `isFirstMessage === true`

**Additional Steps:**
1. Generate session title from user prompt
2. Update session document with generated title
3. Update sidebar with new session title
4. Set `isFirstMessage = false`

**Title Generation:**
```python
from .title_generator import generate_title

if is_first_message:
    session_title = generate_title(user_prompt, model=model)
    session_ref.update({'title': session_title})
```

### Case 2: Temporary/Incognito Session

**Indicator:** `session.get('is_temp') === true`

**Behavior:**
- Session not saved to Firestore
- Messages stored in session storage only
- Cleared on logout or explicit clear
- Ghost icon shown in UI

### Case 3: Web Search Enabled

**Flow:**
1. Extract search query from user prompt
2. Call search backend (Brave/Google/Bing)
3. Parse and rank results
4. Inject into AI prompt as context
5. AI generates response with citations

**Search Context Format:**
```
User Query: {user_prompt}

Search Results:
1. [{title}]({url})
   {snippet}

2. [{title}]({url})
   {snippet}

...

Please answer the user's question using the search results above.
```

### Case 4: Error Handling

**AI API Error:**
```python
if isinstance(model_response, dict) and 'error' in model_response:
    error_code = model_response.get('error_code')
    error_message = FRIENDLY_AI_ERROR_MESSAGES.get(error_code, 'An error occurred.')

    return jsonify({
        'status': 'error',
        'error': error_message,
        'error_code': error_code
    }), 500
```

**Client Error Display:**
```javascript
if (data.status === 'error') {
    displayErrorMessage(data.error);
    enableSendButton();
    return;
}

function displayErrorMessage(message) {
    const errorEl = document.createElement('div');
    errorEl.className = 'message error-message';
    errorEl.textContent = message;
    chatDisplay.appendChild(errorEl);
}
```

### Case 5: Offline Mode

**Detection:** `navigator.onLine === false` or API timeout

**Behavior:**
1. Show offline indicator in header
2. Store messages in localStorage
3. Create draft artifacts (not persisted)
4. Sync when connection restored

---

## Performance Optimizations

### Backend

1. **Batch Operations:** Files written in batches (up to 500)
2. **Parallel Processing:** Concurrent text extraction for attachments
3. **Caching:** LRU cache for markdown conversion
4. **Connection Pooling:** Reuse Firestore client

### Frontend

1. **Lazy Loading:** Load message history on scroll
2. **Debounced Scrolling:** Throttle scroll events
3. **Text Streaming:** Character-by-character rendering
4. **Draft Caching:** Store artifacts in memory

---

## Security Considerations

### Input Validation

1. **CSRF Protection:** All POST requests validated
2. **Content Length:** Max 16MB per request
3. **File Type Validation:** MIME type checking
4. **Path Sanitization:** No traversal attacks

### Output Sanitization

1. **HTML Escaping:** User content escaped
2. **DOMPurify:** Sanitize AI-generated HTML
3. **Iframe Sandboxing:** Restrict artifact preview

### Rate Limiting

- 30 messages per minute per user
- 10 file uploads per minute
- 6 image generations per minute

---

## Monitoring and Debugging

### Logging Points

1. **Request Receipt:** Log user ID, session ID, prompt length
2. **AI API Call:** Log model, prompt tokens, response time
3. **Artifact Detection:** Log artifact ID, type, file count
4. **Firestore Write:** Log document paths, operation type
5. **Errors:** Log full stack trace with context

### Metrics to Track

- **Latency:** Time from request to first byte
- **Token Usage:** Per model, per user
- **Artifact Creation Rate:** Daily/weekly
- **Error Rate:** By error type
- **Cache Hit Rate:** For version lookups

---

**Document Version:** 1.0
**Last Updated:** 2025-12-29
**Maintainer:** Claude Code Assistant
