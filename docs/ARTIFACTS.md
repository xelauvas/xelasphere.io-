## Universal Claude-Style Artifacts

The assistant can now emit a structured ` ```artifact … ``` ` block that describes a complete project. The backend parses the block, validates it, persists it in Firestore, and surfaces it in a Claude-inspired side panel that works for every language.

### 1. Contract & Parsing

````text
```artifact
{
  "title": "Project Name",
  "entrypoint": "README.md",
  "files": [
    {"path": "README.md", "language": "markdown", "content": "# Project"},
    {"path": "src/main.py", "language": "python", "content": "print('hi')"}
  ],
  "meta": { "runtime": "python", "run": "python src/main.py" }
}
```
````

Parser rules:

- JSON must be valid and the only thing inside the fence.
- Paths must be relative (no `..`, no leading `/`).
- Each path is unique; max 40 files.
- Each file is text (binary files rejected) and ≤ 200 KB; total ≤ 800 KB.
- Entrypoint must exist (defaults to first file).
- We compute `primaryLanguage`, byte sizes, and a short summary from the entrypoint.
- If the entire reply is a tiny single-file snippet (≤ 12 lines, ≤ 800 bytes) the UI renders it inline instead of opening the panel.

Helper APIs exposed from `app/artifacts.py`:

- `extract_artifact_block(text)` – returns normalized payload or raises `ArtifactParseError`.
- `normalize_artifact_payload(data)` – shared validation logic for tests.

### 2. Storage Model (Firestore)

```
users/{uid}/chat_sessions/{sessionId}/artifacts/{artifactId}
  artifactId          : string
  conversationId      : string
  title               : string
  entrypointPath      : string
  meta                : map
  versionCount        : number
  activeVersionId     : string
  createdAt/updatedAt : timestamps
  isActive            : bool
  versions/{versionId}
    versionId         : string
    versionNumber     : number
    entrypointPath    : string
    primaryLanguage   : string
    summary           : string
    fileCount         : number
    totalBytes        : number
    createdAt         : timestamp
    files/{fileDocId}
      path            : string
      language        : string
      content         : string
      mime            : string|null
      isBinary        : bool (always false for now)
      byteSize        : number
```

- The first artifact for a chat creates the document and version `v1`. Every subsequent artifact block appends a revision (`versionNumber += 1`).
- `ArtifactStore.persist_decision` chooses `_create_artifact` vs `_append_version` depending on whether an active artifact exists for the chat.
- `GET /chat/<session>/artifacts` returns lightweight metadata for list views.
- `GET /chat/<session>/artifacts/<artifactId>` returns the artifact plus all versions (metadata) and the active version with file contents.
- `GET /chat/<session>/artifacts/<artifactId>/versions/<versionId>` fetches a specific version on demand; `/zip` streams a ZIP (iframe sandbox never executes non-web code).
- `POST /chat/<session>/artifacts/active` accepts `artifact_id` + `version_id` to pin a different version as the active one, so reopening the panel preserves the last selection.

### 3. Chat Flow

- When a decision contains a normalized payload we attempt Firestore persistence. Success returns the full artifact (with versions/files) so the UI can open immediately.
- If persistence fails/offline we build a draft artifact with synthetic IDs, store it in a local draft cache, and surface it with `pending: true`.
- `artifact_reference` objects persisted with messages now include `{artifactId, versionId, versionNumber, title, primaryLanguage, entrypoint, summary, pending}` and are replayed when rendering history.
- Tiny snippets bypass the panel and render inline with a copy button to keep the chat compact.

### 4. Artifact Panel UI

- Resizable drawer on the right; width preference stored and pushes the incognito avatar via `body.artifact-panel-open`.
- Header shows status, title, language badge, version dropdown, and a ZIP download button.
- Tabs: **Preview**, **Code**, **Info**.
    - Preview picks a renderer:
        1. HTML if entrypoint is `.html` or `index.html` exists (inlines CSS/JS safely in an `<iframe sandbox="allow-scripts">`).
        2. Markdown if entrypoint is `.md` (rendered with Marked + DOMPurify).
        3. Plain-text fallback for everything else.
    - Code tab lists files in a folder tree and shows the selected file in a syntax-highlighted viewer with “Copy file / Copy all”.
    - Info tab summarizes version number, entrypoint, file count, and primary language.
- Switching versions fetches data lazily from `/versions/:id` and updates preview/code/info. Downloads go through `/zip` (no client-side bundling).

### 5. Testing

`tests/test_artifacts.py` now covers:

- Artifact block validation (happy path + duplicate path rejection).
- Router recognizing the ` ```artifact` block.
- Store delegating to `_create_artifact` or `_append_version`.

Additional renderer safety is covered indirectly via unit tests and manual QA (HTML sandbox + DOMPurify).

### 6. Extending Renderers

`app/static/js/artifactPanel.js` exposes a `RENDERERS` array. To add a new renderer:

1. Append an object with `{name, matches(version), render(frame, version)}`.
2. `matches` should return true when the renderer should handle the version.
3. `render` receives the iframe element and the version (with `files`, `entrypointPath`, etc.) and must write `frame.srcdoc`.
4. Keep sandboxing rules in mind—no remote scripts or navigation.

### 7. Triggering Artifacts

- Prompt the assistant to _build_/_create_/_generate_ code or projects.
- The system prompt now instructs the model to emit exactly one ` ```artifact` block (no prose) for builders.
- The parser rejects malformed blocks so the chat UI simply displays the inline response if validation fails, keeping the experience resilient.
