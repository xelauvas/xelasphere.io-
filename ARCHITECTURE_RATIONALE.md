# Architecture Rationale

This document captures the thinking behind the XelaSphere backend instead of only restating the file tree. For every major choice we note what we optimised for, what we gave up, which hard constraints boxed us in, and the battle scars that shaped the current code.

## How we shaped the system

### Flask as the request boundary
We kept Flask as the core request layer because its extension ecosystem covers every control we needed—CSRF, session management, rate limiting, and CORS—without forcing us into a heavier framework. The `create_app` factory wires CSRF, `Flask-Session`, limiter hooks, and a strict CSP/HSTS-style header set, then registers the view/auth blueprints and configures rotating logs in one place (`app/__init__.py:16-140`). That keeps the HTTP boundary thin and observable while letting feature code live inside blueprints.

### Firebase Auth + Firestore as the state spine
Instead of running our own auth service and SQL database, we leaned on Firebase’s Auth + Firestore combo. Config bootstraps the Admin SDK from env vars, validates that the service-account file actually exists, and exposes a shared Firestore client for the rest of the app (`app/config.py:10-109`). Chat history, embeddings, preference tags, and credits all sit in per-user subcollections, handled through the `ChatSession` helper that wraps Firestore reads/writes and performs incremental updates (`app/views.py:264-390`). This keeps session state and analytics co-located with identity.

### AI orchestration and fallbacks
Task generation and on-demand completions sit behind a “multi-engine” abstraction. `TaskGenerator` carries a circuit breaker plus a ranked list of Groq/OpenRouter model IDs, so a hiccup in one LLM vendor does not stall the UI (`app/grog_api.py:42-158`). The shared `model_catalog` maps human-friendly IDs to providers and labels, enforcing a single source of truth for what front-end dropdowns expose (`app/model_catalog.py:1-22`). On the inference side, each prompt goes through token budgeting, sentiment analysis, search enrichment, and optional embedding before we persist it (`app/views.py:200-347, 887-1008`), which keeps downstream tooling (clustering, summarisation) viable.

### Retrieval/search guardrails
The Brave-backed `SearchClient` is a singleton with TTL caching, TF-IDF scoring, domain allow/deny lists, Prometheus metrics, and exponential backoff (`app/views.py:918-1139`). That design was deliberate: we needed to enrich answers with web context without blowing through API quotas or surfacing low-quality locales. Caching + vector scoring is cheaper than introducing another datastore, and the per-process singleton means every worker shares the same throttling memory.

### File ingestion hardening
Uploads move through a multi-layer validation and extraction pipeline. We verify size/mime type using both extension and magic bytes (`app/views.py:402-629`), branch to PDF/image/text handlers, and for PDFs we run structured extraction first, then fall back to OCR on embedded images if no glyphs were found (`app/views.py:428-500`). Image OCR itself reuses several pre-processing passes, language fallbacks, and even EasyOCR if Tesseract is unavailable (`app/views.py:503-565`). This lets us accept messy user assets while keeping the attack surface predictable.

## Trade-offs we accepted

1. **Server-side sessions over stateless JWTs.** We stuck with `Flask-Session` so that CSRF protection, limiter keys, and multi-step auth flows can piggyback on server-managed session stores. When signer configuration explodes (common on ephemeral disks) we deliberately fall back to unsigned filesystem sessions rather than failing logins outright (`app/__init__.py:39-48`). The trade-off: horizontal scaling requires sticky storage, but we judged that cheaper than re-implementing CSRF-safe JWT refresh logic.
2. **Firestore’s eventual consistency vs. relational guarantees.** Firestore simplifies per-user subcollections and increment operations (`app/views.py:264-390`), but it forces us to reason about propagation delays. We mitigate that by writing chat mutations through helper methods that atomically union message batches, instead of issuing ad-hoc field updates all over the codebase.
3. **CPU-friendly inference stack vs. peak accuracy.** The embedding layer only initialises `sentence-transformers` when PyTorch is present, otherwise we degrade to “no embeddings” (`app/views.py:211-224`). Likewise, OCR tries multiple light-weight preprocessing steps before calling EasyOCR with `gpu=False` (`app/views.py:503-565`). Accuracy can lag GPU-heavy setups, but we stay deployable on commodity containers.
4. **Single search backend.** All search requests go through Brave (`app/config.py:33-41`, `app/views.py:918-1139`). Adding Bing/Google would add cost and consent hurdles; instead we invest in client-side heuristics (blocked domains, Latin heuristics, TF-IDF ranking) to keep Brave results usable.
5. **Centralised task orchestration.** The circuit-breaker-based `TaskGenerator` intentionally avoids caching so every “More ideas” click feels new (`app/grog_api.py:104-198`). The flip side is higher API usage, but novelty trumped cost for this feature.

## Constraints we had to navigate

- **Multi-tenant identity requirements.** Stakeholders wanted “sign in with whatever you already use,” so we wired Google, Microsoft, and Discord OAuth side-by-side, complete with PKCE helpers and configuration toggles (`app/auth.py:25-56, 59-99`). Every route also has fine-grained rate limits to keep phishing/bot attempts in check (`app/auth.py:104-200`).
- **Strict security envelope.** Production must set its CSP, referrer policy, frame busting, and session cookie flags because we embed AI features and allow uploads. The middleware that injects all security headers and enforces upload caps lives directly in the app factory so no engineer can accidentally bypass it (`app/__init__.py:54-85`).
- **Environment-driven secrets.** We never bake credentials into images; config loads `.env`, reads every key from env vars, and refuses to start if the Firebase credential path is missing or invalid (`app/config.py:7-109`). That constraint came from auditors asking for provable secrets management.
- **API budget + observability.** Brave, Groq, and OpenRouter all meter calls, so the search client exposes Prometheus counters/histograms (`app/views.py:940-1030`), and `TaskGenerator` guards repeated failures with its breaker (`app/grog_api.py:42-76, 104-158`). Those hooks feed the Grafana dashboards we rely on.
- **File upload attack surface.** User demand for PDF/image ingestion forced us to treat uploads as untrusted by default—validating magic bytes, normalising filenames, capping sizes, and hashing user IDs into filenames for traceability (`app/views.py:402-639`). That constraint dictated the layered validator/extractor structure.

## What broke and how we fixed it

1. **Flask-Session exploded on some hosts.** When the filesystem backend could not initialise signed cookies (missing secret or unwritable session dir) the app previously crashed during startup. We now catch the exception, disable signing, log loudly, and retry so the service keeps running (with degraded security) until ops fix the host (`app/__init__.py:39-48`).
2. **Firebase double-initialisation errors.** Early deployments imported Firebase twice (one per module) which raised `ValueError: The default Firebase app already exists`. We now gate every `initialize_app` call with `_apps` checks in both config and views modules (`app/config.py:96-109`, `app/views.py:91-94`).
3. **Search spam and quota burn.** Without caching/backoff we were hammering Brave and occasionally hitting 429s that bubbled up to users. The singleton `SearchClient` introduced TTL cache, per-minute throttling, exponential backoff, and Prometheus error counters so we can auto-mitigate and observe issues instead of blindly retrying (`app/views.py:940-1139`).
4. **OCR pipeline failed when Tesseract was missing.** Our first build hard-required `tesseract`, which is absent in minimal containers. We now detect the binary at runtime, warn when it is absent, and dynamically fall back to EasyOCR so uploads still succeed (with a warning) on CPU-only boxes (`app/views.py:107-158, 503-565`).
5. **PDF uploads returned blank text.** Scanned PDFs with embedded images fooled the naive text extractor. The current `TextExtractorUtility` logs every processing step, attempts structured extraction, and if nothing is found it iterates through images with OCR, returning both text and debug metadata so we can trace what happened (`app/views.py:428-500`).
6. **LLM outages stalled task creation.** Vendor hiccups stranded users on “Generate tasks”. `TaskGenerator` now rotates through a curated model list and opens a circuit breaker after repeated failures so the UI can surface an actionable “try later” message instead of spinning indefinitely (`app/grog_api.py:42-131`).
7. **Torch imports crashed on CPU-only hosts.** Loading `sentence-transformers` would throw when PyTorch wheels were missing. We lazy-load embeddings, catch import failures, and proceed without embeddings so the rest of the chat stack works while keeping clear warnings for observability (`app/views.py:211-224`).

This reasoning is living documentation—future changes should update it with the “why” so the next engineer understands the decision tree we walked. Feel free to extend sections as we make new trade-offs.
