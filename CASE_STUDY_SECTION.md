# Executive Summary (read first)

- Designed and operated a multi-tenant AI backend with auth, search, ingestion, and LLM orchestration.
- Optimised for resilience, security, and predictable operation on commodity infrastructure.
- Designed explicit degraded modes so dependency failures do not cause outages or user lockouts.
- Personally handled production failures across auth, search, OCR, and inference, replacing crash paths with controlled fallbacks.

## Failure Philosophy

- External dependency failures must not crash the service.
- Missing binaries → degraded capability, not downtime.
- Third-party outages → circuit breaker + user-visible feedback.
- Misconfigured hosts → log loudly, keep running.
- User-facing errors should be actionable and finite, not infinite spinners or silent failures.

## Ownership

I was the primary engineer responsible for the architecture, implementation, and production hardening over multiple deployments and iterations. I incorporated feedback from stakeholders, but the final design decisions and trade-offs were mine.

## Risk Decision: HTTP Boundary Control

- **Risk**  
  A heavyweight framework would hide critical security and session behaviour behind implicit lifecycles, while a minimal stack risks incomplete CSRF, rate limiting, and session controls.
- **Decision**  
  Use Flask with targeted extensions (Flask-Session, Flask-WTF/CSRF, Flask-Limiter, Flask-CORS) wired inside a single `create_app` factory.
- **Trade-off**  
  We accept that scaling requires awareness of Flask’s single-threaded defaults and that session storage needs sticky hosts, but we gain full visibility into middleware order, logging, and security headers without inheriting an opinionated lifecycle.
- **Outcome**  
  The app bootstraps CSRF protection, rate limiting, strict CSP headers, and rotating file logs before any blueprint handles traffic, plus it can relax into degraded modes (e.g., unsigned sessions) when hosts misbehave, preventing several total outages during host misconfiguration and third-party failures while keeping attackers in check (`app/__init__.py:16-140`).

## Risk Decision: External AI/ Search Dependency Outages

- **Risk**  
  Relying on Groq/OpenRouter LLMs and Brave Search introduces third-party outages that could block core workflows or spike API spend through uncontrolled retries.
- **Decision**  
  Wrap every outbound dependency in circuit breakers, TTL caches, TF-IDF relevance scoring, and Prometheus-backed observability so we can fail fast, back off, and expose understandable status to users.
- **Trade-off**  
  We invest engineering time in custom clients (TaskGenerator + SearchClient), accept slightly stale search snippets because of caching, and add operational complexity around metrics collection, but we avoid hard dependencies on any single vendor.
- **Outcome**  
  Brave 429s and Groq hiccups now trip breakers, return actionable “try again soon” states, and stay within quota ceilings; we survived multiple provider incidents without downtime or runaway billing while maintaining conversational context through cached search results and queued tasks (`app/grog_api.py:42-198`, `app/views.py:918-1139`).
