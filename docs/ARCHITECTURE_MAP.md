# XelaSphere Application Architecture Map

Generated: 2025-12-29

## Executive Summary

XelaSphere is a Flask-based conversational AI chat application with Firebase backend integration, featuring Claude-style Artifacts for multi-file code project rendering. The application uses a modular blueprint architecture with server-side rendering (Jinja2) and client-side progressive enhancement (vanilla JavaScript).

---

## Directory Structure

```
xelasphere.io-/
├── app/
│   ├── __init__.py                 # Flask app factory with CSRF, CORS, sessions
│   ├── config.py                   # Configuration management, Firebase initialization
│   ├── views.py                    # Main application routes (3585 lines)
│   ├── auth.py                     # Authentication routes (Google, Microsoft, Discord OAuth)
│   ├── utilities.py                # Text processing, AI model communication
│   ├── artifacts.py                # Artifact parsing, routing, and Firestore persistence
│   ├── model_catalog.py            # AI model definitions and resolution
│   ├── grog_api.py                 # Groq API integration
│   ├── title_generator.py          # Chat title generation
│   ├── user_preferences.py         # User preference management
│   ├── limiter.py                  # Rate limiting configuration
│   ├── task_wrapper.py             # Background task handling
│   │
│   ├── templates/                  # Jinja2 templates
│   │   ├── base.html               # Base template
│   │   ├── index.html              # Main chat interface (636 lines)
│   │   ├── login.html              # Login page
│   │   ├── register.html           # Registration page
│   │   ├── dashboard.html          # User dashboard
│   │   ├── profile_edit.html       # Profile editing
│   │   ├── settings_api_keys.html  # API key management
│   │   ├── upgrade.html            # Upgrade page
│   │   ├── 400.html, 403.html, 404.html, 500.html
│   │   ├── csrf_error.html
│   │   ├── complete_reset.html
│   │   └── reset_password.html
│   │
│   └── static/
│       ├── css/                    # Stylesheets
│       │   ├── styles.css          # Main application styles
│       │   ├── artifact-panel.css  # Artifact panel styles (401 lines)
│       │   ├── code-window.css     # Code window styles
│       │   ├── message-styles.css  # Message bubble styles
│       │   ├── prism.css           # Syntax highlighting theme
│       │   ├── dashboard.css
│       │   ├── login.css
│       │   ├── register.css
│       │   ├── model-select.css
│       │   ├── onlineSearch.css
│       │   ├── sidebarContent.css
│       │   ├── sidebarToggle.css
│       │   ├── deleteChat.css
│       │   ├── options-btn.css
│       │   ├── api-keys.css
│       │   ├── profile-edit.css
│       │   ├── upgrade.css
│       │   └── debug.css
│       │
│       ├── js/                     # JavaScript modules
│       │   ├── artifactPanel.js    # Artifact panel UI (788 lines) ✓ IMPLEMENTED
│       │   ├── codeWindow.js       # Code rendering (60,209 bytes)
│       │   ├── startNewChatModule.js # Chat management (149KB)
│       │   ├── script.js           # Main application logic
│       │   ├── sidebarHandler.js   # Sidebar interactions
│       │   ├── modelSelect.js      # Model selection UI
│       │   ├── speechSynthesisModule.js # Text-to-speech
│       │   ├── typeTextWorker.js   # Text streaming/typing effects
│       │   ├── detectorCode.js     # Code detection utilities
│       │   ├── scrollState.js      # Scroll behavior management
│       │   ├── fetchMoreOptions.js # Option fetching
│       │   ├── dropdownHandler.js  # Dropdown menus
│       │   ├── closeAlert.js       # Alert management
│       │   └── register.js         # Registration form handling
│       │
│       └── images/                 # Static images
│           ├── logo.png
│           └── default-profile.png
│
├── tests/
│   └── test_artifacts.py          # Artifact feature tests ✓ IMPLEMENTED
│
├── logs/
│   └── app.log                     # Application logs (rotating)
│
├── uploads/                        # User file uploads (by user_id/date)
│
├── flask_session/                  # Server-side session storage
│
├── nltk_data/                      # NLTK resources (sentiment, tokenization)
│
├── firestore.rules                 # Firestore security rules ✓ IMPLEMENTED
│
├── requirements-cpu.txt            # Python dependencies
│
├── .env                            # Environment variables (not in repo)
│
├── run.py                          # Application entry point
│
└── docs/                           # Documentation
    ├── ARTIFACTS.md                # Artifact system documentation ✓ EXISTS
    ├── ARCHITECTURE_RATIONALE.md
    ├── CASE_STUDY_SECTION.md
    └── PORTFOLIO_PLAN.md
```

---

## Backend Architecture

### Flask Application Factory Pattern

**File:** `app/__init__.py`

The application uses the factory pattern for initialization:

```python
def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Security middleware
    csrf = CSRFProtect(app)
    limiter.init_app(app)
    CORS(app, resources={...})
    Session(app)

    # Register blueprints
    app.register_blueprint(app_routes)
    app.register_blueprint(auth_routes)

    # Error handlers
    @app.errorhandler(404|500|403|400|CSRFError)

    return app
```

**Key Features:**
- CSRF protection via Flask-WTF
- CORS enabled for specified origins
- Server-side sessions (filesystem or custom backend)
- Content Security Policy (CSP) headers
- Rate limiting via Flask-Limiter
- Comprehensive error handling

---

### Routing Structure

**Primary Blueprint:** `app_routes` (in `views.py`)

**Authentication Routes:** `auth_routes` (in `auth.py`)

#### Main Application Routes

| Route | Method | Purpose | Auth Required |
|-------|--------|---------|---------------|
| `/` | GET | Main chat interface | Yes |
| `/dashboard` | GET | User dashboard | Yes |
| `/account/manage` | GET | Account settings | Yes |
| `/upload` | POST | File upload handler | Yes |
| `/generate-image` | POST | AI image generation | Yes |
| `/chat/select` | POST | Select active chat session | Yes |
| `/chat/<session_id>/messages` | GET, POST | Retrieve/send messages | Yes |
| `/chat/<session_id>/messages/<message_id>` | DELETE | Delete message | Yes |
| `/chat/<session_id>/delete` | DELETE | Delete entire session | Yes |
| `/sessions` | GET | List user sessions | Yes |
| `/chat/<session_id>/title` | PUT | Update session title | Yes |

#### Artifact-Specific Routes ✓ IMPLEMENTED

| Route | Method | Purpose | Status |
|-------|--------|---------|--------|
| `/chat/<session_id>/artifacts` | GET | List all artifacts | ✓ Complete |
| `/chat/<session_id>/artifacts/<artifact_id>` | GET | Get artifact details | ✓ Complete |
| `/chat/<session_id>/artifacts/<artifact_id>/versions/<version_id>` | GET | Get specific version | ✓ Complete |
| `/chat/<session_id>/artifacts/<artifact_id>/versions/<version_id>/zip` | GET | Download version as ZIP | ✓ Complete |
| `/chat/<session_id>/artifacts/active` | POST | Set active version | ✓ Complete |

#### Authentication Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/auth/google` | GET | Google OAuth login |
| `/auth/google/callback` | GET | Google OAuth callback |
| `/auth/microsoft` | GET | Microsoft OAuth login |
| `/auth/microsoft/callback` | GET | Microsoft OAuth callback |
| `/auth/discord` | GET | Discord OAuth login |
| `/auth/discord/callback` | GET | Discord OAuth callback |
| `/auth/logout` | POST | User logout |

---

### Configuration Management

**File:** `app/config.py`

**Key Configuration Areas:**

1. **Security:**
   - `SECRET_KEY` - Flask session secret
   - `GROQ_API_KEY` - Groq AI API key
   - `OPENROUTER_API_KEY` - OpenRouter API key

2. **OAuth Credentials:**
   - Google: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
   - Microsoft: `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`
   - Discord: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`

3. **Firebase:**
   - `FIREBASE_CRED_PATH` - Service account JSON path
   - `FIREBASE_STORAGE_BUCKET` - Storage bucket name

4. **Search Providers:**
   - Brave, Google, Bing API keys and endpoints
   - `SEARCH_BLOCKED_DOMAINS`

5. **File Handling:**
   - `UPLOAD_FOLDER` - File upload directory
   - `MAX_CONTENT_LENGTH` - 16MB limit
   - `TESSERACT_CMD`, `TESSERACT_LANG` - OCR configuration

6. **Session Management:**
   - `SESSION_TYPE` - filesystem or custom
   - `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`
   - `RATELIMIT_STORAGE_URI`

---

## Frontend Architecture

### Template Engine: Jinja2

**Base Template:** `templates/base.html` - Provides common structure

**Main Chat Interface:** `templates/index.html` (636 lines)

**Key Sections:**
1. **Head:** Meta tags, CDN libraries (Marked.js, DOMPurify, KaTeX, Prism.js, CodeMirror)
2. **Left Sidebar:** Logo, new chat button, session list, profile footer
3. **Content Wrapper:** Chat stage with header, messages, and composer
4. **Right Sidebar:** Search results and additional tools
5. **Artifact Panel:** Resizable drawer for code projects ✓ IMPLEMENTED
6. **Modals:** History search, image gallery, image preview

---

### JavaScript Architecture

**Pattern:** Vanilla JavaScript with module pattern (IIFEs)

**State Management:** Global state objects and closures

**Key Modules:**

1. **artifactPanel.js** (788 lines) ✓ IMPLEMENTED
   - `ArtifactPanel` class manages artifact UI
   - Handles version switching, preview rendering, file tree
   - Implements three renderers: HTML, Markdown, Plain text
   - Draft caching system for offline artifacts
   - Resizable panel with localStorage persistence

2. **codeWindow.js** (60KB)
   - Code block rendering and syntax highlighting
   - Integration with Prism.js
   - Copy-to-clipboard functionality

3. **startNewChatModule.js** (149KB)
   - Chat session management
   - Message rendering pipeline
   - Real-time message streaming
   - Attachment handling
   - Artifact integration hooks

4. **script.js**
   - Main application orchestrator
   - CSRF token management
   - Scroll behavior
   - Attachment store (manages pending files)

5. **sidebarHandler.js**
   - Left sidebar interactions
   - Session list rendering
   - Search functionality

6. **typeTextWorker.js**
   - Text streaming effects
   - Character-by-character display

7. **speechSynthesisModule.js**
   - Text-to-speech integration
   - Voice selection and control

---

## Data Flow Architecture

### Message Lifecycle

1. **User Input:**
   ```
   User types message → Composer → attachmentStore.getAll()
   → CSRF token added → POST /chat/<session>/messages
   ```

2. **Server Processing:**
   ```
   Route handler → Validate CSRF → Check auth
   → Extract attachments → Build prompt
   → send_prompt() to AI model
   → Parse response with ArtifactRouter
   → Persist to Firestore
   → Return JSON response
   ```

3. **Client Rendering:**
   ```
   Response received → Parse JSON
   → Render user message bubble
   → Stream AI response with typeTextWorker
   → Detect artifact payload
   → artifactPanel.attachPreview()
   → Render artifact pill/button
   ```

4. **Artifact Opening:**
   ```
   User clicks artifact pill
   → artifactPanel.openArtifact()
   → Check draft cache
   → Fetch from server if needed
   → Render preview/code/info tabs
   → Update panel header
   → Add to version cache
   ```

---

## Existing Patterns and Conventions

### Python Code Style

- **Type Hints:** Extensive use of typing (Dict, List, Optional, Tuple, Any)
- **Docstrings:** Comprehensive function documentation
- **Error Handling:** Try-catch blocks with logging
- **Logging:** Structured logging with logger.info/warning/error
- **Naming:** snake_case for functions/variables

### JavaScript Code Style

- **Module Pattern:** IIFEs for encapsulation
- **Class-Based:** ES6 classes for complex components
- **Naming:** camelCase for functions/variables
- **DOM Queries:** getElementById, querySelector
- **Event Handling:** addEventListener pattern
- **Comments:** Inline comments for complex logic

### CSS Architecture

- **BEM-like Naming:** `.artifact-panel__header`, `.artifact-panel__toggle-btn`
- **CSS Variables:** `--artifact-panel-width`, `--artifact-panel-offset`
- **Modifiers:** `.is-active`, `.is-pending`, `.has-attachments`
- **Responsive:** Media queries for mobile/tablet/desktop
- **Dark Theme:** Primary design is dark mode
- **Glassmorphism:** Backdrop filters and transparency

### Security Patterns

1. **CSRF Protection:** All forms include `csrf_token()`
2. **Input Sanitization:** DOMPurify for HTML content
3. **Iframe Sandboxing:** `sandbox="allow-scripts"` attribute
4. **Content Security Policy:** Strict CSP headers
5. **Path Validation:** No path traversal allowed
6. **File Type Validation:** MIME type checking
7. **Authentication Required:** `@login_required` decorator

---

## Integration Points for Artifacts

### Backend Integration Points ✓ IMPLEMENTED

1. **Message Processing (views.py:~2900)**
   ```python
   routing_decision = artifact_router.route(structured, user_prompt, provider_flagged_code)

   if routing_decision.artifact_payload:
       persisted_artifact = artifact_store.persist_decision(...)
       artifact_reference = build_artifact_reference_payload(...)
   ```

2. **Artifact Storage Initialization (views.py:572-573)**
   ```python
   artifact_router = ArtifactRouter()
   artifact_store = ArtifactStore(db)
   ```

3. **Message Persistence with Artifacts (views.py:~2950)**
   ```python
   message_data = {
       'artifact': artifact_payload,
       'artifact_reference': artifact_reference,
       ...
   }
   ```

### Frontend Integration Points ✓ IMPLEMENTED

1. **Panel Initialization (index.html:591)**
   ```javascript
   window.artifactPanel = new ArtifactPanel();
   ```

2. **Message Rendering Hook (startNewChatModule.js)**
   ```javascript
   if (window.artifactPanel && artifact_reference) {
       window.artifactPanel.attachPreview(messageEl, artifact_reference, {
           payload: artifact_payload,
           sessionId: currentSessionId
       });
   }
   ```

3. **Global State Integration (script.js)**
   ```javascript
   attachmentStore - manages file uploads
   scrollState - manages chat scrolling
   conversationStateController - tracks conversation state
   ```

---

## Performance Considerations

### Backend Optimizations

- **Concurrent Futures:** Parallel text extraction from files
- **Firestore Batch Operations:** Artifact file writes batched
- **LRU Caching:** `@lru_cache` for markdown conversion
- **Connection Pooling:** Firestore client reuse
- **Rate Limiting:** Per-endpoint rate limits

### Frontend Optimizations

- **Lazy Loading:** Artifact versions fetched on-demand
- **LocalStorage Caching:** Panel width, draft artifacts
- **Version Cache:** In-memory Map for version data
- **Debounced Scrolling:** Scroll event throttling
- **Syntax Highlighting:** Only on visible code blocks

---

## Technology Stack Summary

### Backend
- **Framework:** Flask 2.x
- **Database:** Google Cloud Firestore
- **Authentication:** Firebase Auth, OAuth 2.0
- **AI Models:** Groq API, OpenRouter API
- **Text Processing:** NLTK, pytesseract, PyPDF2
- **Image Processing:** Pillow, EasyOCR (optional)
- **Web Server:** Gunicorn (production)

### Frontend
- **Template Engine:** Jinja2
- **JavaScript:** Vanilla ES6+
- **Markdown:** Marked.js
- **Sanitization:** DOMPurify
- **Math Rendering:** KaTeX
- **Syntax Highlighting:** Prism.js
- **Code Editor:** CodeMirror
- **Icons:** Font Awesome 6

### Infrastructure
- **Session Storage:** Filesystem or Redis
- **File Storage:** Local filesystem + Firebase Storage
- **Logging:** Python logging + ConcurrentRotatingFileHandler
- **CORS:** Flask-CORS
- **CSRF:** Flask-WTF

---

## Key Architectural Decisions

1. **Server-Side Rendering + Progressive Enhancement:**
   - Initial page load via Jinja2
   - JavaScript enhances with SPA-like behavior
   - Maintains SEO and accessibility

2. **Firestore as Primary Database:**
   - NoSQL document structure
   - Real-time capabilities
   - Scalable and serverless

3. **Blueprint-Based Routing:**
   - Separation of concerns (app_routes, auth_routes)
   - Modular and maintainable

4. **Artifact-First Design:**
   - Multi-file projects as first-class citizens
   - Version control built-in
   - Language-agnostic rendering

5. **Security-First Approach:**
   - Multiple layers of protection
   - Input validation at every level
   - Sandboxed execution environments

---

## Next Steps for Enhancement

See INTEGRATION_POINTS.md for detailed enhancement opportunities.

---

**Document Version:** 1.0
**Last Updated:** 2025-12-29
**Maintainer:** Claude Code Assistant
