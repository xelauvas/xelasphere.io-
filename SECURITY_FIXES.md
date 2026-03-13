# Security Vulnerabilities Fixed

This document tracks security vulnerabilities identified and fixed in the XelaSphere application.

---

## 1. Removed `unsafe-eval` from CSP

**File:** `app/__init__.py`
**Severity:** Critical
**Issue:** `unsafe-eval` in Content Security Policy allowed XSS payload execution.

**Fix:** Removed `'unsafe-eval'` from the `script-src` directive.

```python
# Before
"script-src 'self' ... 'unsafe-inline' 'unsafe-eval'"

# After
"script-src 'self' ... 'unsafe-inline'"
```

---

## 2. XSS via `|safe` Filter

**Files:** `app/__init__.py`, templates
**Severity:** High
**Issue:** Using `{{ message|safe }}` in templates rendered unescaped user input.

**Fix:** Created sanitization filters using `bleach` library:

- `sanitize_html` - For general HTML content with allowed tags whitelist
- `sanitize_flash` - Restricted filter for flash messages

```python
@app.template_filter('sanitize_html')
def sanitize_html_filter(text):
    cleaned = bleach.clean(str(text), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, ...)
    return Markup(cleaned)
```

Updated templates to use `{{ message|sanitize_flash }}` instead of `{{ message|safe }}`.

---

## 3. innerHTML Without Sanitization

**Files:** `artifactPanel.js`, `typeTextWorker.js`, `startNewChatModule.js`
**Severity:** High
**Issue:** DOM-based XSS through unsanitized innerHTML assignments.

**Fix:**

- **artifactPanel.js:** Added `escapeHtml()` calls for `node.name` and `file.path`
- **typeTextWorker.js:** Added DOMPurify sanitization at constructor level
- **startNewChatModule.js:** Already using `escapeHtml()` properly

```javascript
// Before
el.innerHTML = `<span>${node.name}</span>`;

// After
el.innerHTML = `<span>${escapeHtml(node.name)}</span>`;
```

---

## 4. Markup() with User Input

**File:** `app/auth.py:258, 263`
**Severity:** Medium
**Issue:** Flash message XSS via f-strings inside `Markup()`.

**Fix:** Changed to `Markup().format()` which auto-escapes interpolated values.

```python
# Before
flash(Markup(f"Invalid credentials.<a href='{reset_url}'>Forgot password?</a>"), 'danger')

# After
flash(Markup("Invalid credentials. <a href='{}'>Forgot password?</a>").format(reset_url), 'danger')
```

---

## 5. Missing CSRF Protection

**File:** `app/views.py` - `/more-options` endpoint
**Severity:** High
**Issue:** POST endpoint lacked CSRF token validation.

**Fix:**

1. Added `validate_csrf` import from `flask_wtf.csrf`
2. Created `@csrf_required` decorator for explicit validation
3. Applied decorator to `/more-options` endpoint

```python
def csrf_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        csrf_token = request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token')
        if not csrf_token:
            return jsonify({'error': 'CSRF token missing.'}), 403
        try:
            validate_csrf(csrf_token)
        except Exception as e:
            return jsonify({'error': 'CSRF token invalid.'}), 403
        return f(*args, **kwargs)
    return decorated_function
```

---

## 6. Plaintext API Keys in Firestore

**Files:** `app/views.py`, `app/encryption.py` (new)
**Severity:** High
**Issue:** User API keys stored unencrypted in Firestore database.

**Fix:**

1. Created `app/encryption.py` with Fernet symmetric encryption
2. Keys encrypted before storage, decrypted on retrieval
3. Added `API_ENCRYPTION_KEY` to `.env`
4. Backwards compatible with existing plaintext keys

```python
# Storing
updated_keys['groq'] = encrypt_api_key(groq_key)

# Retrieving
if is_encrypted(key_value):
    user_api_keys[key_name] = decrypt_api_key(key_value)
```

**Environment Variable:**
```
API_ENCRYPTION_KEY=<generated-fernet-key>
```

---

## 7. Share Token Enumeration

**File:** `app/views.py` - `/shared/<share_token>` endpoint
**Severity:** Medium
**Issue:** Brute-force access to shared artifacts possible.

**Fix:**

1. Added rate limiting: `@limiter.limit("30 per minute")`
2. Token format validation before database query
3. Failed attempt logging with IP address
4. Upgraded to cryptographically secure tokens using `secrets.token_urlsafe(32)` (256 bits)

```python
@app_routes.route('/shared/<share_token>', methods=['GET'])
@limiter.limit("30 per minute")
@error_handler
def view_shared_artifact(share_token: str):
    # Validate token format
    if not (uuid_pattern.match(share_token) or urlsafe_pattern.match(share_token)):
        logger.warning(f"Invalid share token format attempted: {share_token[:20]}...")
        return "Share link not found or has expired.", 404
```

---

## 8. Markdown Without Sanitization

**File:** `app/utilities.py:179`
**Severity:** Medium
**Issue:** HTML injection via markdown conversion.

**Fix:** Added `bleach.clean()` sanitization after markdown conversion.

```python
def convert_markdown_to_html(text):
    html_output = markdown.markdown(text)
    sanitized = bleach.clean(
        html_output,
        tags=MARKDOWN_ALLOWED_TAGS,
        attributes=MARKDOWN_ALLOWED_ATTRIBUTES,
        protocols=MARKDOWN_ALLOWED_PROTOCOLS,
        strip=True
    )
    return sanitized
```

---

## 9. Missing File Magic Validation

**File:** `app/views.py` - `FileValidator`, `get_file_type()`
**Severity:** High
**Issue:** Upload bypass by spoofing file extensions.

**Fix:**

1. Enhanced magic byte detection with ordered signatures
2. Added `BLOCKED_EXTENSIONS` set (30+ dangerous types)
3. Validates extension matches detected MIME type
4. Checks for dangerous patterns in "text" files

```python
BLOCKED_EXTENSIONS = {
    '.exe', '.dll', '.bat', '.cmd', '.sh', '.ps1', '.vbs', '.js', '.jar',
    '.php', '.asp', '.aspx', '.jsp', '.cgi', '.py', '.rb', '.pl',
    '.html', '.htm', '.svg', '.xml', '.xhtml', ...
}

# Validates:
# 1. Extension not in blocked list
# 2. Magic bytes match claimed type
# 3. Extension matches detected MIME type
```

---

## 10. No Prompt Length Validation

**File:** `app/views.py` - `/chat` endpoint
**Severity:** Medium
**Issue:** Resource exhaustion via extremely long prompts.

**Fix:** Added length validation for user prompts and extracted text.

```python
MAX_PROMPT_LENGTH = 50000  # ~12,500 tokens
MAX_EXTRACTED_TEXT_LENGTH = 100000

if len(user_prompt) > MAX_PROMPT_LENGTH:
    return jsonify({'error': f'Prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters.'}), 400
```

---

## Dependencies Added

```
# requirements-cpu.txt & requirements-gpu.txt
bleach==6.1.0
cryptography==44.0.0
```

---

## Environment Variables Added

```bash
# .env
API_ENCRYPTION_KEY=<fernet-key>  # For encrypting user API keys
```

---

## Summary

| Vulnerability | Severity | Status |
|--------------|----------|--------|
| unsafe-eval in CSP | Critical | Fixed |
| XSS via \|safe filter | High | Fixed |
| innerHTML without sanitization | High | Fixed |
| Markup() with user input | Medium | Fixed |
| Missing CSRF protection | High | Fixed |
| Plaintext API keys | High | Fixed |
| Share token enumeration | Medium | Fixed |
| Markdown without sanitization | Medium | Fixed |
| Missing file magic validation | High | Fixed |
| No prompt length validation | Medium | Fixed |
