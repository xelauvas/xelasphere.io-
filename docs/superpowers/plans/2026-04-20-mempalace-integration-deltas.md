# MemPalace Integration — Plan Deltas

> **Purpose.** When implementation discovers the plan got something slightly wrong — an import path, a library API that changed, a test fixture that needs an extra arg, an ordering constraint the plan missed — the fix goes in the code *and* gets one line here. Future-you reading the plan in month 8 wonders why the code diverges; this file explains.
>
> The plan at `2026-04-20-mempalace-integration.md` is the frozen record of what we decided on 2026-04-20. The deltas here are the ground truth as of execution time.

---

## Format

One delta per bullet. Include: the sub-milestone (M0.x), what the plan said, what the code actually does, and why. Link to the commit.

- `YYYY-MM-DD M0.x` — [one-line change]. Commit `<sha>`. Reason: [why].

---

## Deltas

- `2026-04-20 M0.1.1` — Python 3.12.3 used for the sidecar venv instead of the 3.11 the plan presumed. `requires-python = ">=3.11"` is satisfied. Commits `78161f8`, `e43c2a4`, `b5bba4e`. Reason: host only has 3.12 installed; satisfies the floor. Watch for 3.12-specific behavior differences in later milestones (esp. `asyncio` task-group semantics and `sentence-transformers` / `torch` CPU wheels).
- `2026-04-20 M0.1.1` — Required OS-level packages `pkg-config` and `libsystemd-dev` installed via apt for `systemd-python` to build. Not captured in the provisioning script yet; fold into `deploy/mempalace/provision.sh` when M0.19 is dispatched.
- `2026-04-20 M0.1.3` — `requirements-dev.txt` installed into the existing project venv at `/root/xelas.io/xelasphere.io-/venv/` (the sidecar has its own dedicated `mempalace-sidecar/.venv/`). Reason: system `pip` is PEP-668-externally-managed on this host. Correct split — Flask-side deps live in the Flask venv, sidecar deps in the sidecar venv.
- `2026-04-20 M0.1.2` — `pytest.ini` `testpaths` narrowed from the plan's `tests app` (line 341 of plan) to `tests app/mempalace/tests`. Reason: widening to the whole `app/` tree surfaced 102 previously-uncollected tests from `app/memory_v2/` and `app/memory_v3/`, one of which (`test_pinned_must_be_active_invariant`) is a pre-existing latent failure unrelated to M0.1. Applying the plan verbatim would have inherited a red CI suite into M0.2. Narrowed scope matches the plan's intent (M0.1.2 Step 6 only asked `app/mempalace/tests/` to be reachable). The memory_v2 failure is a separate issue to file and fix, not part of this milestone.
- `2026-04-20 M0.1.1` — Sidecar `.gitignore` extended beyond the plan's 7 entries to also ignore `.ruff_cache/`, `.mypy_cache/`, `.coverage`, `htmlcov/`. Reason: ruff is declared as a sidecar dev dep (`pyproject.toml:27`) so `.ruff_cache/` will materialize on first lint run. Coverage and mypy caches are industry-standard for Python packages. Additive only; no plan intent is changed.
- `2026-04-20 M0.1` — Deliberate cross-venv `httpx` pin-style mismatch: Flask-side `requirements-cpu.txt` pins `httpx==0.28.1` (exact), sidecar `pyproject.toml` declares `httpx>=0.27` (floor). Not a bug — the two processes communicate over UDS with HTTP/1.1 wire format, which is stable across httpx minor versions. Each venv is entitled to resolve its own version. If a wire-protocol incompatibility ever surfaces, either pin the sidecar to the same exact version or add an integration test that exercises both ends together.
- `2026-04-20 M0.2.5` — No-op. The plan's M0.2.5 calls for adding a shadow-log-ownership docstring to `mempalace_sidecar/__init__.py`, but the M0.1 implementer pre-shipped that docstring during M0.1.1. No M0.2.5 commit created.
- `2026-04-20 M0.2.4` — Test-only deviation from plan. Plan's `test_sd_notify.py` uses `httpx.AsyncClient(transport=ASGITransport(app=app))` to verify lifespan notifies READY. `httpx 0.28.x` `ASGITransport` no longer drives the ASGI lifespan protocol (breaking change from earlier versions). Implementer swapped to `fastapi.testclient.TestClient` used as a context manager — it does drive lifespan, requires no new dependency, and the assertion (`_sd_notify_ready` called once during startup) is unchanged. Production code in `main.py` matches the plan verbatim. Commit `613eea1`.
- `2026-04-20 M0.2` — Quality-review fixes applied beyond plan:
  - `auth.py`: `TokenAuthMiddleware` now uses `hmac.compare_digest` instead of `!=` for token compare (constant-time; mitigates timing attack on the 32-byte secret).
  - `auth.py`: `EXEMPT_PREFIXES` renamed to `EXEMPT_PATHS`; match is now `path == p or path.startswith(p + "/")` so that a rogue route at `/healthzetc` or `/metricsX` cannot bypass auth by prefix accident.
  - `main.py`: bare `except Exception: pass` in sd_notify helpers narrowed to `except ImportError` (systemd-python missing) and `except OSError` (NOTIFY_SOCKET missing or dead); common body factored to a private `_sd_notify(message)`. Prevents silent swallowing of programmer errors (e.g. a future API shape change in `systemd.daemon`).
  - `config.py`: `data_dir` validator renamed `_must_exist` → `_must_be_writable` and raises `ValueError` if the directory exists but isn't writable by the process. Catches the systemd misowner failure mode explicitly instead of letting the first write crash.
  - Reasons / references: `/root/xelas.io/xelasphere.io-` M0.2 code-review report (issues #1, #2a, #4, #5). Rejected — left as-is for scoping: extract sd_notify to its own module (#2b, YAGNI at 2 helpers), injectable lifespan factory (#3, speculative), `_started_at` in lifespan (#6, defer to M0.11), `WWW-Authenticate` header + module-constant error string (#7, YAGNI), inline `extra="ignore"` TODO comment (#8, repo prefers comment-less code), routes aggregator / SCHEMA_VERSION relocation / salt min_length test (#9-#11, all defer to their natural milestone).
