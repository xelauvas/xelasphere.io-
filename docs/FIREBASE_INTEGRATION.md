# Firebase Integration Documentation

Generated: 2025-12-29

## Overview

XelaSphere uses Google Cloud Firestore as its primary database and Firebase Authentication for user management. The integration is comprehensive, covering user data, chat sessions, messages, artifacts, and file metadata.

---

## Firebase Admin SDK Initialization

**File:** `app/config.py` (lines 98-118)

```python
# Service account credentials
cred = credentials.Certificate(Config.FIREBASE_CRED_PATH)

# Initialize Firebase Admin SDK
firebase_options = {}
if Config.FIREBASE_STORAGE_BUCKET:
    firebase_options['storageBucket'] = Config.FIREBASE_STORAGE_BUCKET

if not firebase_admin._apps:
    if firebase_options:
        firebase_admin.initialize_app(cred, firebase_options)
    else:
        firebase_admin.initialize_app(cred)

# Shared Firestore client
db = firestore.client()
```

**Environment Variables Required:**
- `FIREBASE_CRED_PATH` - Path to service account JSON file
- `FIREBASE_STORAGE_BUCKET` - Optional storage bucket name

---

## Firestore Data Models

### 1. User Collection

**Path:** `/users/{userId}`

```javascript
{
  "uid": string,              // Firebase Auth UID
  "email": string,            // User email
  "name": string,             // Display name
  "picture": string,          // Profile picture URL (optional)
  "provider": string,         // "google" | "microsoft" | "discord"
  "created_at": timestamp,    // Account creation time
  "last_login": timestamp,    // Last login time
  "preferences": {            // User preferences (optional)
    "theme": string,
    "notifications": boolean,
    // ... other preferences
  }
}
```

**Access Patterns:**
- Read/Write: Authenticated user with matching UID

---

### 2. Chat Sessions Collection

**Path:** `/users/{userId}/chat_sessions/{sessionId}`

```javascript
{
  "session_id": string,           // Unique session identifier
  "user_id": string,              // Owner's UID
  "title": string,                // Session title (auto-generated or custom)
  "created_at": string,           // ISO 8601 timestamp
  "last_updated": timestamp,      // Firestore server timestamp
  "model": string,                // AI model used (e.g., "llama-3.1-70b-versatile")
  "message_count": number,        // Total messages in session
  "has_files": boolean,           // Whether session has file attachments
  "files": array,                 // File metadata (optional)
  "is_temp": boolean,             // Temporary/incognito session flag (optional)
  "deleted_at": timestamp,        // Soft delete timestamp (optional)

  // Extended metadata
  "summary": string,              // Session summary (optional)
  "tags": array,                  // User-defined tags (optional)
  "pinned": boolean               // Pin to top of sidebar (optional)
}
```

**File Metadata Structure:**
```javascript
{
  "filename": string,             // Sanitized filename
  "original_filename": string,    // User's original filename
  "upload_date": string,          // ISO 8601 timestamp
  "file_size": number,            // Bytes
  "mime_type": string,            // MIME type
  "md5_hash": string,             // MD5 checksum
  "extracted_text": string,       // OCR/PDF text extraction result
  "ocr_error": string,            // Error message if extraction failed
  "ocr_engine": string            // "tesseract" | "easyocr" | "pypdf2"
}
```

**Indexing:**
- `created_at` (descending) - For chronological session lists
- `last_updated` (descending) - For "recent" sorting
- `user_id` - Implicit index for user-scoped queries

---

### 3. Messages Subcollection

**Path:** `/users/{userId}/chat_sessions/{sessionId}/messages/{messageId}`

```javascript
{
  "message_id": string,           // Unique message identifier
  "role": string,                 // "user" | "assistant" | "system"
  "content": string,              // Message text content
  "timestamp": timestamp,         // Firestore server timestamp
  "model": string,                // AI model used for assistant messages

  // Rich content
  "formatted_content": string,    // HTML-formatted content (optional)
  "contains_code": boolean,       // Code detection flag

  // Attachments
  "attachments": array,           // File attachments (optional)

  // Artifact integration ✓ IMPLEMENTED
  "artifact": object,             // Full artifact payload (draft or persisted)
  "artifact_reference": {         // Lightweight artifact reference
    "artifactId": string,
    "versionId": string,
    "versionNumber": number,
    "title": string,
    "primaryLanguage": string,
    "entrypoint": string,
    "summary": string,
    "pending": boolean            // True for offline/draft artifacts
  },

  // Metadata
  "tokens_used": number,          // Token count (optional)
  "latency_ms": number,           // Response time (optional)
  "error": string,                // Error message if message failed (optional)
  "edited_at": timestamp,         // Last edit timestamp (optional)
  "deleted": boolean              // Soft delete flag (optional)
}
```

**Attachment Structure:**
```javascript
{
  "id": string,                   // Unique attachment ID
  "name": string,                 // Filename
  "type": string,                 // MIME type
  "isImage": boolean,             // Image flag
  "dataUrl": string               // Base64 data URL (for images)
}
```

**Indexing:**
- `timestamp` (ascending) - For message ordering
- Composite: `(role, timestamp)` - For filtering by role

---

### 4. Artifacts Collection ✓ IMPLEMENTED

**Path:** `/users/{userId}/chat_sessions/{sessionId}/artifacts/{artifactId}`

```javascript
{
  "artifactId": string,           // Unique artifact identifier
  "conversationId": string,       // Parent session ID
  "title": string,                // Artifact title
  "type": string,                 // "project" | "code" | "document"
  "entrypointPath": string,       // Primary file path
  "meta": {                       // Custom metadata
    "runtime": string,            // e.g., "python", "node"
    "run": string,                // Execution command
    // ... other meta fields
  },

  "createdAt": timestamp,         // Creation time
  "updatedAt": timestamp,         // Last modification time
  "versionCount": number,         // Total versions
  "activeVersionId": string,      // Currently active version
  "createdBy": string,            // "assistant" | "user"
  "isActive": boolean             // Active artifact flag
}
```

**Firestore Rules:**
```javascript
match /artifacts/{artifactId} {
  allow read, update, delete: if canAccessArtifact(userId) && validArtifactType(request.resource.data);
  allow create: if canAccessArtifact(userId) && validArtifactType(request.resource.data);
}
```

---

### 5. Artifact Versions Subcollection ✓ IMPLEMENTED

**Path:** `/users/{userId}/chat_sessions/{sessionId}/artifacts/{artifactId}/versions/{versionId}`

```javascript
{
  "versionId": string,            // Unique version identifier
  "versionNumber": number,        // Incrementing version number (1, 2, 3...)
  "entrypointPath": string,       // Primary file for this version
  "primaryLanguage": string,      // Detected language
  "summary": string,              // Short description (≤400 chars)
  "fileCount": number,            // Number of files in version
  "totalBytes": number,           // Total size in bytes
  "sourceMessageId": string,      // Message that created this version
  "createdAt": timestamp          // Version creation time
}
```

**Version Constraints:**
- Max 40 files per version
- Max 200KB per file
- Max 800KB total per version
- Text files only (binary files rejected)

---

### 6. Version Files Subcollection ✓ IMPLEMENTED

**Path:** `/users/{userId}/chat_sessions/{sessionId}/artifacts/{artifactId}/versions/{versionId}/files/{fileDocId}`

```javascript
{
  "path": string,                 // Relative file path (e.g., "src/main.py")
  "language": string,             // Programming language
  "content": string,              // Full file content (UTF-8)
  "mime": string,                 // MIME type (optional)
  "isBinary": boolean,            // Always false (binary not supported)
  "byteSize": number              // File size in bytes
}
```

**Path Validation:**
- Must be relative (no leading `/`)
- No parent directory traversal (`..`)
- Unique within version
- Normalized as POSIX paths

**Language Detection:**
- Auto-detected from file extension
- Supported: python, javascript, typescript, html, css, json, yaml, markdown, etc.
- Fallback: "code"

---

## Firestore Operations

### Read Operations

#### Get User Sessions
```python
user_ref = db.collection('users').document(user_id)
sessions_ref = user_ref.collection('chat_sessions')

# Get all sessions (ordered by creation date)
sessions = sessions_ref.order_by('created_at', direction=firestore.Query.DESCENDING).stream()

# Get paginated sessions
sessions = sessions_ref.order_by('created_at', direction=firestore.Query.DESCENDING).limit(20).stream()
```

#### Get Session Messages
```python
session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
messages_ref = session_ref.collection('messages')

# Get all messages (chronological order)
messages = messages_ref.order_by('timestamp', direction=firestore.Query.ASCENDING).stream()

# Paginated messages (for infinite scroll)
last_doc = None
page_size = 50
messages = messages_ref.order_by('timestamp').start_after(last_doc).limit(page_size).stream()
```

#### Get Artifacts ✓ IMPLEMENTED
```python
artifacts_ref = session_ref.collection('artifacts')

# List all artifacts for a session
artifacts = artifacts_ref.order_by('updatedAt', direction=firestore.Query.DESCENDING).stream()

# Get specific artifact with versions
artifact_ref = artifacts_ref.document(artifact_id)
artifact_doc = artifact_ref.get()

# Get all versions
versions_ref = artifact_ref.collection('versions')
versions = versions_ref.order_by('versionNumber', direction=firestore.Query.DESCENDING).stream()

# Get specific version with files
version_ref = versions_ref.document(version_id)
files_ref = version_ref.collection('files')
files = files_ref.order_by('path').stream()
```

---

### Write Operations

#### Create Session
```python
session_ref = db.collection('users').document(user_id).collection('chat_sessions').document()
session_id = session_ref.id

session_data = {
    'session_id': session_id,
    'user_id': user_id,
    'title': title,
    'created_at': datetime.now().isoformat(),
    'last_updated': firestore.SERVER_TIMESTAMP,
    'model': model_name,
    'message_count': 0
}

session_ref.set(session_data)
```

#### Add Message
```python
message_ref = session_ref.collection('messages').document()
message_id = message_ref.id

message_data = {
    'message_id': message_id,
    'role': 'user',
    'content': content,
    'timestamp': firestore.SERVER_TIMESTAMP,
    'attachments': attachments
}

message_ref.set(message_data)

# Update session message count
session_ref.update({
    'message_count': firestore.Increment(1),
    'last_updated': firestore.SERVER_TIMESTAMP
})
```

#### Create Artifact ✓ IMPLEMENTED
```python
from firebase_admin import firestore as admin_firestore

artifact_id = f"art_{uuid.uuid4().hex}"
version_id = f"ver_{uuid.uuid4().hex}"

artifact_ref = session_ref.collection('artifacts').document(artifact_id)

artifact_doc = {
    'artifactId': artifact_id,
    'conversationId': session_id,
    'title': payload['title'],
    'entrypointPath': payload['entrypoint'],
    'type': 'project',
    'meta': payload.get('meta', {}),
    'createdAt': firestore.SERVER_TIMESTAMP,
    'updatedAt': firestore.SERVER_TIMESTAMP,
    'versionCount': 0,
    'activeVersionId': version_id,
    'createdBy': 'assistant',
    'isActive': True
}

version_doc = {
    'versionId': version_id,
    'versionNumber': 1,
    'entrypointPath': payload['entrypoint'],
    'primaryLanguage': payload['primaryLanguage'],
    'summary': payload['summary'],
    'fileCount': len(payload['files']),
    'totalBytes': payload['totalBytes'],
    'sourceMessageId': message_id,
    'createdAt': firestore.SERVER_TIMESTAMP
}

# Transactional write
@admin_firestore.transactional
def create_artifact_txn(transaction):
    transaction.set(artifact_ref, artifact_doc)
    transaction.set(artifact_ref.collection('versions').document(version_id), version_doc)

create_artifact_txn(db.transaction())

# Write files in batch
batch = db.batch()
for file in payload['files']:
    file_ref = artifact_ref.collection('versions').document(version_id).collection('files').document()
    batch.set(file_ref, {
        'path': file['path'],
        'language': file['language'],
        'content': file['content'],
        'mime': file.get('mime'),
        'isBinary': False,
        'byteSize': file['byteSize']
    })
batch.commit()
```

#### Update Artifact (New Version) ✓ IMPLEMENTED
```python
version_id = f"ver_{uuid.uuid4().hex}"

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
        'activeVersionId': version_id,
        'isActive': True
    }

    version_doc = {
        'versionId': version_id,
        'versionNumber': next_version_number,
        'entrypointPath': payload['entrypoint'],
        'primaryLanguage': payload['primaryLanguage'],
        'summary': payload['summary'],
        'fileCount': len(payload['files']),
        'totalBytes': payload['totalBytes'],
        'sourceMessageId': message_id,
        'createdAt': firestore.SERVER_TIMESTAMP
    }

    transaction.update(artifact_ref, updates)
    transaction.set(artifact_ref.collection('versions').document(version_id), version_doc)

append_version_txn(db.transaction())
# Then write files in batch (same as create)
```

---

### Delete Operations

#### Soft Delete Session
```python
session_ref.update({
    'deleted_at': firestore.SERVER_TIMESTAMP,
    'deleted': True
})
```

#### Hard Delete Session
```python
# Delete all messages
messages = session_ref.collection('messages').stream()
for msg in messages:
    msg.reference.delete()

# Delete all artifacts (recursive)
artifacts = session_ref.collection('artifacts').stream()
for artifact in artifacts:
    # Delete versions and files
    versions = artifact.reference.collection('versions').stream()
    for version in versions:
        files = version.reference.collection('files').stream()
        for file in files:
            file.reference.delete()
        version.reference.delete()
    artifact.reference.delete()

# Delete session document
session_ref.delete()
```

---

## Firestore Security Rules ✓ IMPLEMENTED

**File:** `firestore.rules`

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // User sessions
    match /users/{userId}/chat_sessions/{sessionId} {
      allow read, write: if request.auth != null && request.auth.uid == userId;

      // Artifacts
      match /artifacts/{artifactId} {
        allow read, update, delete: if canAccessArtifact(userId) && validArtifactType(request.resource.data);
        allow create: if canAccessArtifact(userId) && validArtifactType(request.resource.data);

        // Artifact revisions (versions)
        match /revisions/{revisionId} {
          allow read, write: if canAccessArtifact(userId);
        }
      }
    }
  }

  // Helper functions
  function canAccessArtifact(userId) {
    return request.auth != null && request.auth.uid == userId;
  }

  function validArtifactType(data) {
    return !("type" in data) || data.type == 'code';
  }
}
```

**Security Principles:**
1. Users can only access their own data (`request.auth.uid == userId`)
2. All operations require authentication
3. Type validation on artifact creation
4. Recursive rules for subcollections

**Deployment:**
```bash
firebase deploy --only firestore:rules
```

---

## Firebase Storage Integration

**Status:** Configured but not actively used for artifacts

**Configuration:**
```python
# app/config.py
FIREBASE_STORAGE_BUCKET = os.getenv('FIREBASE_STORAGE_BUCKET')

# Initialized in config.py
firebase_options['storageBucket'] = Config.FIREBASE_STORAGE_BUCKET
```

**Potential Use Cases:**
- User profile pictures
- Generated images
- Large file attachments
- Artifact exports (ZIP files)

**Access Pattern:**
```python
from firebase_admin import storage

bucket = storage.bucket()
blob = bucket.blob(f'users/{user_id}/files/{filename}')
blob.upload_from_filename(local_path)
url = blob.public_url
```

---

## Error Handling Patterns

### Firestore Exceptions

```python
from google.cloud.exceptions import NotFound, PermissionDenied

try:
    doc = session_ref.get()
    if not doc.exists:
        return jsonify({'error': 'Session not found'}), 404
except NotFound:
    return jsonify({'error': 'Session not found'}), 404
except PermissionDenied:
    return jsonify({'error': 'Access denied'}), 403
except Exception as e:
    logger.error(f"Firestore error: {str(e)}")
    return jsonify({'error': 'Database error'}), 500
```

### Transaction Failures

```python
from google.api_core.exceptions import Aborted

try:
    @admin_firestore.transactional
    def update_with_transaction(transaction):
        # ... transaction logic
        pass

    update_with_transaction(db.transaction())
except Aborted:
    logger.warning("Transaction aborted, retrying...")
    # Firestore automatically retries, but handle if needed
except Exception as e:
    logger.error(f"Transaction failed: {str(e)}")
    raise
```

---

## Performance Optimization

### 1. Batch Writes
- Artifact files written in batches (up to 500 operations)
- Reduces network round trips
- Atomic commits

### 2. Pagination
- Messages paginated (50-100 per page)
- Cursor-based pagination with `start_after()`
- Reduces initial load time

### 3. Selective Field Queries
- Only fetch needed fields with `.select()`
- Reduces bandwidth and deserialization time

### 4. Indexing
- Composite indexes for complex queries
- Automatic single-field indexes

### 5. Caching
- Client-side: localStorage for panel width, draft artifacts
- Server-side: Python LRU cache for frequently accessed data

---

## Monitoring and Observability

### Logging

```python
logger.info(f"Created artifact {artifact_id} for session {session_id}")
logger.warning(f"Artifact block rejected: {error_message}")
logger.error(f"Failed to fetch artifact: {str(exc)}")
```

**Log Rotation:**
- ConcurrentRotatingFileHandler
- Max 10MB per file
- 10 backup files
- Location: `logs/app.log`

### Metrics to Track

1. **Artifact Creation Rate:**
   - Number of artifacts created per day/week
   - Success vs. failure ratio

2. **Version History Growth:**
   - Average versions per artifact
   - Storage usage trends

3. **Query Performance:**
   - Response times for artifact fetching
   - Cache hit rates

4. **Error Rates:**
   - Parse errors (malformed artifact blocks)
   - Firestore transaction failures
   - Permission denied errors

---

## Migration and Backup Strategies

### Data Export

```python
# Export all artifacts for a user
def export_user_artifacts(user_id):
    sessions_ref = db.collection('users').document(user_id).collection('chat_sessions')

    for session in sessions_ref.stream():
        artifacts_ref = session.reference.collection('artifacts')
        for artifact in artifacts_ref.stream():
            # Export to JSON
            artifact_data = artifact.to_dict()
            # ... save to file or external storage
```

### Firestore Backup

**Managed Backups:**
- Use Google Cloud Console or gcloud CLI
- Scheduled daily backups recommended

```bash
gcloud firestore export gs://[BUCKET_NAME]/[EXPORT_PREFIX]
```

### Disaster Recovery

1. **Automated Backups:** Daily exports to Cloud Storage
2. **Point-in-Time Recovery:** Available with Firestore native mode
3. **Multi-Region Replication:** Configure for high availability

---

## Future Enhancements

### Planned Improvements

1. **Real-Time Collaboration:**
   - Firestore snapshot listeners for live updates
   - Multiple users editing same artifact

2. **Full-Text Search:**
   - Algolia or Elasticsearch integration
   - Search across artifact content

3. **Analytics:**
   - User engagement metrics
   - Popular languages/frameworks

4. **Artifact Sharing:**
   - Public links for artifacts
   - Collaborative permissions

5. **Storage Optimization:**
   - Compress artifact content
   - Deduplication of common files
   - Move large artifacts to Cloud Storage

---

## Reference Links

- [Firestore Documentation](https://firebase.google.com/docs/firestore)
- [Firebase Admin SDK (Python)](https://firebase.google.com/docs/admin/setup)
- [Security Rules Reference](https://firebase.google.com/docs/firestore/security/get-started)
- [Best Practices](https://firebase.google.com/docs/firestore/best-practices)

---

**Document Version:** 1.0
**Last Updated:** 2025-12-29
**Maintainer:** Claude Code Assistant
