# Dependencies Documentation

Generated: 2025-12-29

## Overview

This document catalogs all Python and JavaScript dependencies used in XelaSphere, including their purposes, versions, licenses, and security considerations.

---

## Python Dependencies

**Source:** `requirements-cpu.txt`

### Web Framework & Extensions

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| Flask | 3.1.0 | Core web framework | BSD-3-Clause |
| flask-session | 0.8.0 | Server-side session management | BSD-3-Clause |
| flask-wtf | 1.2.2 | CSRF protection, form handling | BSD-3-Clause |
| flask-cors | 5.0.1 | Cross-origin resource sharing | MIT |
| flask-limiter | 3.8.0 | Rate limiting | MIT |
| gunicorn | 23.0.0 | WSGI HTTP server (production) | MIT |

**Critical for Artifacts:**
- Flask: Core routing and request handling
- flask-wtf: CSRF protection for artifact API endpoints
- flask-cors: Allow frontend to communicate with backend

---

### Firebase & Google Cloud

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| firebase-admin | 6.6.0 | Firebase Admin SDK (Auth, Firestore, Storage) | Apache-2.0 |
| google-cloud-firestore | 2.20.0 | Firestore client library | Apache-2.0 |

**Critical for Artifacts:**
- firebase-admin: Authentication and SDK initialization
- google-cloud-firestore: Artifact storage, versioning, file persistence

**Configuration Requirements:**
- Service account JSON file (`FIREBASE_CRED_PATH`)
- Storage bucket (`FIREBASE_STORAGE_BUCKET`)

---

### HTTP & Networking

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| requests | 2.32.3 | Synchronous HTTP client | Apache-2.0 |
| aiohttp | 3.11.13 | Asynchronous HTTP client | Apache-2.0 |

**Usage:**
- requests: AI API calls (Groq, OpenRouter), web search
- aiohttp: Async operations, parallel requests

---

### Utilities & Core Libraries

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| python-dotenv | 1.0.1 | Environment variable management | BSD-3-Clause |
| cachetools | 5.3.3 | Caching utilities (LRU, TTL) | MIT |
| prometheus_client | 0.21.1 | Metrics collection (optional) | Apache-2.0 |
| concurrent-log-handler | 0.9.25 | Thread-safe rotating file logger | Apache-2.0 |

**Critical for Artifacts:**
- cachetools: Version caching, artifact metadata caching

---

### Data Validation & Processing

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| pydantic | 2.10.6 | Data validation, settings management | MIT |
| tiktoken | 0.9.0 | OpenAI token counting | MIT |
| markdown | 3.7 | Markdown to HTML conversion | BSD-3-Clause |
| tldextract | 5.1.3 | Domain extraction from URLs | BSD-3-Clause |

**Critical for Artifacts:**
- markdown: Render markdown files in artifact preview
- tiktoken: Token counting for prompt optimization

---

### Natural Language Processing

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| nltk | 3.9.1 | Sentiment analysis, tokenization | Apache-2.0 |

**Usage:**
- Sentiment analysis on user messages
- Sentence tokenization
- VADER lexicon for emotion detection

**Required Data:**
- punkt, punkt_tab (tokenizers)
- vader_lexicon (sentiment)

---

### Image & Document Processing

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| pytesseract | 0.3.13 | OCR wrapper for Tesseract | Apache-2.0 |
| pillow | 11.1.0 | Image processing library | HPND |
| PyPDF2 | 3.0.1 | PDF text extraction | BSD-3-Clause |
| easyocr | 1.7.1 | Alternative OCR engine | Apache-2.0 |

**Critical for Artifacts:**
- pillow: Image preprocessing for attachments
- PyPDF2: Extract text from PDF documents

**System Requirements:**
- Tesseract OCR binary (`tesseract` command)
- Language packs (`TESSERACT_LANG` env var)

---

### Machine Learning & AI

| Package | Version | Purpose | License |
|---------|---------|---------|---------|
| numpy | 2.2.3 | Numerical computing | BSD-3-Clause |
| scikit-learn | 1.6.1 | Machine learning utilities | BSD-3-Clause |
| scipy | 1.15.2 | Scientific computing | BSD-3-Clause |
| sentence-transformers | 3.4.1 | Semantic embeddings | Apache-2.0 |
| transformers | 4.49.0 | Hugging Face transformers | Apache-2.0 |
| torch | 2.6.0 | PyTorch (CPU-only) | BSD-3-Clause |

**Usage:**
- sentence-transformers: Semantic search, embeddings
- scikit-learn: Clustering, PCA for analytics
- torch: Model inference (CPU-optimized build)

**Note:** CPU-only PyTorch (`--extra-index-url https://download.pytorch.org/whl/cpu`)

---

## Python Dependency Graph

```
Flask (Core)
├── flask-session
├── flask-wtf
├── flask-cors
└── flask-limiter

Firebase
├── firebase-admin
│   ├── google-cloud-firestore
│   └── google-auth
└── google-cloud-storage (implicit)

AI/ML Stack
├── transformers
│   ├── torch
│   └── sentence-transformers
├── scikit-learn
│   ├── numpy
│   └── scipy
└── nltk

Text Processing
├── markdown
├── tiktoken
└── pydantic

Image/PDF
├── pillow
├── pytesseract (requires tesseract binary)
├── PyPDF2
└── easyocr
    └── torch (shared)

Utilities
├── requests
├── aiohttp
├── python-dotenv
├── cachetools
└── concurrent-log-handler
```

---

## JavaScript Dependencies (CDN)

**Source:** `app/templates/index.html`

### Markdown & Content Processing

| Library | Version | CDN | Purpose | License |
|---------|---------|-----|---------|---------|
| Marked.js | Latest | jsdelivr | Markdown to HTML parsing | MIT |
| DOMPurify | Latest | jsdelivr | HTML sanitization | Apache-2.0/MPL-2.0 |

**Critical for Artifacts:**
- Marked.js: Render markdown artifacts in preview
- DOMPurify: Sanitize AI-generated HTML before display

**Usage:**
```javascript
const htmlContent = marked.parse(markdownText);
const sanitized = DOMPurify.sanitize(htmlContent);
iframe.srcdoc = sanitized;
```

---

### Math Rendering

| Library | Version | CDN | Purpose | License |
|---------|---------|-----|---------|---------|
| KaTeX | 0.16.9 | jsdelivr | LaTeX math rendering | MIT |
| KaTeX Auto-render | 0.16.9 | jsdelivr | Automatic math detection | MIT |

**Usage:** Render mathematical expressions in chat messages and artifacts.

**Delimiters:**
- Inline: `$...$` or `\(...\)`
- Display: `$$...$$` or `\[...\]`

---

### Syntax Highlighting

| Library | Version | CDN | Purpose | License |
|---------|---------|-----|---------|---------|
| Prism.js | 1.25.0 | cdnjs | Syntax highlighting | MIT |
| Prism Autoloader | 1.25.0 | cdnjs | Dynamic language loading | MIT |
| Prism JavaScript | 1.25.0 | cdnjs | JavaScript grammar | MIT |
| Prism CSS | 1.25.0 | cdnjs | CSS grammar | MIT |
| Prism Python | 1.25.0 | cdnjs | Python grammar | MIT |

**Critical for Artifacts:**
- Prism.js: Syntax highlighting in code tab
- Autoloader: Load additional languages on demand

**Supported Languages:** 200+ languages via autoloader

**Integration:**
```javascript
const codeBlock = document.getElementById('artifact-code-block');
codeBlock.className = `language-${file.language}`;
Prism.highlightElement(codeBlock);
```

---

### Code Editor

| Library | Version | CDN | Purpose | License |
|---------|---------|-----|---------|---------|
| CodeMirror | 5.65.5 | cdnjs | Code editor component | MIT |
| CodeMirror JavaScript mode | 5.65.5 | cdnjs | JavaScript editing | MIT |
| CodeMirror Material Dark theme | 5.65.5 | cdnjs | Dark theme | MIT |
| CodeMirror Match Brackets | 5.65.5 | cdnjs | Bracket matching | MIT |
| CodeMirror Close Brackets | 5.65.5 | cdnjs | Auto-close brackets | MIT |

**Usage:** Embedded code editor for advanced editing (optional feature).

---

### Icons & Fonts

| Library | Version | CDN | Purpose | License |
|---------|---------|-----|---------|---------|
| Font Awesome | 6.1.1 | cdnjs | Icon library | Font Awesome Free License |
| Google Fonts (Roboto Mono) | Latest | fonts.googleapis.com | Monospace font | Apache-2.0 |
| Google Fonts (Manrope) | Latest | fonts.googleapis.com | Sans-serif font | OFL |

**Font Usage:**
- Roboto Mono: Code blocks, inline code
- Manrope: UI text, headings

---

## Local JavaScript Modules

**Location:** `app/static/js/`

| Module | Size | Purpose | Dependencies |
|--------|------|---------|--------------|
| script.js | ~25KB | Main application logic | - |
| artifactPanel.js | 35KB | Artifact UI management | Marked.js, DOMPurify, Prism.js |
| codeWindow.js | 60KB | Code rendering and detection | Prism.js |
| startNewChatModule.js | 149KB | Chat session management | All above |
| sidebarHandler.js | 8KB | Sidebar interactions | - |
| speechSynthesisModule.js | 28KB | Text-to-speech | Web Speech API |
| modelSelect.js | 9KB | Model selection UI | - |
| scrollState.js | 8KB | Scroll behavior management | - |
| detectorCode.js | 30KB | Code detection utilities | - |
| fetchMoreOptions.js | 11KB | Options menu | - |

**Total:** ~378KB (uncompressed)

---

## CSS Dependencies

**Location:** `app/static/css/`

| Stylesheet | Size | Purpose |
|------------|------|---------|
| styles.css | ~50KB | Main application styles |
| artifact-panel.css | 12KB | Artifact panel styles |
| code-window.css | 8KB | Code block styles |
| message-styles.css | 10KB | Message bubble styles |
| prism.css | 5KB | Syntax highlighting theme |
| dashboard.css | 6KB | Dashboard page |
| login.css | 4KB | Login page |
| register.css | 4KB | Registration page |

**Total:** ~99KB (uncompressed)

---

## Production Optimization

### Python

**Optimization Strategies:**
1. **Virtual Environment:** Isolated dependencies
2. **CPU-Only PyTorch:** Reduced install size (~2GB → ~200MB)
3. **Lazy Imports:** Import heavy libraries only when needed
4. **Connection Pooling:** Reuse Firestore client

**Production Checklist:**
```bash
pip install -r requirements-cpu.txt
python -m nltk.downloader punkt punkt_tab vader_lexicon
```

### JavaScript

**Optimization Strategies:**
1. **CDN Loading:** Leverage browser caching
2. **Deferred Loading:** Scripts load after page render
3. **Module Pattern:** Reduce global scope pollution
4. **Lazy Syntax Highlighting:** Only highlight visible code

**Future Improvements:**
- Bundle and minify local JS modules
- Use Webpack or Rollup for tree-shaking
- Implement code splitting

---

## Security Considerations

### Python Dependencies

**Known Vulnerabilities:**
- Regularly audit with `pip-audit` or `safety`
- Monitor CVE databases for Firebase, Flask, PyTorch

**Update Strategy:**
```bash
# Check for updates
pip list --outdated

# Security audit
pip install pip-audit
pip-audit
```

**Critical Security Packages:**
- flask-wtf: CSRF protection (keep updated)
- requests: HTTP client (verify SSL)
- pillow: Image processing (CVE history)

### JavaScript Dependencies

**CDN Risks:**
- Reliance on third-party CDNs (jsdelivr, cdnjs)
- Mitigations: Subresource Integrity (SRI) hashes, fallback to local

**Recommended SRI Implementation:**
```html
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"
        integrity="sha384-..."
        crossorigin="anonymous"></script>
```

**Content Security Policy:**
- Restrict script sources to trusted CDNs
- Use `nonce` or `hash` for inline scripts

---

## Dependency Licensing Summary

### Permissive Licenses (OK for Commercial Use)
- MIT: Flask, requests, Prism.js, Marked.js, KaTeX
- BSD-3-Clause: NumPy, PyTorch, NLTK, pillow
- Apache-2.0: Firebase, transformers, DOMPurify

### Copyleft Licenses (Requires Attribution)
- None (all dependencies use permissive licenses)

### License Compliance Checklist
- [ ] Include LICENSE file in distribution
- [ ] Attribute third-party libraries in About page
- [ ] Comply with Apache-2.0 NOTICE requirements
- [ ] Include Font Awesome attribution (if using icons)

---

## Future Dependency Additions

### Planned Additions

| Package | Purpose | Status |
|---------|---------|--------|
| redis | Session storage backend | Planned |
| celery | Background task queue | Planned |
| websockets | Real-time collaboration | Planned |
| beautifulsoup4 | Web scraping for search | Planned |
| langchain | Advanced AI orchestration | Considering |

### Artifact-Specific Enhancements

| Package | Purpose | Priority |
|---------|---------|----------|
| mermaid.js | Diagram rendering in artifacts | High |
| react | React component rendering | Medium |
| @babel/standalone | JSX transpilation | Medium |
| monaco-editor | Advanced code editor | Low |

---

## Version Pinning Strategy

### Current Approach
- **Major.Minor.Patch** pinning (e.g., `Flask==3.1.0`)
- Benefits: Reproducible builds, stability
- Drawbacks: Miss security patches

### Recommended Approach
- **Major.Minor** pinning with `~=` operator
- Example: `Flask~=3.1.0` (allows 3.1.1, 3.1.2, but not 3.2.0)
- Balances stability and security updates

---

## Dependency Installation Guide

### Development Environment

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements-cpu.txt

# Download NLTK data
python -c "import nltk; nltk.download(['punkt', 'punkt_tab', 'vader_lexicon'])"

# Install Tesseract (Ubuntu/Debian)
sudo apt-get install tesseract-ocr tesseract-ocr-eng

# Install Tesseract (macOS)
brew install tesseract

# Verify installation
python -c "import flask, firebase_admin, torch; print('OK')"
```

### Production Environment

```bash
# Use production-optimized requirements
pip install -r requirements-cpu.txt --no-cache-dir

# Set environment variables
export FLASK_ENV=production
export FIREBASE_CRED_PATH=/path/to/service-account.json
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

# Run with gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 run:app
```

---

## Monitoring Dependency Health

### Automated Checks

1. **Dependabot (GitHub):**
   - Automated pull requests for dependency updates
   - Security vulnerability alerts

2. **PyUp.io:**
   - Python security monitoring
   - Automated updates

3. **Snyk:**
   - Cross-language vulnerability scanning
   - CI/CD integration

### Manual Audits

**Quarterly Review Checklist:**
- [ ] Update all dependencies to latest stable versions
- [ ] Run security audit (`pip-audit`, `npm audit`)
- [ ] Test artifact functionality after updates
- [ ] Review changelogs for breaking changes
- [ ] Update documentation

---

## Troubleshooting Common Issues

### PyTorch Installation Fails

**Problem:** Default PyTorch is CUDA-enabled (large download)

**Solution:**
```bash
pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
```

### Tesseract Not Found

**Problem:** `pytesseract.TesseractNotFoundError`

**Solution:**
```bash
# Ubuntu/Debian
sudo apt-get install tesseract-ocr

# macOS
brew install tesseract

# Set path in config
export TESSERACT_CMD=/usr/bin/tesseract
```

### Firebase Admin SDK Errors

**Problem:** `ValueError: The default Firebase app does not exist`

**Solution:**
- Verify `FIREBASE_CRED_PATH` points to valid JSON
- Check service account has Firestore permissions
- Ensure Firebase project is initialized

---

**Document Version:** 1.0
**Last Updated:** 2025-12-29
**Maintainer:** Claude Code Assistant
