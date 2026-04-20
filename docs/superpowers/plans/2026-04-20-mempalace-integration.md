# MemPalace Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Companion spec:** `docs/superpowers/specs/2026-04-20-mempalace-integration-design.md` (commit `d777512`). Read it before starting; this plan deliberately does not restate design rationale.

**Review lineage — why this plan is shaped the way it is.**
This feature went through brainstorming with seven sections of pushback, a cross-section consistency pass that caught ten real inconsistencies, a draft review that caught two runtime bugs (asyncio-from-sync-context and post-P4 bootability), and a post-commit pass that caught shell pseudocode. Future-you opening this plan in month 8: the decisions that look odd (tokens don't rotate in v1; single flusher thread; LRU idle TTL = 4h; `off` mode keeps the tool registered) were deliberate, and the spec explains why. Don't re-litigate — iterate within the design.

**Goal:** Integrate MemPalace as XelaSphere's long-term memory backend, running as a localhost-only sidecar behind a Unix domain socket, with a staged five-phase rollout that preserves `memory_v2`/`memory_v3` as dormant-but-callable through P3 and deletes them at P4.

**Standing dispatch discipline — applies to every milestone.** These rules override per-milestone defaults; dispatch prompts should reinforce them rather than drop them.

1. **Red-commit-first TDD, always.** The first commit of each dispatch MUST be red: tests written, target module stubbed or absent, verified failing by running `pytest -v`. The second commit makes them green. Single-commit submissions where tests and implementation land together are REJECTED at review regardless of test pass rate — we can't verify the tests would have failed against the pre-implementation state. If the subagent's local workflow is single-commit for speed, they can split via `git reset --soft HEAD~1` + `git add <tests> && git commit` + `git add <impl> && git commit` before pushing. M0.3.0's `ad70de0` → `b0c583f` is the model.
2. **Mandatory "Deviations from spec" note at end of every dispatch report.** If the count is zero, the subagent MUST list at least three small gaps they considered and rejected, one sentence each. Zero deviations with no gaps-considered means the subagent didn't engage deeply — re-dispatch with "you didn't find anything worth mentioning; that's usually a sign of shallow review, try again."
3. **Stop on ambiguity, don't decide.** If the subagent hits anything the spec or plan doesn't anticipate — a library quirk, an unexpected error shape, an arg the spec didn't name — they stop and file a `NEEDS_CONTEXT` report. They do NOT make the design decision unilaterally. This rule is what caught the mempalace-3.3.1 API mismatch at M0.3 dispatch time rather than at M0.5 integration time.
4. **Read-before-specifying for library-coupled milestones.** When a milestone involves a library whose behavior the spec is guessing at (as opposed to one we've already read), the dispatch prompt should ask the subagent to read the relevant library modules first, produce a findings note naming any mismatch with the spec, THEN implement. Cheaper than discovering at integration-test time. M0.5 is the next milestone where this applies — `mempalace.layers`, `mempalace.searcher.search_memories`, `mempalace.miner.add_drawer` are all involved in the wake-up composition. M0.7 applies — `mempalace.knowledge_graph.KnowledgeGraph`. M0.9 applies — `mempalace.migrate`.
5. **Teeth-bearing tests are non-skippable.** When a milestone has a named test that enforces an invariant mechanically (e.g. `test_close_pops_backend_cache`, `test_eviction_drains_readers`, `test_registry_eviction_bounds_backend_cache`, `test_wakeup_concurrent_faster_than_sum_of_parts`), review WILL fail if the test is missing, skipped, or mocked past its assertion. Named in each milestone's plan section; flagged again in each dispatch prompt.



**Architecture:** Two processes on one host. The sidecar (`mempalace-sidecar`) is a FastAPI app that wraps the `mempalace` 3.3.1 functional API with per-user directories and a size-weighted LRU. The library-isolation boundary is `PalaceHandle` (spec §5.3); Flask calls the sidecar over UDS via an adapter whose only public surface is `app/mempalace/adapter.py`. A durable SQLite outbox makes writes at-least-once; a disk-backed idempotency log on the sidecar (the handle-owned `turns.sqlite`) makes them effectively exactly-once.

**Tech Stack:** Python 3.11 + Flask 3.1 (app) • FastAPI + uvicorn + aiorwlock + pydantic 2 (sidecar) • `mempalace==3.3.1` (ChromaDB-backed) • `httpx` over UDS (transport) • SQLite outbox (Flask) + SQLite idempotency+audit+KG-queue (sidecar) • `prometheus_client` (already in repo) • `sentence-transformers==3.4.1` (already in repo, reused for embedding) • `systemd` + `logrotate` • `restic` for backup.

**Plan structure:**

- **Milestone 0 — Pre-P0 construction.** All code and infrastructure needed before anything can be deployed. This is the bulk of the work; P0 is a 3-day observation window, not an implementation window.
- **P0 — Dark deploy (3 days).** `MEMPALACE_MODE=off`, sidecar up, instrumentation green, zero mempalace-attributed errors.
- **P1 — Shadow writes (14 days).** `MEMPALACE_MODE=shadow`, ingest validated at real traffic scale, rebuild benchmark measured.
- **P2 — Shadow reads (21 days).** Reads logged, weekly rubric run, 2×2 quality decision.
- **P3 — Active (60 days).** Injection live; legacy still callable; day-30 and day-55 rubric re-runs; two monthly capacity reviews.
- **P4 — Legacy removed.** Single PR; deletes `memory_v2`/`memory_v3`, removes `LEGACY_MEMORY` flag, removes the both-off boot refusal.

**File tree touched by this plan (locked now; any deviation is a spec change):**

```
mempalace-sidecar/                    # NEW: subdir; see §7.7 of spec for split trigger
├── pyproject.toml
├── mempalace_sidecar/
│   ├── __init__.py
│   ├── main.py                       # FastAPI factory + lifespan
│   ├── auth.py
│   ├── config.py                     # Pydantic settings
│   ├── registry.py                   # PalaceRegistry + aiorwlock
│   ├── wakeup.py                     # Concurrent L0-L3 composition
│   ├── kg_queue.py                   # Durable kg_pending worker
│   ├── audit.py
│   ├── shadow_log.py                 # (sidecar never writes shadow logs; this is a placeholder, removed if unused — see M0.2)
│   ├── shutdown.py
│   ├── metrics.py
│   ├── migrations/
│   │   ├── __init__.py
│   │   ├── _base.py
│   │   ├── runner.py
│   │   └── v0_to_v1.py               # No-op migration, just establishes the runner harness
│   └── routes/
│       ├── __init__.py
│       ├── palace.py
│       ├── kg.py
│       ├── lifecycle.py
│       └── ops.py
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_auth.py
    │   ├── test_registry.py
    │   ├── test_migrations.py
    │   ├── test_kg_queue.py
    │   ├── test_shutdown.py
    │   └── test_wakeup_concurrency.py
    ├── integration/
    │   ├── test_ingest_search.py
    │   ├── test_wakeup_layers.py
    │   ├── test_cold_vs_hot.py
    │   ├── test_idempotency.py
    │   ├── test_migration_e2e.py
    │   ├── test_delete.py
    │   └── test_rebuild.py
    └── e2e/
        └── test_socket_e2e.py

app/mempalace/                        # NEW
├── __init__.py
├── client.py                         # async SidecarClient + SyncSidecarClient + circuit breaker + ChaosTransport
├── adapter.py                        # MempalaceAdapter — the only module the app imports
├── outbox.py                         # Durable write queue (sync sqlite3, WAL)
├── flusher.py                        # Thread-based flusher (uses SyncSidecarClient)
├── flusher_main.py                   # Entrypoint for mempalace-flusher.service
├── wakeup.py                         # Per-provider WakeupContext formatters
├── tool.py                           # search_memory tool schema + handler
├── shadow_sink.py                    # threaded queue.Queue + daemon writer (NOT asyncio)
├── feature_flag.py                   # MEMPALACE_MODE + LEGACY_MEMORY
├── metrics.py                        # Metrics class (M0.11b) + build_default_metrics()
├── redaction/
│   ├── __init__.py
│   ├── rules.py
│   ├── redaction_rules.yaml
│   ├── README.md                     # Four-step feedback loop
│   └── tests/
│       ├── conftest.py
│       ├── test_rules.py
│       └── redaction_corpus.yaml
└── tests/
    ├── conftest.py
    ├── test_client.py
    ├── test_outbox.py
    ├── test_outbox_property.py       # Hypothesis-driven
    ├── test_flusher.py
    ├── test_metrics.py
    ├── test_wakeup_formatters.py
    ├── test_feature_flag.py
    ├── test_adapter.py
    ├── test_shadow_sink.py
    ├── test_tool.py
    └── contracts/
        └── test_sidecar_openapi.py   # schemathesis against snapshot

app/orchestrator/                     # MODIFIED — call-site wiring only
├── ... (existing files get guarded call-sites added; no new files)

app/memory_v2/DEPRECATED.md           # NEW at P3 entry
app/memory_v3/DEPRECATED.md           # NEW at P3 entry

deploy/mempalace/
├── mempalace-sidecar.service         # systemd unit — the sidecar
├── mempalace-flusher.service         # systemd unit — single-instance flusher (M0.19.4)
├── sidecar.env.example
├── mempalace.env.example             # XelaSphere-side flags
├── provision.sh                      # Idempotent provisioning
└── mempalace-mode                    # /usr/local/bin script

docs/ops/
├── mempalace-runbook.md              # Inline sections + STUBs
├── logging-privacy.md
└── dashboards/
    ├── mempalace-live.json
    └── mempalace-rollout.json

docs/mempalace-shadow/
├── README.md                         # Rubric methodology
├── phase-log.md                      # Phase transitions + approvals
└── YYYY-WW.md                        # Weekly rubric results

tools/mempalace-rubric                # Terminal rubric harness (click + input)
```

---

## Milestone 0 — Pre-P0 Construction

Organized as sub-milestones M0.1 → M0.24. Each sub-milestone contains bite-sized tasks. TDD throughout: write the failing test first, run it to see it fail, implement minimally, verify pass, commit.

**Guiding conventions (every task):**
- Exact file paths only — no "somewhere in the app."
- Test code and implementation code are both written out in full.
- Every task ends with a `git commit` step using a conventional-commit message.
- Failing-test expectation is always stated explicitly (`Expected: FAIL with 'foo is not defined'`).
- Running tests under `pytest -v --tb=short -ra` unless specified otherwise.

---

### M0.1 — Repo scaffolding and tooling

#### Task M0.1.1: Create sidecar package skeleton

**Files:**
- Create: `mempalace-sidecar/pyproject.toml`
- Create: `mempalace-sidecar/mempalace_sidecar/__init__.py`
- Create: `mempalace-sidecar/mempalace_sidecar/version.py`
- Create: `mempalace-sidecar/tests/__init__.py`
- Create: `mempalace-sidecar/tests/conftest.py`
- Create: `mempalace-sidecar/.gitignore`

- [ ] **Step 1: Write failing version test**

File `mempalace-sidecar/tests/test_version.py`:
```python
from mempalace_sidecar.version import __version__

def test_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mempalace-sidecar && pytest tests/test_version.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mempalace_sidecar.version'`

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "mempalace-sidecar"
version = "0.1.0"
description = "Sidecar wrapping mempalace for XelaSphere"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "pydantic>=2.10",
  "pydantic-settings>=2.6",
  "aiorwlock>=1.4",
  "aiosqlite>=0.20",
  "prometheus-client>=0.21",
  "mempalace==3.3.1",
  "httpx>=0.27",
  "systemd-python>=235 ; platform_system=='Linux'",
]

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.24",
  "pytest-forked>=1.6",
  "pytest-timeout>=2.3",
  "hypothesis>=6.112",
  "schemathesis>=3.36",
  "ruff>=0.6",
]

[project.scripts]
mempalace-sidecar = "mempalace_sidecar.main:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["mempalace_sidecar"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
asyncio_mode = "auto"
markers = [
  "integration: requires real Chroma + SQLite on tmp_path",
  "e2e: spawns real sidecar subprocess over UDS",
  "slow: > 1s",
]
addopts = "-v --tb=short -ra --timeout=60"
```

- [ ] **Step 4: Create `mempalace_sidecar/__init__.py`**

```python
from mempalace_sidecar.version import __version__

__all__ = ["__version__"]
```

- [ ] **Step 5: Create `mempalace_sidecar/version.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 6: Create empty `tests/__init__.py` and `tests/conftest.py`**

`tests/conftest.py`:
```python
import pytest

@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "mempalace"
    data.mkdir()
    (data / "palaces").mkdir()
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(data))
    return data
```

- [ ] **Step 7: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
dist/
build/
*.egg-info/
```

- [ ] **Step 8: Install in editable mode and run the test**

Run:
```
cd mempalace-sidecar && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/test_version.py -v
```
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add mempalace-sidecar/
git commit -m "feat(sidecar): scaffold mempalace-sidecar package with version test"
```

#### Task M0.1.2: Scaffold Flask-side `app/mempalace/` package

**Files:**
- Create: `app/mempalace/__init__.py`
- Create: `app/mempalace/tests/__init__.py`
- Create: `app/mempalace/tests/conftest.py`
- Modify: `pytest.ini` (add markers for `mempalace_integration`)

- [ ] **Step 1: Write failing import test**

File `app/mempalace/tests/test_package.py`:
```python
def test_package_imports():
    import app.mempalace
    assert hasattr(app.mempalace, "__version__")
```

- [ ] **Step 2: Run test**

Run: `pytest app/mempalace/tests/test_package.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.mempalace'`.

- [ ] **Step 3: Create `app/mempalace/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Create `app/mempalace/tests/__init__.py`** (empty file) and `app/mempalace/tests/conftest.py`:

```python
import pytest

@pytest.fixture
def outbox_db_path(tmp_path):
    return tmp_path / "outbox.db"

@pytest.fixture
def fake_uds_path(tmp_path):
    return tmp_path / "sidecar.sock"
```

- [ ] **Step 5: Update `pytest.ini` markers**

Modify `pytest.ini`:
```ini
[pytest]
testpaths = tests app
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short -ra
filterwarnings =
    ignore::DeprecationWarning
    ignore::PendingDeprecationWarning
markers =
    unit: Unit tests (no external dependencies)
    integration: Integration tests (requires mocked services)
    slow: Tests that take > 1 second
    mempalace_integration: Integration tests for mempalace adapter
```

- [ ] **Step 6: Verify tests pass**

Run: `pytest app/mempalace/tests/ -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/mempalace/ pytest.ini
git commit -m "feat(app): scaffold app/mempalace package"
```

#### Task M0.1.3: Add sidecar test dependencies to main repo requirements

**Files:**
- Modify: `requirements-cpu.txt` (add httpx explicitly)
- Create: `requirements-dev.txt` (if not present) — add `schemathesis`, `hypothesis`

- [ ] **Step 1: Check whether httpx is already pinned**

Run: `grep -n 'httpx' requirements-cpu.txt requirements-gpu.txt 2>/dev/null`

- [ ] **Step 2: If not present, add to `requirements-cpu.txt`**

```
httpx==0.28.1
```

(Place alphabetically near `requests`.)

- [ ] **Step 3: Create `requirements-dev.txt` if missing**

```
pytest>=8
pytest-asyncio>=0.24
pytest-forked>=1.6
pytest-timeout>=2.3
hypothesis>=6.112
schemathesis>=3.36
aiorwlock>=1.4
```

- [ ] **Step 4: Install and verify**

Run: `pip install -r requirements-dev.txt && python -c "import httpx, hypothesis, schemathesis; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add requirements-cpu.txt requirements-dev.txt
git commit -m "build: pin httpx + add dev dependencies for mempalace integration"
```

---

### M0.2 — Sidecar skeleton, config, and auth

#### Task M0.2.1: Pydantic settings with env-file loading

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/config.py`
- Create: `mempalace-sidecar/tests/unit/test_config.py`

- [ ] **Step 1: Write failing test for default-value config load**

`tests/unit/test_config.py`:
```python
import pytest
from mempalace_sidecar.config import Settings

def test_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_TOKEN", "deadbeef" * 4)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    s = Settings()
    assert s.socket_path == "/run/mempalace/sidecar.sock"
    assert s.cache_max_bytes == 2 * 1024**3
    assert s.idle_ttl_sec == 14400
    assert s.embed_model == "all-MiniLM-L6-v2"
    assert s.kg_daily_usd_cap == pytest.approx(5.0)
    assert s.shadow_sample_rate == pytest.approx(0.20)

def test_settings_requires_token(monkeypatch, tmp_path):
    monkeypatch.delenv("MEMPALACE_TOKEN", raising=False)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    with pytest.raises(Exception):
        Settings()


def test_prometheus_multiproc_dir_from_bare_env(monkeypatch, tmp_path):
    # prometheus_client reads PROMETHEUS_MULTIPROC_DIR directly; the sidecar
    # also needs to see the same bare-name env var via validation_alias.
    monkeypatch.setenv("MEMPALACE_TOKEN", "d" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path / "prom"))
    s = Settings()
    assert str(s.prometheus_multiproc_dir) == str(tmp_path / "prom")
```

- [ ] **Step 2: Run test → fails**

Run: `cd mempalace-sidecar && pytest tests/unit/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Create `config.py`**

```python
from pathlib import Path
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMPALACE_",
        env_file=None,  # env file supplied via systemd EnvironmentFile
        extra="ignore",
        populate_by_name=True,  # Permits bare-name matches for validation_alias
    )

    socket_path: str = Field(default="/run/mempalace/sidecar.sock")
    socket_group: str = Field(default="mempalace")
    token: str = Field(min_length=32)
    data_dir: Path
    cache_max_bytes: int = Field(default=2 * 1024**3)
    idle_ttl_sec: int = Field(default=14400)
    embed_model: str = Field(default="all-MiniLM-L6-v2")
    kg_llm_provider: str = Field(default="ollama")
    kg_daily_usd_cap: float = Field(default=5.0)
    log_level: str = Field(default="info")
    prometheus_multiproc_dir: Path = Field(
        default=Path("/var/run/mempalace/prom"),
        validation_alias=AliasChoices("PROMETHEUS_MULTIPROC_DIR", "MEMPALACE_PROMETHEUS_MULTIPROC_DIR"),
    )
    shadow_sample_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    user_id_hash_salt: str = Field(min_length=8)

    @field_validator("data_dir")
    @classmethod
    def _must_exist(cls, v: Path) -> Path:
        v = Path(v)
        v.mkdir(parents=True, exist_ok=True)
        return v
```

- [ ] **Step 4: Run test → passes**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/config.py mempalace-sidecar/tests/unit/test_config.py
git commit -m "feat(sidecar): add pydantic settings with env-prefixed config"
```

#### Task M0.2.2: FastAPI app factory + `/healthz`

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/main.py`
- Create: `mempalace-sidecar/mempalace_sidecar/routes/__init__.py`
- Create: `mempalace-sidecar/mempalace_sidecar/routes/ops.py`
- Create: `mempalace-sidecar/tests/unit/test_healthz.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_healthz.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient
from mempalace_sidecar.main import create_app

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_TOKEN", "d" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    return create_app()

async def test_healthz_ok(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["version"], str)
    assert isinstance(body["uptime_s"], (int, float))
    assert body["schema_version"] == 1
```

- [ ] **Step 2: Run → fails**

Run: `pytest tests/unit/test_healthz.py -v`
Expected: FAIL (`ModuleNotFoundError` on `main`).

- [ ] **Step 3: Create `routes/__init__.py`** (empty file) and `routes/ops.py`:

```python
import time
from fastapi import APIRouter
from mempalace_sidecar.version import __version__

router = APIRouter(tags=["ops"])
_started_at = time.monotonic()

SCHEMA_VERSION = 1  # bumped whenever the OpenAPI schema changes incompatibly

@router.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "uptime_s": time.monotonic() - _started_at,
        "schema_version": SCHEMA_VERSION,
    }
```

- [ ] **Step 4: Create `main.py`**

```python
from fastapi import FastAPI
from mempalace_sidecar.config import Settings
from mempalace_sidecar.routes import ops


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="mempalace-sidecar", version="0.1.0")
    app.state.settings = settings
    app.include_router(ops.router)
    return app


def run() -> None:  # entrypoint referenced by pyproject scripts
    import uvicorn
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        uds=settings.socket_path,
        log_level=settings.log_level.lower(),
    )
```

- [ ] **Step 5: Run → passes**

Run: `pytest tests/unit/test_healthz.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/main.py mempalace-sidecar/mempalace_sidecar/routes/ mempalace-sidecar/tests/unit/test_healthz.py
git commit -m "feat(sidecar): FastAPI app factory + /healthz endpoint"
```

#### Task M0.2.3: `X-Sidecar-Token` middleware

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/auth.py`
- Create: `mempalace-sidecar/tests/unit/test_auth.py`
- Modify: `mempalace-sidecar/mempalace_sidecar/main.py` (register middleware)

- [ ] **Step 1: Write failing tests**

`tests/unit/test_auth.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient
from mempalace_sidecar.main import create_app
from mempalace_sidecar.config import Settings

@pytest.fixture
def token():
    return "t" * 32

@pytest.fixture
def app(monkeypatch, tmp_path, token):
    monkeypatch.setenv("MEMPALACE_TOKEN", token)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    return create_app()

async def test_healthz_skips_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200

async def test_palace_route_requires_token(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/palace/u/ingest", json={})
    assert r.status_code == 401

async def test_palace_route_rejects_wrong_token(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/palace/u/ingest", json={}, headers={"X-Sidecar-Token": "wrong"})
    assert r.status_code == 401

async def test_palace_route_accepts_correct_token(app, token):
    # stub palace route will 404 (not implemented yet); we only assert auth passes (not 401)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/palace/u/ingest", json={}, headers={"X-Sidecar-Token": token})
    assert r.status_code != 401
```

- [ ] **Step 2: Run → 3 of 4 fail**

Run: `pytest tests/unit/test_auth.py -v`
Expected: `test_healthz_skips_auth` passes; others fail because middleware doesn't exist.

- [ ] **Step 3: Create `auth.py`**

```python
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

EXEMPT_PREFIXES = ("/healthz", "/readyz", "/metrics")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, expected_token: str):
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)
        token = request.headers.get("X-Sidecar-Token")
        if not token or token != self._expected:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)
```

- [ ] **Step 4: Register middleware in `main.py`**

Replace `create_app` body with:

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="mempalace-sidecar", version="0.1.0")
    app.state.settings = settings
    app.add_middleware(TokenAuthMiddleware, expected_token=settings.token)
    app.include_router(ops.router)
    return app
```

Add import:
```python
from mempalace_sidecar.auth import TokenAuthMiddleware
```

- [ ] **Step 5: Run → all pass**

Run: `pytest tests/unit/test_auth.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/auth.py mempalace-sidecar/mempalace_sidecar/main.py mempalace-sidecar/tests/unit/test_auth.py
git commit -m "feat(sidecar): add X-Sidecar-Token middleware with exemptions"
```

#### Task M0.2.4: `sd_notify` integration + `Type=notify` support

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/main.py` (add lifespan that notifies READY=1)
- Create: `mempalace-sidecar/tests/unit/test_sd_notify.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_sd_notify.py`:
```python
from unittest.mock import patch
import pytest
from httpx import ASGITransport, AsyncClient
from mempalace_sidecar.main import create_app

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    return create_app()

async def test_lifespan_notifies_ready(app):
    with patch("mempalace_sidecar.main._sd_notify_ready") as mock_ready:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            await c.get("/healthz")
        mock_ready.assert_called_once()
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Update `main.py`**

Replace `create_app`:
```python
from contextlib import asynccontextmanager

def _sd_notify_ready() -> None:
    try:
        import systemd.daemon as sd  # type: ignore
        sd.notify("READY=1")
    except Exception:  # systemd not available (tests, dev)
        pass


def _sd_notify_stopping() -> None:
    try:
        import systemd.daemon as sd  # type: ignore
        sd.notify("STOPPING=1")
    except Exception:
        pass


@asynccontextmanager
async def _lifespan(app):
    # Embedding model warmup goes here once registry is wired (M0.5).
    _sd_notify_ready()
    try:
        yield
    finally:
        _sd_notify_stopping()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="mempalace-sidecar", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings
    app.add_middleware(TokenAuthMiddleware, expected_token=settings.token)
    app.include_router(ops.router)
    return app
```

- [ ] **Step 4: Run → passes**

- [ ] **Step 5: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/main.py mempalace-sidecar/tests/unit/test_sd_notify.py
git commit -m "feat(sidecar): sd_notify READY=1 on startup via lifespan hook"
```

#### Task M0.2.5: `shadow_log.py` placeholder removal

The spec listed `shadow_log.py` in the sidecar but shadow logs are Flask-side only. Delete nothing (no file was created); note in `mempalace_sidecar/__init__.py` that shadow logs live in the Flask app, not the sidecar. One-line docstring:

- [ ] **Step 1: Add docstring**

```python
# mempalace_sidecar/__init__.py
"""MemPalace sidecar.

Shadow logs are owned by the Flask app (``app/mempalace/shadow_sink.py``),
not the sidecar. See docs/superpowers/specs/2026-04-20-mempalace-integration-design.md
section 3.
"""
from mempalace_sidecar.version import __version__

__all__ = ["__version__"]
```

- [ ] **Step 2: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/__init__.py
git commit -m "docs(sidecar): clarify shadow logs are Flask-side"
```

---

### M0.3 — PalaceRegistry + per-user RWLock

> **Library-reality reconciliation (2026-04-20).** The original draft of this section assumed `from mempalace import Palace; p = Palace.open(path)` returning an OOP handle with `.knowledge_graph`, `.close()`, `.add_drawer()`, `.search()`, etc. The installed `mempalace==3.3.1` ships a functional-module API instead (path-threaded helpers in `mempalace.palace`, `mempalace.miner`, `mempalace.searcher`, `mempalace.knowledge_graph`, `mempalace.layers`). The sidecar owns the OOP wrapper via `PalaceHandle` (spec §5.3) — the library-isolation boundary. Every `palace.foo(...)` reference in the draft becomes `handle.foo(...)` on the sidecar-owned wrapper, and a new sub-task M0.3.0 builds `PalaceHandle` before the registry uses it. See deltas file entry "2026-04-20 M0.3 library isolation" for the full lineage.
>
> **Dispatch boundary:** M0.3.0 (PalaceHandle) is its own implementer dispatch + spec+quality review gate. M0.3.1 through M0.3.5 (PalaceRegistry) is a second dispatch that consumes the reviewed PalaceHandle.

#### Task M0.3.0: `palace_handle.py` — library isolation wrapper

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/palace_handle.py`
- Create: `mempalace-sidecar/mempalace_sidecar/schemas.py` additions (`DrawerDict`, `AddDrawerResult`, `ListDrawersResult` — TypedDicts or dataclasses, whichever is already in use)
- Create: `mempalace-sidecar/tests/unit/test_palace_handle.py`
- Create: `mempalace-sidecar/tests/integration/test_palace_handle_real.py` (marked `integration`)

**Scope:** implement the full v1 surface specified in spec §5.3 — constructor, `open()`, `add_drawer`, `recent_drawers`, `session_drawers`, `topic_search`, `semantic_search`, `search`, `list_drawers`, `kg_add_triple`, `kg_query_entity`, `kg_timeline`, `close`, `col` property, plus the `turns.sqlite` schema and operations. Read spec §5.3 end-to-end before starting — the method surface, cache-eviction contract, and source_file encoding trick are all specified there.

- [ ] **Step 1: Pin `mempalace==3.3.1` exactly in `pyproject.toml`.**
  Plan's existing pin is `mempalace==3.3.1`. Confirm it stays `==`, not `>=`. The source_file encoding trick depends on the library's drawer-ID derivation (`sha256(source_file + str(chunk_index))[:24]`); any future version that changes this silently breaks idempotency. Pin hard.

- [ ] **Step 2: Write failing tests first.** The test file specifies behavior; implementation follows. At minimum:
  - `test_open_creates_palace_dir_and_child_state` — palace dir, `kg.sqlite`, `turns.sqlite`, `config/`, `meta.json` all present after `open()`.
  - `test_add_drawer_idempotent_by_turn_id` — call twice with same `turn_id`; assert `collection.count() == 1` and second call returns `status="duplicate"`.
  - `test_add_drawer_deterministic_drawer_id` — two separate handles on the same palace, both calling `add_drawer` with the same `turn_id`, produce the same `drawer_id` (confirms the `source_file=f"turn:{turn_id}"` trick holds).
  - `test_session_drawers_returns_by_session` — add 3 turns in session A and 2 in session B; assert `handle.session_drawers("A")` returns exactly the 3 A-drawers in reverse-time order.
  - `test_semantic_search_returns_search_memories_shape` — mock `mempalace.searcher.search_memories` to return a fixed dict; assert the return shape matches `DrawerDict` after our unwrap.
  - `test_list_drawers_offset_pagination` — 25 drawers in, `list_drawers(limit=10, offset=0)` returns first 10, `offset=10` returns next 10, `offset=20` returns last 5 + `next_offset=None`.
  - **`test_close_pops_backend_cache`** — after `handle.close()`, assert `palace_path not in mempalace.palace._DEFAULT_BACKEND._clients`. This is the teeth-bearing cache-contract test required by spec §5.3.
  - `test_close_is_idempotent` — calling `close()` twice must not raise.
  - `test_no_mcp_server_import` — grep the package source for `mempalace.mcp_server`; fails if any import surfaces (spec §5.3 "upstream issue" rule).
  - **`test_turns_authoritative_when_library_id_derivation_changes`** — the failure mode we most fear: a future `mempalace` release changes how `miner.add_drawer` derives `drawer_id` from `source_file`. If `turns.sqlite` is authoritative (spec §4.1), the test passes. If we'd relied on library-derived ID uniqueness, duplicates appear silently. Simulate by monkey-patching `mempalace.miner.add_drawer` to prefix the drawer_id with a salt string, thereby producing a DIFFERENT drawer_id for the same `turn_id`. Call `handle.add_drawer(turn_id="T1", ...)`, then under the monkey-patch call it again with the same `turn_id="T1"`. Assert:
    1. Second call returns `status="duplicate"` (blocked by `turns.sqlite` PK, not library dedup).
    2. `collection.count() == 1` (library did not file a second drawer, because `handle.add_drawer` short-circuited before calling the library).

    This test is the empirical proof that `turns.sqlite` — not the library — is the authority.

- [ ] **Step 3: Implement `palace_handle.py`.** Roughly:

```python
# mempalace-sidecar/mempalace_sidecar/palace_handle.py
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mempalace.config import MempalaceConfig
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.layers import Layer0, Layer1, Layer2, Layer3  # imported if we use them; see §5.3 notes
from mempalace.miner import add_drawer as _library_add_drawer
from mempalace.palace import _DEFAULT_BACKEND, get_collection
from mempalace.searcher import build_where_filter, search_memories

CURRENT_SCHEMA_VERSION = 1


@dataclass
class AddDrawerResult:
    status: str           # "accepted" | "duplicate"
    drawer_id: str


# See spec §5.3 for turns.sqlite schema. Keep this constant colocated so the
# invariant "drawer_id derivation lives exactly one place" holds.
_TURNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    drawer_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    pair_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    ts REAL NOT NULL,
    filed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_session_ts ON turns(session_id, ts);
CREATE INDEX IF NOT EXISTS turns_pair ON turns(pair_id);
"""


class PalaceHandle:
    def __init__(self, palace_path: Path, collection, kg: KnowledgeGraph, config: MempalaceConfig,
                 turns: sqlite3.Connection, meta: dict):
        self.palace_path = Path(palace_path)
        self._collection = collection
        self.kg = kg
        self.config = config
        self.turns = turns
        self.meta = meta
        self._closed = False

    @classmethod
    def open(cls, palace_path: str | Path) -> "PalaceHandle":
        palace_path = Path(palace_path)
        palace_path.mkdir(parents=True, exist_ok=True)

        meta_path = palace_path / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        else:
            meta = {"schema_version": CURRENT_SCHEMA_VERSION, "created_at": time.time()}
            meta_path.write_text(json.dumps(meta))

        # Chroma format migration check (spec §5.6 dimension 1)
        # Done lazily on first open to keep the hot path fast.
        _maybe_migrate_chromadb_format(palace_path)

        collection = get_collection(str(palace_path), create=True)

        kg = KnowledgeGraph(db_path=str(palace_path / "kg.sqlite"))

        config_dir = palace_path / "config"
        config_dir.mkdir(exist_ok=True)
        config = MempalaceConfig(config_dir=config_dir)

        turns = sqlite3.connect(str(palace_path / "turns.sqlite"), check_same_thread=False)
        turns.executescript(_TURNS_SCHEMA)
        turns.commit()

        return cls(palace_path, collection, kg, config, turns, meta)

    def add_drawer(self, *, turn_id: str, session_id: str, pair_id: str,
                   role: str, text: str, ts: float,
                   wing: str = "conversations", room: str = "session",
                   agent: str = "xelasphere") -> AddDrawerResult:
        # Idempotency: turns.sqlite PK gate
        cur = self.turns.execute("SELECT drawer_id FROM turns WHERE turn_id = ?", (turn_id,))
        row = cur.fetchone()
        if row is not None:
            return AddDrawerResult(status="duplicate", drawer_id=row[0])

        source_file = f"turn:{turn_id}"
        # Library deterministically derives drawer_id from (source_file, chunk_index=0, wing, room).
        # We compute the same value locally so turns.sqlite has a row even in the
        # race where another process files the same turn between our SELECT and INSERT.
        import hashlib
        drawer_id = (
            f"drawer_{wing}_{room}_"
            + hashlib.sha256((source_file + str(0)).encode()).hexdigest()[:24]
        )
        _library_add_drawer(self._collection, wing, room, text, source_file, 0, agent)
        self.turns.execute(
            "INSERT OR IGNORE INTO turns (turn_id, drawer_id, session_id, pair_id, role, ts, filed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (turn_id, drawer_id, session_id, pair_id, role, ts, time.time()),
        )
        self.turns.commit()
        return AddDrawerResult(status="accepted", drawer_id=drawer_id)

    def recent_drawers(self, limit: int = 10) -> list:
        # Over-fetch 4x and sort in Python since Chroma has no ORDER BY.
        # Budget check happens in M0.5 integration test.
        over = max(limit * 4, 40)
        got = self._collection.get(limit=over, include=["documents", "metadatas"])
        rows = [
            {"text": d, "metadata": m}
            for d, m in zip(got.get("documents") or [], got.get("metadatas") or [])
        ]
        rows.sort(key=lambda r: r["metadata"].get("filed_at", ""), reverse=True)
        return rows[:limit]

    def session_drawers(self, session_id: str, limit: int = 10) -> list:
        rows = self.turns.execute(
            "SELECT drawer_id FROM turns WHERE session_id = ? ORDER BY ts DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        drawer_ids = [r[0] for r in rows]
        if not drawer_ids:
            return []
        got = self._collection.get(ids=drawer_ids, include=["documents", "metadatas"])
        out = []
        for d, m in zip(got.get("documents") or [], got.get("metadatas") or []):
            out.append({"text": d, "metadata": m})
        return out

    def semantic_search(self, query: str, k: int = 5) -> list:
        result = search_memories(query, str(self.palace_path), n_results=k)
        return result.get("results", [])

    # topic_search v1 == semantic_search. Open question §10.x in spec.
    topic_search = semantic_search

    def search(self, query: str, *, wing: str | None = None, k: int = 5) -> list:
        result = search_memories(query, str(self.palace_path), wing=wing, n_results=k)
        return result.get("results", [])

    def list_drawers(self, *, wing: str | None = None, room: str | None = None,
                     limit: int = 20, offset: int = 0):
        where = build_where_filter(wing, room)
        kwargs = {"limit": limit, "offset": offset, "include": ["documents", "metadatas"]}
        if where:
            kwargs["where"] = where
        got = self._collection.get(**kwargs)
        drawers = [
            {"text": d, "metadata": m}
            for d, m in zip(got.get("documents") or [], got.get("metadatas") or [])
        ]
        # Chroma.count() is cheap; include for the next-offset calculation.
        total = self._collection.count()
        next_offset = offset + limit if offset + limit < total else None
        return {"drawers": drawers, "total": total, "next_offset": next_offset}

    def kg_add_triple(self, *args, **kwargs):
        return self.kg.add_triple(*args, **kwargs)

    def kg_query_entity(self, *args, **kwargs):
        return self.kg.query_entity(*args, **kwargs)

    def kg_timeline(self, *args, **kwargs):
        return self.kg.timeline(*args, **kwargs)

    @property
    def col(self):
        return self._collection

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.kg.close()
        except Exception:
            pass
        try:
            self.turns.close()
        except Exception:
            pass
        # Spec §5.3 cache-eviction contract — MUST pop from the library's
        # backend cache, otherwise ChromaDB PersistentClients leak forever.
        _DEFAULT_BACKEND._clients.pop(str(self.palace_path), None)
        self._closed = True


def _maybe_migrate_chromadb_format(palace_path: Path) -> None:
    """If the on-disk Chroma format predates the installed chromadb,
    call mempalace.migrate.migrate() BEFORE the first collection open."""
    from mempalace.backends.chroma import ChromaBackend
    from mempalace.migrate import detect_chromadb_version, migrate

    try:
        on_disk = detect_chromadb_version(str(palace_path))
    except Exception:
        return
    installed = ChromaBackend.backend_version()
    if on_disk and on_disk != installed:
        migrate(str(palace_path), dry_run=False, confirm=True)
```

- [ ] **Step 4: Run tests, confirm all pass.** Expected count: 9 unit + 1 integration (the integration test uses a real palace directory on `tmp_path`).

- [ ] **Step 5: Commit:**

```bash
git add mempalace-sidecar/mempalace_sidecar/palace_handle.py \
        mempalace-sidecar/mempalace_sidecar/schemas.py \
        mempalace-sidecar/tests/unit/test_palace_handle.py \
        mempalace-sidecar/tests/integration/test_palace_handle_real.py \
        mempalace-sidecar/pyproject.toml
git commit -m "feat(sidecar): PalaceHandle — library isolation boundary for mempalace 3.3.1"
```

**Dispatch review gate:** spec + quality review here, BEFORE M0.3.1 dispatches. The PalaceHandle surface is load-bearing for every downstream milestone; review catches any method-mapping miss before it compounds.

**Dispatch prompt MUST ask for a deviations-from-spec-§5.3 note.** Writing the class almost always reveals gaps in the spec that only surface under implementation: exact error types for missing-palace/corrupt-meta cases, default argument values that weren't specified, library return shapes that differ slightly from the spec's description, method signatures the spec left open. The implementer should produce a short "Deviations from spec §5.3" section at the end of its report listing each such decision. During the review gate we fold them back into §5.3 (amending the spec so reality matches the document) before M0.3.1 begins — otherwise the spec lags reality and subsequent milestones implement against the stale version.

#### Task M0.3.1: `PalaceEntry` dataclass

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/registry.py`
- Create: `mempalace-sidecar/tests/unit/test_registry.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_registry.py
import time
from pathlib import Path
import pytest
from mempalace_sidecar.registry import PalaceEntry

def test_palace_entry_fields(tmp_path):
    now = time.monotonic()
    e = PalaceEntry(
        user_id="u1",
        handle=object(),            # stand-in for PalaceHandle; registry doesn't care about type
        path=tmp_path / "u1",
        size_bytes=1024,
        schema_version=1,
        opened_at=now,
        last_used_at=now,
    )
    assert e.user_id == "u1"
    assert e.size_bytes == 1024
    assert e.schema_version == 1
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Create `registry.py` (dataclass only for now)**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PalaceEntry:
    user_id: str
    handle: Any               # PalaceHandle at runtime; Any to avoid the circular import at module load
    path: Path
    size_bytes: int
    schema_version: int
    opened_at: float
    last_used_at: float
```

> **Note on the consolidated field.** The earlier draft had separate `palace: Any` and `kg: Any` fields because it assumed `palace.knowledge_graph` was an attribute accessible from outside. With `PalaceHandle` as the library-isolation boundary, the KG instance lives at `handle.kg` and the registry only holds one reference. The registry doesn't care about internals — it caches, LRU-evicts, and calls `handle.close()` on eviction.

- [ ] **Step 4: Run → passes**

- [ ] **Step 5: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/registry.py mempalace-sidecar/tests/unit/test_registry.py
git commit -m "feat(sidecar): PalaceEntry dataclass"
```

#### Task M0.3.2: `PalaceRegistry` open/close + size-weighted LRU

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/registry.py`
- Modify: `mempalace-sidecar/tests/unit/test_registry.py`

- [ ] **Step 1: Write failing tests**

Append to `test_registry.py`:

```python
import asyncio

@pytest.fixture
def registry(tmp_path):
    from mempalace_sidecar.registry import PalaceRegistry
    return PalaceRegistry(data_dir=tmp_path, max_bytes=1024, idle_ttl_sec=3600)

async def test_registry_opens_and_caches(registry, tmp_path):
    e = await registry.open("u1")
    assert e.user_id == "u1"
    assert e.path == tmp_path / "palaces" / "u1"
    e2 = await registry.open("u1")
    assert e is e2  # same object cached

async def test_registry_evicts_by_size(monkeypatch, registry):
    # Force each palace to report 600 bytes so two won't fit in 1024 cap
    async def fake_open(uid):
        from mempalace_sidecar.registry import PalaceEntry
        import time
        now = time.monotonic()
        path = registry._data_dir / "palaces" / uid
        path.mkdir(parents=True, exist_ok=True)
        return PalaceEntry(
            user_id=uid, handle=object(), path=path,
            size_bytes=600, schema_version=1, opened_at=now, last_used_at=now,
        )
    monkeypatch.setattr(registry, "_construct_entry", fake_open)
    await registry.open("u1")
    await registry.open("u2")
    # u1 should be evicted (LRU) since 1200 > 1024
    assert "u1" not in registry._cache
    assert "u2" in registry._cache
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Extend `registry.py`**

```python
from collections import OrderedDict
import asyncio
from pathlib import Path

from aiorwlock import RWLock


class PalaceRegistry:
    """Per-user palace cache with size-weighted LRU eviction.

    Thread/task safety:
      * Registry dict mutation is guarded by ``self._registry_lock`` (asyncio.Lock).
      * Per-palace concurrency uses ``aiorwlock.RWLock``; readers parallel, writers exclusive.
    """

    def __init__(self, *, data_dir: Path, max_bytes: int, idle_ttl_sec: int):
        self._data_dir = Path(data_dir)
        self._palaces_dir = self._data_dir / "palaces"
        self._palaces_dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._idle_ttl_sec = idle_ttl_sec
        self._cache: "OrderedDict[str, PalaceEntry]" = OrderedDict()
        self._locks: dict[str, RWLock] = {}
        self._registry_lock = asyncio.Lock()

    def _lock_for(self, user_id: str) -> RWLock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = RWLock()
            self._locks[user_id] = lock
        return lock

    async def _construct_entry(self, user_id: str) -> "PalaceEntry":
        # Minimal for unit tests; M0.3.4 replaces this with PalaceHandle.open().
        path = self._palaces_dir / user_id
        path.mkdir(parents=True, exist_ok=True)
        import time
        now = time.monotonic()
        return PalaceEntry(
            user_id=user_id, handle=None, path=path, size_bytes=0,
            schema_version=1, opened_at=now, last_used_at=now,
        )

    async def open(self, user_id: str) -> "PalaceEntry":
        # Two-phase eviction (see _evict_to_fit_unlocked below). Phase 1 runs
        # inside _registry_lock and returns the list of entries to close.
        # Phase 2 runs after lock release, under each user's write lock, so
        # in-flight readers drain before handle.close() touches their state.
        to_close_after = []
        async with self._registry_lock:
            if user_id in self._cache:
                self._cache.move_to_end(user_id)
                entry = self._cache[user_id]
                import time
                entry.last_used_at = time.monotonic()
            else:
                entry = await self._construct_entry(user_id)
                self._cache[user_id] = entry
                to_close_after = await self._evict_to_fit_unlocked()
        for evicted_uid, evicted_entry in to_close_after:
            await self._close_evicted(evicted_uid, evicted_entry)
        return entry

    def _total_bytes(self) -> int:
        return sum(e.size_bytes for e in self._cache.values())

    async def _evict_to_fit_unlocked(self) -> list:
        # Two-phase eviction (review-fix #7, 2026-04-20):
        # Phase 1 (inside _registry_lock): select candidates and remove them
        # from self._cache so no new requests can find them.
        # Phase 2 (OUTSIDE the registry lock, in the caller): acquire each
        # evicted user's WRITE lock (draining in-flight readers), then call
        # handle.close().
        #
        # Why split phases: if close() ran while we still held _registry_lock,
        # a concurrent reader that acquired its per-user reader_lock BEFORE
        # we grabbed _registry_lock could still be mid-call with the handle's
        # collection in its stack frame. Closing underneath it hits a
        # garbage-collected Chroma client. See test_eviction_drains_readers.
        #
        # Invariant: every evicted entry still has handle.close() called on it
        # eventually (spec §5.3 cache-eviction contract). Phase 2 guarantees
        # this; the returned list is what Phase 2 iterates.
        to_close = []
        while self._total_bytes() > self._max_bytes and len(self._cache) > 1:
            uid, evicted = next(iter(self._cache.items()))
            self._cache.pop(uid)
            to_close.append((uid, evicted))
        return to_close
```

Callers of `_evict_to_fit_unlocked` must do Phase 2 themselves. The pattern (used by both `open` and `sweep_idle`):

```python
async def open(self, user_id: str, *, track_cold: bool = False):
    to_close_after = []
    async with self._registry_lock:
        was_cold = user_id not in self._cache
        if was_cold:
            entry = await self._construct_entry(user_id)
            self._cache[user_id] = entry
            to_close_after = await self._evict_to_fit_unlocked()
        else:
            self._cache.move_to_end(user_id)
            entry = self._cache[user_id]
            import time
            entry.last_used_at = time.monotonic()
    # Phase 2 — registry lock released. Close evicted handles under each
    # user's write lock so in-flight readers drain first.
    for evicted_uid, evicted_entry in to_close_after:
        await self._close_evicted(evicted_uid, evicted_entry)
    return (entry, was_cold) if track_cold else entry


async def _close_evicted(self, uid: str, evicted_entry) -> None:
    lock = self._locks.get(uid)
    handle = getattr(evicted_entry, "handle", None)
    if handle is None:
        return
    async def _do_close():
        try:
            handle.close()
        except Exception:
            pass
    if lock is None:
        await _do_close()
        return
    async with lock.writer_lock:
        await _do_close()
```

- [ ] **Step 4: Run → passes**

- [ ] **Step 5: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/registry.py mempalace-sidecar/tests/unit/test_registry.py
git commit -m "feat(sidecar): PalaceRegistry with size-weighted LRU eviction"
```

#### Task M0.3.3: Hypothesis property test for byte accounting

**Files:**
- Modify: `mempalace-sidecar/tests/unit/test_registry.py`

- [ ] **Step 1: Add property test**

```python
from hypothesis import given, strategies as st, settings as hyp_settings

@hyp_settings(max_examples=50)
@given(
    ops=st.lists(
        st.one_of(
            st.tuples(st.just("open"), st.text(min_size=1, max_size=4, alphabet="abcde"), st.integers(0, 500)),
        ),
        min_size=0, max_size=30,
    )
)
async def test_total_bytes_accurate(ops, tmp_path):
    from mempalace_sidecar.registry import PalaceRegistry, PalaceEntry
    r = PalaceRegistry(data_dir=tmp_path, max_bytes=10_000, idle_ttl_sec=3600)

    async def construct_with_size(size):
        async def inner(uid):
            path = r._palaces_dir / uid
            path.mkdir(parents=True, exist_ok=True)
            import time
            now = time.monotonic()
            # Field order: user_id, handle, path, size_bytes, schema_version, opened_at, last_used_at
            return PalaceEntry(uid, None, path, size, 1, now, now)
        return inner

    for op in ops:
        action, uid, size = op
        r._construct_entry = await construct_with_size(size)  # type: ignore
        await r.open(uid)
        expected = sum(e.size_bytes for e in r._cache.values())
        assert r._total_bytes() == expected
```

- [ ] **Step 2: Run → passes (exercising randomised interleavings)**

- [ ] **Step 3: Commit**

```bash
git add mempalace-sidecar/tests/unit/test_registry.py
git commit -m "test(sidecar): hypothesis property test for registry byte accounting"
```

#### Task M0.3.4: `_construct_entry` opens real `PalaceHandle`

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/registry.py`
- Create: `mempalace-sidecar/tests/integration/__init__.py`
- Create: `mempalace-sidecar/tests/integration/test_registry_real.py`

> **Dependency:** this task consumes `PalaceHandle` as implemented in M0.3.0. Don't dispatch this before M0.3.0's review gate has passed.

- [ ] **Step 1: Write integration test (marked `integration`)**

```python
import pytest
pytestmark = pytest.mark.integration

async def test_opens_real_palace(tmp_path):
    from mempalace_sidecar.registry import PalaceRegistry
    r = PalaceRegistry(data_dir=tmp_path, max_bytes=2**31, idle_ttl_sec=3600)
    e = await r.open("alice")
    assert e.handle is not None
    assert e.path.exists()
    assert (e.path / "meta.json").exists()
    assert (e.path / "kg.sqlite").exists()
    assert (e.path / "turns.sqlite").exists()


async def test_registry_eviction_bounds_backend_cache(tmp_path):
    """Spec §5.3 teeth-bearing test: eviction must pop the library's
    ChromaBackend._clients dict. If a refactor drops handle.close() from
    _evict_to_fit_unlocked, this test fails."""
    from mempalace_sidecar.registry import PalaceRegistry
    from mempalace.palace import _DEFAULT_BACKEND

    # Size cap small enough that 2 palaces can't both fit
    r = PalaceRegistry(data_dir=tmp_path, max_bytes=1024, idle_ttl_sec=3600)
    e1 = await r.open("alice")
    e1.size_bytes = 800  # simulated
    assert str(e1.path) in _DEFAULT_BACKEND._clients

    e2 = await r.open("bob")
    e2.size_bytes = 800
    # Registry should have evicted alice — assert her client was popped
    assert str(e1.path) not in _DEFAULT_BACKEND._clients
    assert str(e2.path) in _DEFAULT_BACKEND._clients


async def test_eviction_drains_readers(tmp_path):
    """Review-fix #7 (2026-04-20): two-phase eviction must acquire the
    evicted user's writer_lock before calling handle.close(), so concurrent
    readers finish cleanly without seeing a closed Chroma client.

    Setup: user A is hot. Reader on A enters its method body (holds
    reader_lock, has self._collection in its frame). In parallel, user B
    arrives and forces eviction of A. The test asserts:
      1. Reader's col.get() completes without exception.
      2. After reader releases, A is no longer in registry._cache AND
         A's palace_path is no longer in _DEFAULT_BACKEND._clients.

    If a future refactor moves handle.close() back inside _registry_lock
    (single-phase eviction), this test fails because the reader's
    col.get() races against client GC.
    """
    import asyncio
    from mempalace_sidecar.registry import PalaceRegistry
    from mempalace.palace import _DEFAULT_BACKEND

    r = PalaceRegistry(data_dir=tmp_path, max_bytes=1024, idle_ttl_sec=3600)
    a = await r.open("alice")
    a.size_bytes = 800
    alice_path = str(a.path)
    assert alice_path in _DEFAULT_BACKEND._clients

    reader_started = asyncio.Event()
    reader_may_finish = asyncio.Event()
    reader_exc = []

    async def slow_reader():
        lock = r._lock_for("alice")
        async with lock.reader_lock:
            reader_started.set()
            try:
                # simulate an in-flight col.get while eviction is attempted
                _ = a.handle.list_drawers(limit=1)
                await reader_may_finish.wait()
                _ = a.handle.list_drawers(limit=1)
            except Exception as e:
                reader_exc.append(e)

    async def evicter():
        await reader_started.wait()
        # force eviction: open bob, size_bytes trips cap, alice gets evicted
        b = await r.open("bob")
        b.size_bytes = 800
        # Let reader finish its second library call BEFORE we check invariants
        reader_may_finish.set()

    await asyncio.gather(slow_reader(), evicter())
    assert reader_exc == [], f"Reader raised: {reader_exc}"
    # Eviction must have completed Phase 2 by now (evicter awaited it).
    assert "alice" not in r._cache
    assert alice_path not in _DEFAULT_BACKEND._clients
```

- [ ] **Step 2: Run → fails (stub `_construct_entry` sets `handle=None`)**

Run: `pytest tests/integration/test_registry_real.py -v -m integration`

- [ ] **Step 3: Replace `_construct_entry` to use `PalaceHandle.open`**

```python
import os
import time
from pathlib import Path

from mempalace_sidecar.palace_handle import PalaceHandle


async def _construct_entry(self, user_id: str) -> "PalaceEntry":
    path = self._palaces_dir / user_id
    handle = PalaceHandle.open(path)           # opens dir + Chroma + KG + turns.sqlite + meta.json
    size = _dir_size_bytes(path)
    now = time.monotonic()
    return PalaceEntry(
        user_id=user_id,
        handle=handle,
        path=path,
        size_bytes=size,
        schema_version=handle.meta.get("schema_version", 1),
        opened_at=now,
        last_used_at=now,
    )


def _dir_size_bytes(p: Path) -> int:
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except FileNotFoundError:
                continue
    return total
```

Key differences from the original draft:
- No `from mempalace import Palace` — that class does not exist in mempalace 3.3.1. `PalaceHandle.open()` is the sidecar-owned wrapper that opens the functional-API resources (Chroma collection, KnowledgeGraph, turns.sqlite, MempalaceConfig) against `palace_path`.
- No `kg=palace.knowledge_graph` field — the KG lives at `handle.kg`, one reference per palace.
- `meta.json` read/write is inside `PalaceHandle.open()`; the registry doesn't need to duplicate it.
- ChromaDB format-version migration check is inside `PalaceHandle.open()` (spec §5.6 dimension 1) — the registry stays ignorant of format versioning.

- [ ] **Step 4: Run → both integration tests pass**

- [ ] **Step 5: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/registry.py mempalace-sidecar/tests/integration/
git commit -m "feat(sidecar): PalaceRegistry opens real PalaceHandle"
```

#### Task M0.3.5: Idle-TTL eviction task

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/registry.py`
- Modify: `mempalace-sidecar/tests/unit/test_registry.py`

- [ ] **Step 1: Write failing test (time-mocked)**

```python
async def test_idle_eviction(tmp_path, monkeypatch):
    from mempalace_sidecar.registry import PalaceRegistry
    r = PalaceRegistry(data_dir=tmp_path, max_bytes=2**31, idle_ttl_sec=5)
    # Insert synthetic entry
    now = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    await r.open("u1")
    now[0] += 4
    await r.sweep_idle()
    assert "u1" in r._cache
    now[0] += 2  # total 6 seconds since last use
    await r.sweep_idle()
    assert "u1" not in r._cache
```

- [ ] **Step 2: Run → fails (sweep_idle missing)**

- [ ] **Step 3: Implement**

Add to `PalaceRegistry`:

```python
async def sweep_idle(self) -> int:
    """Close palaces idle longer than idle_ttl_sec. Returns count evicted.

    Two-phase eviction (see _evict_to_fit_unlocked):
      Phase 1 (inside _registry_lock): pop idle entries so no new requests find them.
      Phase 2 (after lock release): under each user's write lock, close the handle
      so any in-flight reader drains before the close touches handle state.

    Spec §5.3 cache-eviction contract: handle.close() MUST run on every
    eviction path so the library's ChromaBackend._clients dict doesn't
    grow unbounded. Both phases together preserve that invariant.
    """
    import time
    now = time.monotonic()
    evicted = []
    async with self._registry_lock:
        idle = [
            uid for uid, e in self._cache.items()
            if (now - e.last_used_at) > self._idle_ttl_sec
        ]
        for uid in idle:
            entry = self._cache.pop(uid)
            evicted.append((uid, entry))
    # Phase 2
    for uid, entry in evicted:
        await self._close_evicted(uid, entry)
    return len(evicted)
```

- [ ] **Step 4: Run → passes**

- [ ] **Step 5: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/registry.py mempalace-sidecar/tests/unit/test_registry.py
git commit -m "feat(sidecar): PalaceRegistry idle-TTL sweep"
```

---

### M0.4 — Ingest endpoint with disk-backed idempotency

#### Task M0.4.1: Request/response Pydantic models

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/schemas.py`
- Create: `mempalace-sidecar/tests/unit/test_schemas.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_schemas.py
import pytest
from pydantic import ValidationError
from mempalace_sidecar.schemas import IngestRequest

def test_ingest_request_roundtrip():
    r = IngestRequest(
        turn_id="018f1bce-9ab7-7bca-baaa-000000000000",
        session_id="s1",
        pair_id="018f1bce-9ab7-7bca-baaa-111111111111",
        role="user",
        text="hello",
        ts=1_700_000_000.0,
    )
    assert r.role == "user"

def test_ingest_request_rejects_bad_role():
    with pytest.raises(ValidationError):
        IngestRequest(
            turn_id="x", session_id="s", pair_id="y",
            role="system", text="hi", ts=1.0,
        )
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Create `schemas.py`**

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field


Role = Literal["user", "assistant"]


class IngestRequest(BaseModel):
    turn_id: str
    session_id: str
    pair_id: str
    role: Role
    text: str
    ts: float
    attachments: Optional[list[dict]] = None


class IngestResponse(BaseModel):
    status: Literal["accepted", "duplicate"]


class Drawer(BaseModel):
    text: str
    wing: Optional[str] = None
    room: Optional[str] = None
    ts: float
    pair_id: Optional[str] = None
    score: Optional[float] = None


class WakeupMeta(BaseModel):
    hot: bool
    embed_ms: float
    total_ms: float
    warnings: list[str] = Field(default_factory=list)


class WakeupContext(BaseModel):
    user_id: str
    schema_version: int = 1
    l0_recent: list[Drawer] = Field(default_factory=list)
    l1_session: list[Drawer] = Field(default_factory=list)
    l2_topic: list[Drawer] = Field(default_factory=list)
    l3_semantic: list[Drawer] = Field(default_factory=list)
    meta: WakeupMeta


class WakeupRequest(BaseModel):
    query: str = ""
    k: int = 5
    layers: Optional[list[str]] = None
    session_id: Optional[str] = None   # drives L1 session_drawers lookup when present


class SearchRequest(BaseModel):
    query: str
    wing: Optional[str] = None
    k: int = 5


class DeleteRequest(BaseModel):
    requested_by: str
    reason: str


class DeletionReceipt(BaseModel):
    status: Literal["deleted"]
    bytes_removed: int
```

- [ ] **Step 4: Run → passes**

- [ ] **Step 5: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/schemas.py mempalace-sidecar/tests/unit/test_schemas.py
git commit -m "feat(sidecar): request/response schemas"
```

#### Task M0.4.2: Ingest route + idempotency via `PalaceHandle.turns.sqlite`

> **Design consolidation (2026-04-20).** The original draft created a separate `ingest_log.py` module with its own `ingest_log.sqlite` table keyed on `turn_id`. After the PalaceHandle rewrite (spec §5.3), `PalaceHandle.add_drawer` already owns a handle-local `turns.sqlite` with `PRIMARY KEY(turn_id)` for exactly this purpose — duplicate prevention, pair/session lookup, and drawer-id reverse mapping in one table. Two tables with overlapping purpose would be strictly worse. The route now delegates idempotency to `handle.add_drawer` and checks the returned `AddDrawerResult.status`.

> **Schema bump required (2026-04-20 review fix #3).** This task lands the first new route beyond the M0.2 scaffolding (`/healthz`), which changes the FastAPI-generated OpenAPI output. Per spec §5.2 "schema change without `schema_version` bump = PR failure." **Bump `SCHEMA_VERSION = 1` → `SCHEMA_VERSION = 2` in `mempalace_sidecar/routes/ops.py` as part of this task's commit.** `/healthz` will report `{"schema_version": 2, ...}` after deploy. The OpenAPI snapshot test that enforces this invariant ships in M0.12; until then, the bump is a documentation/honesty discipline rather than CI-enforced. M0.12's first snapshot will be taken at v2.

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/routes/palace.py`
- Modify: `mempalace-sidecar/mempalace_sidecar/main.py` (register router + dependencies)
- Create: `mempalace-sidecar/tests/integration/test_ingest_idempotency.py`

- [ ] **Step 1: Write failing integration test**

```python
import pytest
pytestmark = pytest.mark.integration

from httpx import ASGITransport, AsyncClient


@pytest.fixture
def token():
    return "t" * 32


@pytest.fixture
def app(monkeypatch, tmp_path, token):
    monkeypatch.setenv("MEMPALACE_TOKEN", token)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    from mempalace_sidecar.main import create_app
    return create_app()


@pytest.fixture
def auth(token):
    return {"X-Sidecar-Token": token}


async def test_ingest_twice_returns_duplicate(app, auth):
    body = {
        "turn_id": "018f1bce-9ab7-7bca-baaa-000000000000",
        "session_id": "s1",
        "pair_id": "018f1bce-9ab7-7bca-baaa-111111111111",
        "role": "user",
        "text": "hello world",
        "ts": 1_700_000_000.0,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/palace/alice/ingest", json=body, headers=auth)
        r2 = await c.post("/palace/alice/ingest", json=body, headers=auth)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["status"] == "accepted"
    assert r2.json()["status"] == "duplicate"
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Create `routes/palace.py`**

```python
from fastapi import APIRouter, Request

from mempalace_sidecar.schemas import IngestRequest, IngestResponse

router = APIRouter(prefix="/palace", tags=["palace"])


def _registry(request: Request):
    return request.app.state.registry


@router.post("/{user_id}/ingest", response_model=IngestResponse)
async def ingest(user_id: str, body: IngestRequest, request: Request) -> IngestResponse:
    """
    Idempotency comes from PalaceHandle.turns.sqlite (spec §5.3). Ordering:
      1. Acquire the user's write lock (registry-level).
      2. Call handle.add_drawer(turn_id=...). The handle does:
         a. SELECT on turns.sqlite by turn_id. If row exists → return duplicate.
         b. Otherwise call mempalace.miner.add_drawer with source_file=f"turn:{turn_id}"
            (deterministic drawer_id derivation — same turn_id, same drawer_id across
            processes and runs).
         c. INSERT OR IGNORE into turns.sqlite.
      3. Map AddDrawerResult.status → IngestResponse.status.

    Guarantee: a 200 response with status="accepted" implies the drawer is durable
    in Chroma AND recorded in turns.sqlite. status="duplicate" implies the drawer
    was already durable from a prior request. No intermediate state is observable.
    """
    registry = _registry(request)
    entry = await registry.open(user_id)
    lock = registry._lock_for(user_id)
    async with lock.writer_lock:
        result = entry.handle.add_drawer(
            turn_id=body.turn_id,
            session_id=body.session_id,
            pair_id=body.pair_id,
            role=body.role,
            text=body.text,
            ts=body.ts,
        )
    return IngestResponse(status=result.status)
```

- [ ] **Step 4: Wire registry into `main.py` lifespan**

In `main.py`, replace `_lifespan`:

```python
@asynccontextmanager
async def _lifespan(app):
    settings: Settings = app.state.settings
    from mempalace_sidecar.registry import PalaceRegistry
    app.state.registry = PalaceRegistry(
        data_dir=settings.data_dir,
        max_bytes=settings.cache_max_bytes,
        idle_ttl_sec=settings.idle_ttl_sec,
    )
    _sd_notify_ready()
    try:
        yield
    finally:
        _sd_notify_stopping()
```

And register palace router:

```python
from mempalace_sidecar.routes import palace as palace_router
...
app.include_router(palace_router.router)
```

- [ ] **Step 5: Run integration test → passes**

Run: `pytest tests/integration/test_ingest_idempotency.py -v -m integration`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mempalace-sidecar/
git commit -m "feat(sidecar): /ingest endpoint backed by PalaceHandle.turns.sqlite"
```

---

### M0.5 — Wake-up endpoint (concurrent L0/L1/L2/L3)

#### Task M0.5.1: Embedding model pre-warm in lifespan

> **Scope change (2026-04-20).** The original draft had `Embedder` as the primary embedding API, with `embedder.embed(query)` called once in `build_wakeup` and the vector passed to every retrieval layer. The reality (spec §5.3 bridges) is that `mempalace.searcher.search_memories` embeds internally via ChromaDB's default embedding function — we do not call our own embedder during wake-up. However, ChromaDB loads the sentence-transformers model lazily on first query, which would push a ~2-5 s cold-model load onto the first /wake-up of the sidecar process. To keep `/readyz` honest ("embedding_loaded") we still pre-warm — just via a throwaway Chroma query against a dedicated `_warmup` palace instead of a standalone Embedder class.

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/main.py` (lifespan pre-warms)
- Create: `mempalace-sidecar/mempalace_sidecar/warmup.py`
- Create: `mempalace-sidecar/tests/integration/test_warmup.py`

- [ ] **Step 1: Write failing integration test for warmup**

```python
# tests/integration/test_warmup.py
import pytest
pytestmark = pytest.mark.integration

async def test_warmup_primes_chroma_embedding(monkeypatch, tmp_path):
    from mempalace_sidecar.warmup import prime_embedding_function

    # Record how long the first real query takes before and after warmup.
    # After warmup we expect the query to not re-load the model.
    import time
    before = time.monotonic()
    prime_embedding_function(tmp_path / "_warmup")
    warmup_elapsed = time.monotonic() - before

    before_first = time.monotonic()
    from mempalace.palace import get_collection
    col = get_collection(str(tmp_path / "_warmup"), create=False)
    _ = col.query(query_texts=["probe"], n_results=1)
    first_elapsed = time.monotonic() - before_first

    # After warmup, a second query should not incur the model-load cost.
    # Weak assertion: the second query is much faster than warmup itself.
    assert first_elapsed < warmup_elapsed / 2 or first_elapsed < 0.5
```

- [ ] **Step 2: Create `warmup.py`**

```python
from pathlib import Path

from mempalace.palace import get_collection


def prime_embedding_function(warmup_palace_path: Path) -> None:
    """Force ChromaDB's default embedding function to load its sentence-transformers model.

    Chroma loads the model lazily on first query. Calling this at sidecar startup
    shifts the ~2-5s cold-model load off the first user /wake-up. Uses a dedicated
    palace at `warmup_palace_path` which is created if absent; it is never used
    for real user data.
    """
    warmup_palace_path = Path(warmup_palace_path)
    warmup_palace_path.mkdir(parents=True, exist_ok=True)
    col = get_collection(str(warmup_palace_path), create=True)
    # query_texts forces the embedding function to resolve and load.
    # n_results=1 keeps the query cheap; an empty palace returns no rows.
    col.query(query_texts=["warmup"], n_results=1)
```

- [ ] **Step 3: Wire into lifespan**

Modify `main.py`:

```python
from mempalace_sidecar.warmup import prime_embedding_function
...
@asynccontextmanager
async def _lifespan(app):
    settings: Settings = app.state.settings
    prime_embedding_function(settings.data_dir / "_warmup")
    app.state.embedding_ready = True
    from mempalace_sidecar.registry import PalaceRegistry
    app.state.registry = PalaceRegistry(
        data_dir=settings.data_dir,
        max_bytes=settings.cache_max_bytes,
        idle_ttl_sec=settings.idle_ttl_sec,
    )
    _sd_notify_ready()
    try:
        yield
    finally:
        _sd_notify_stopping()
```

The boolean `app.state.embedding_ready` is what `/readyz` will check (M0.11).

- [ ] **Step 4: Run integration test → passes**

- [ ] **Step 5: Commit**

```bash
git add mempalace-sidecar/mempalace_sidecar/warmup.py mempalace-sidecar/mempalace_sidecar/main.py mempalace-sidecar/tests/integration/test_warmup.py
git commit -m "feat(sidecar): pre-warm Chroma embedding function at startup"
```

#### Task M0.5.2: Concurrent wake-up composition (layers in parallel)

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/wakeup.py`
- Modify: `mempalace-sidecar/mempalace_sidecar/routes/palace.py`
- Create: `mempalace-sidecar/tests/unit/test_wakeup_concurrency.py`
- Create: `mempalace-sidecar/tests/integration/test_wakeup_layers.py`

> **Composition-level rewrite (2026-04-20).** The original draft passed a pre-computed query vector into every layer and ran all four layers via `asyncio.gather`. Reality:
> - `mempalace.searcher.search_memories` takes a `query: str` and embeds internally via ChromaDB. We do not own the embedding.
> - `handle.recent_drawers`, `handle.session_drawers`, `handle.topic_search`, `handle.semantic_search` are **synchronous** Python function bodies.
> - `asyncio.gather` over sync bodies does NOT parallelize — it runs them sequentially on the event loop. Real concurrency requires wrapping each call in `asyncio.to_thread`, which dispatches to the default thread pool.
> - **Whether we need that concurrency is a measurement question** (spec §5.3 concurrency model). On modest palaces four serial sync calls may total <100 ms. If so, `asyncio.to_thread` adds overhead without benefit. Integration measurement in Step 6 below decides.
>
> Shipping stance: implement with `asyncio.to_thread` so concurrency is available from day one. If M0.5.3 measurement shows serial is within budget, swap back to serial in a follow-up commit and record the measurement in the deltas file.

- [ ] **Step 1: Write failing unit test — asserts all four layers run via the build_wakeup composer and return the expected WakeupContext shape**

```python
# tests/unit/test_wakeup_concurrency.py
from unittest.mock import MagicMock


async def test_build_wakeup_populates_all_layers():
    from mempalace_sidecar.wakeup import build_wakeup
    handle = MagicMock()
    handle.recent_drawers.return_value = [{"text": "r", "metadata": {"ts": 1.0}}]
    handle.session_drawers.return_value = [{"text": "s", "metadata": {"ts": 2.0}}]
    handle.topic_search.return_value = [{"text": "t", "similarity": 0.7}]
    handle.semantic_search.return_value = [{"text": "d", "similarity": 0.9}]

    ctx = await build_wakeup(
        user_id="u",
        query="hi",
        handle=handle,
        session_id="s1",
        hot=True,
        k=5,
    )

    assert ctx.user_id == "u"
    assert len(ctx.l0_recent) == 1 and ctx.l0_recent[0].text == "r"
    assert len(ctx.l1_session) == 1 and ctx.l1_session[0].text == "s"
    assert len(ctx.l2_topic) == 1 and ctx.l2_topic[0].text == "t"
    assert len(ctx.l3_semantic) == 1 and ctx.l3_semantic[0].text == "d"
    assert ctx.meta.hot is True


async def test_build_wakeup_empty_query_skips_search_layers():
    from mempalace_sidecar.wakeup import build_wakeup
    handle = MagicMock()
    handle.recent_drawers.return_value = []
    handle.session_drawers.return_value = []
    handle.topic_search.return_value = []
    handle.semantic_search.return_value = []

    ctx = await build_wakeup(user_id="u", query="", handle=handle, session_id="s", hot=True, k=5)

    # L2/L3 never called with empty query
    handle.topic_search.assert_not_called()
    handle.semantic_search.assert_not_called()
    assert ctx.l2_topic == []
    assert ctx.l3_semantic == []
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Create `wakeup.py`**

```python
import asyncio
import time
from mempalace_sidecar.schemas import Drawer, WakeupContext, WakeupMeta


def _to_drawer(raw) -> Drawer:
    """Normalize the several shapes our handle methods return into schemas.Drawer.

    - handle.recent_drawers / session_drawers return {text, metadata: {ts, wing, room, pair_id}}
    - handle.semantic_search / topic_search / search return {text, wing, room, similarity, ...}
      (shape of mempalace.searcher.search_memories hits)
    """
    if isinstance(raw, dict):
        meta = raw.get("metadata") or {}
        return Drawer(
            text=raw.get("text", ""),
            wing=raw.get("wing") or meta.get("wing"),
            room=raw.get("room") or meta.get("room"),
            ts=float(raw.get("ts", meta.get("ts", 0.0)) or 0.0),
            pair_id=raw.get("pair_id") or meta.get("pair_id"),
            score=raw.get("similarity") or raw.get("score"),
        )
    # tolerant fallback for object-shaped rows
    return Drawer(
        text=getattr(raw, "text", ""),
        wing=getattr(raw, "wing", None),
        room=getattr(raw, "room", None),
        ts=float(getattr(raw, "ts", 0.0) or 0.0),
        pair_id=getattr(raw, "pair_id", None),
        score=getattr(raw, "similarity", None) or getattr(raw, "score", None),
    )


async def build_wakeup(
    *,
    user_id: str,
    query: str,
    handle,
    session_id: str | None,
    hot: bool = True,
    k: int = 5,
) -> WakeupContext:
    """Compose L0-L3 into a WakeupContext.

    All four layer calls are synchronous Python bodies inside PalaceHandle.
    We run them via asyncio.to_thread to get real parallelism across the
    default thread pool. If M0.5.3 integration measurement shows serial is
    within §4.2's latency budget on our typical palaces, a follow-up commit
    may swap back to serial with the reason recorded in deltas. See spec
    §5.3 "Concurrency model inside PalaceHandle" for the rationale.
    """
    t0 = time.monotonic()

    def _l0():
        return handle.recent_drawers(limit=k) or []

    def _l1():
        if session_id is None:
            return []
        return handle.session_drawers(session_id, limit=k) or []

    def _l2():
        if not query:
            return []
        return handle.topic_search(query, k=k) or []

    def _l3():
        if not query:
            return []
        return handle.semantic_search(query, k=k) or []

    l0r, l1r, l2r, l3r = await asyncio.gather(
        asyncio.to_thread(_l0),
        asyncio.to_thread(_l1),
        asyncio.to_thread(_l2),
        asyncio.to_thread(_l3),
    )

    total_ms = (time.monotonic() - t0) * 1000
    return WakeupContext(
        user_id=user_id,
        l0_recent=[_to_drawer(r) for r in l0r],
        l1_session=[_to_drawer(r) for r in l1r],
        l2_topic=[_to_drawer(r) for r in l2r],
        l3_semantic=[_to_drawer(r) for r in l3r],
        # embed_ms retained in WakeupMeta for schema stability; always 0.0
        # because Chroma handles embedding internally and doesn't surface a timing.
        meta=WakeupMeta(hot=hot, embed_ms=0.0, total_ms=total_ms),
    )
```

- [ ] **Step 4: Wire `/wake-up` route in `routes/palace.py`**

Append:

```python
from mempalace_sidecar.schemas import WakeupRequest, WakeupContext
from mempalace_sidecar.wakeup import build_wakeup


@router.post("/{user_id}/wake-up", response_model=WakeupContext)
async def wake_up(user_id: str, body: WakeupRequest, request: Request, warmup: bool = False) -> WakeupContext:
    registry = _registry(request)
    # Per plan review fix #14: open() reports its own was_cold atomically.
    # No TOCTOU peek into registry._cache from caller.
    entry, was_cold = await registry.open(user_id, track_cold=True)
    from mempalace_sidecar.schemas import WakeupMeta
    if warmup:
        return WakeupContext(
            user_id=user_id,
            meta=WakeupMeta(hot=(not was_cold), embed_ms=0, total_ms=0),
        )
    lock = registry._lock_for(user_id)
    async with lock.reader_lock:
        return await build_wakeup(
            user_id=user_id,
            query=body.query,
            handle=entry.handle,
            session_id=getattr(body, "session_id", None),
            hot=(not was_cold),
            k=body.k,
        )
```

Registry contract update (amend `PalaceRegistry.open` from M0.3.2 / M0.3.4): the method now takes a keyword-only `track_cold: bool = False` argument. When `True`, it returns `(entry, was_cold)` instead of just `entry`. `was_cold` is computed under `_registry_lock` — no TOCTOU. The two-phase eviction pattern introduced in M0.3.2 is preserved (see the "two-phase eviction" note in M0.3.2 Step 3): Phase 1 selects candidates inside the registry lock, Phase 2 closes them outside the lock under each user's write lock so in-flight readers drain first.

```python
async def open(self, user_id: str, *, track_cold: bool = False):
    to_close_after = []
    async with self._registry_lock:
        was_cold = user_id not in self._cache
        if not was_cold:
            self._cache.move_to_end(user_id)
            entry = self._cache[user_id]
            import time
            entry.last_used_at = time.monotonic()
        else:
            entry = await self._construct_entry(user_id)
            self._cache[user_id] = entry
            to_close_after = await self._evict_to_fit_unlocked()
    # Phase 2: close evicted handles with reader-drain discipline (see M0.3.2).
    for evicted_uid, evicted_entry in to_close_after:
        await self._close_evicted(evicted_uid, evicted_entry)
    return (entry, was_cold) if track_cold else entry
```

Update any existing callers in `routes/palace.py` and tests accordingly — they continue working because `track_cold=False` returns the same shape as before.

- [ ] **Step 5: Run unit + integration tests → pass**

- [ ] **Step 6: Commit**

```bash
git add mempalace-sidecar/
git commit -m "feat(sidecar): L0-L3 wake-up composition via PalaceHandle layers"
```

#### Task M0.5.3: Integration test — full wake-up with seeded palace

**Files:**
- Create: `mempalace-sidecar/tests/integration/test_wakeup_layers.py`

- [ ] **Step 1: Write test**

```python
import pytest
pytestmark = pytest.mark.integration

from httpx import ASGITransport, AsyncClient


async def test_wake_up_returns_all_four_layers(app, auth):
    # Seed 10 drawers
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for i in range(10):
            await c.post("/palace/u/ingest", headers=auth, json={
                "turn_id": f"t{i:040d}",
                "session_id": "s1",
                "pair_id": f"p{i:040d}",
                "role": "user" if i % 2 == 0 else "assistant",
                "text": f"message {i}",
                "ts": 1_700_000_000.0 + i,
            })
        r = await c.post("/palace/u/wake-up", headers=auth, json={
            "query": "message 5", "k": 5, "session_id": "s1",
        })
    assert r.status_code == 200
    ctx = r.json()
    assert ctx["user_id"] == "u"
    for layer in ("l0_recent", "l1_session", "l2_topic", "l3_semantic"):
        assert layer in ctx
    # L1 exercised via session_id — must return at least one of the seeded session turns
    assert len(ctx["l1_session"]) > 0
```

- [ ] **Step 1a: Measurement test — serial vs to_thread composition**

```python
async def test_wakeup_composition_within_latency_budget(app, auth):
    """Spec §4.2 budget: p95 wake-up ≤ 500 ms hot, ≤ 2 s cold.
    Measure total_ms across 10 wake-ups against a 100-drawer palace;
    assert p95 under hot budget. If this fails, either (a) drop back to
    serial composition and re-measure, or (b) profile the individual
    layers to find which is blowing budget. Record the outcome as a delta.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # seed 100 drawers
        for i in range(100):
            await c.post("/palace/perf/ingest", headers=auth, json={
                "turn_id": f"perf{i:040d}",
                "session_id": "s1",
                "pair_id": f"perf_p{i:040d}",
                "role": "user" if i % 2 == 0 else "assistant",
                "text": f"turn {i} — some conversational content for retrieval",
                "ts": 1_700_000_000.0 + i,
            })
        # one warmup, then 10 measured
        await c.post("/palace/perf/wake-up", headers=auth, json={"query": "content", "k": 5})
        times_ms = []
        for _ in range(10):
            r = await c.post("/palace/perf/wake-up", headers=auth, json={
                "query": "content", "k": 5, "session_id": "s1",
            })
            assert r.status_code == 200
            times_ms.append(r.json()["meta"]["total_ms"])
        times_ms.sort()
        p95 = times_ms[int(len(times_ms) * 0.95)]
        assert p95 < 500, f"p95={p95:.1f}ms exceeds 500ms hot budget — see spec §4.2"


async def test_wakeup_concurrent_faster_than_sum_of_parts(app, auth):
    """Review-fix #4 (2026-04-20) — mechanical enforcement of the
    parallelism architectural decision. The plan specifies asyncio.to_thread
    wrapping so the four layer calls run concurrently in the default thread
    pool (spec §5.3). A subagent that implements M0.5.2 with a naked
    asyncio.gather over sync bodies would silently get sequential execution
    — tests like test_layers_run and test_p95_latency pass on tiny seed
    data, and the budget blows in production on 10k-drawer palaces.

    Assertion: wake-up wall time is strictly less than the sum of each
    layer's wall time when called standalone. A 0.7× threshold gives slack
    for thread-pool overhead. If the test fails, the implementation is
    running layers serially — fix by wrapping each call in asyncio.to_thread.
    """
    import time
    import asyncio
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # Seed: a non-trivial palace so each layer has real work to do.
        for i in range(300):
            await c.post("/palace/par/ingest", headers=auth, json={
                "turn_id": f"par{i:040d}",
                "session_id": "s1",
                "pair_id": f"par_p{i:040d}",
                "role": "user" if i % 2 == 0 else "assistant",
                "text": f"content turn {i} with a variety of retrievable words and phrases",
                "ts": 1_700_000_000.0 + i,
            })

        # Warm up palace cache and Chroma query-path.
        await c.post("/palace/par/wake-up", headers=auth, json={
            "query": "content", "k": 5, "session_id": "s1",
        })

        # Exercise the route directly to get individual layer timings.
        # The wakeup_seconds histogram labels record per-layer t; here we
        # approximate by calling the build_wakeup helper with all-but-one
        # layers stubbed in a separate test. For integration granularity,
        # rely on the fact that total_ms < sum(component routes) is a strong
        # signal of parallelism. Component calls:
        t0 = time.monotonic()
        _ = await c.get("/palace/par/drawers", headers=auth, params={"limit": 5})
        l0_like = time.monotonic() - t0

        t0 = time.monotonic()
        _ = await c.post("/palace/par/search", headers=auth, json={"query": "content", "k": 5})
        l3_like = time.monotonic() - t0

        t0 = time.monotonic()
        r = await c.post("/palace/par/wake-up", headers=auth, json={
            "query": "content", "k": 5, "session_id": "s1",
        })
        total = time.monotonic() - t0
        assert r.status_code == 200

    # Lower bound on the "sum of parts": L0+L3 (the two we measured).
    # Wake-up also does L1 (cheap sqlite+get) and L2 (same cost as L3);
    # a serial implementation would total ≥ l0_like + l3_like + l3_like.
    #
    # Coefficient choice — 0.7 is load-bearing, not arbitrary.
    # Ideal parallelism over four equal-cost layers is ratio ≈ 0.33
    # (max(l0, l1, l2, l3) / sum). Real parallelism lands higher because:
    #   - thread-pool scheduling has ~1-5 ms overhead per dispatch
    #   - GIL contention on the sync bodies (each Python-heavy) caps speedup
    #   - 2-core CI boxes hit true-parallelism ceiling fast
    # Setting threshold at 0.7 catches "pure serial" (ratio ~1.0) with wide
    # margin, while tolerating the overhead envelope of real thread-pool
    # parallelism on modest hardware. If this test flakes on CI, read the
    # logged layer times below and investigate; DO NOT loosen the coefficient
    # without matching evidence that the overhead has fundamentally changed.
    serial_bound = l0_like + 2 * l3_like
    ratio = total / serial_bound if serial_bound else 1.0
    # Log component times on every run so a flake comes with diagnosis, not
    # just "assertion failed". Use pytest -s (or read captured stderr) to see.
    import sys
    print(
        f"[wakeup-parallelism] l0={l0_like*1000:.1f}ms l3={l3_like*1000:.1f}ms "
        f"total={total*1000:.1f}ms serial_bound={serial_bound*1000:.1f}ms "
        f"ratio={ratio:.2f} (threshold 0.70)",
        file=sys.stderr,
    )
    assert total < 0.7 * serial_bound, (
        f"Wake-up took {total*1000:.0f} ms; serial bound {serial_bound*1000:.0f} ms. "
        f"Ratio {ratio:.2f} ≥ 0.7 — layers may be running sequentially. "
        f"Ensure build_wakeup wraps each layer in asyncio.to_thread. "
        f"Component times: l0={l0_like*1000:.1f}ms l3={l3_like*1000:.1f}ms."
    )
```

- [ ] **Step 2: Reuse `app` and `auth` fixtures from conftest.py** — add to `tests/integration/conftest.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

@pytest.fixture
def token():
    return "t" * 32

@pytest.fixture
def app(monkeypatch, tmp_path, token):
    monkeypatch.setenv("MEMPALACE_TOKEN", token)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    from mempalace_sidecar.main import create_app
    return create_app()

@pytest.fixture
def auth(token):
    return {"X-Sidecar-Token": token}
```

- [ ] **Step 3: Run → passes**

- [ ] **Step 4: Commit**

```bash
git add mempalace-sidecar/tests/integration/
git commit -m "test(sidecar): end-to-end wake-up layer composition"
```

---

### M0.6 — Search + list_drawers + tool fallback

> Implementation mirrors M0.5.2 in structure; each is its own task but the template is the same: failing test → Pydantic schema + route → implementation → integration test → commit. The plan sketches the test and implementation; the executor writes them in full.

#### Task M0.6.1: `/palace/{uid}/search` with wing filter

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/routes/palace.py`
- Create: `mempalace-sidecar/tests/integration/test_search.py`

- [ ] **Step 1: Failing integration test** — ingest 3 drawers, hit `/search`, expect ordered list with the closest match first. Code:

```python
import pytest
pytestmark = pytest.mark.integration

async def test_search_finds_keyword(app, auth):
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/palace/u/ingest", headers=auth, json={
            "turn_id": "a" * 32, "session_id": "s", "pair_id": "x" * 32,
            "role": "user", "text": "GraphQL migration complete", "ts": 1.0,
        })
        r = await c.post("/palace/u/search", headers=auth, json={"query": "GraphQL", "k": 3})
    assert r.status_code == 200
    results = r.json()
    assert any("GraphQL" in d["text"] for d in results)
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Implement route**

```python
from mempalace_sidecar.schemas import SearchRequest, Drawer


@router.post("/{user_id}/search", response_model=list[Drawer])
async def search(user_id: str, body: SearchRequest, request: Request) -> list[Drawer]:
    registry = _registry(request)
    entry = await registry.open(user_id)
    lock = registry._lock_for(user_id)
    async with lock.reader_lock:
        # handle.search bridges to mempalace.searcher.search_memories
        # which does hybrid BM25+vector+closet-boost internally. We do not
        # own the embedding; Chroma does.
        raws = entry.handle.search(body.query, wing=body.wing, k=body.k) or []
    from mempalace_sidecar.wakeup import _to_drawer
    return [_to_drawer(r) for r in raws]
```

- [ ] **Step 4: Run → passes**
- [ ] **Step 5: Commit.**

#### Task M0.6.2: `/palace/{uid}/drawers` paginated list

> **Pagination model (2026-04-20).** Original draft used cursor-based pagination; reality is that ChromaDB's `col.get()` takes `limit` and `offset` with no cursor concept. The spec table (§5.2) was amended to offset-based. Route returns `{drawers, total, next_offset}` where `next_offset=None` signals end-of-pagination.

Identical TDD structure. Implementation:

```python
@router.get("/{user_id}/drawers")
async def drawers(user_id: str, request: Request,
                  wing: str | None = None, room: str | None = None,
                  limit: int = 100, offset: int = 0):
    registry = _registry(request)
    entry = await registry.open(user_id)
    lock = registry._lock_for(user_id)
    async with lock.reader_lock:
        result = entry.handle.list_drawers(wing=wing, room=room, limit=limit, offset=offset)
    from mempalace_sidecar.wakeup import _to_drawer
    return {
        "drawers": [_to_drawer(r) for r in result["drawers"]],
        "total": result["total"],
        "next_offset": result["next_offset"],
    }
```

Test: ingest 5 drawers, request with `limit=2, offset=0`, assert `next_offset=2`; second request with `offset=2` continues pagination; final call (offset≥total) returns `next_offset=None` and an empty drawers list. Commit.

---

### M0.7 — KG queue worker

Five small tasks:

1. **`kg_queue.py` SQLite schema** — matches spec §5.5 exactly; unit test asserts unique-constraint rejection of duplicates.
2. **`enqueue(user_id, drawer_ids)` helper** — wired into `/ingest` as a fire-and-forget call (wrapped in a try/except to never fail ingest).
3. **Batched worker loop** — `async def run_kg_worker(app)` pulls `LIMIT 4` rows every 30s, batches by user, `kg_llm_provider.extract(texts)` returns triples, writes to per-palace `kg.sqlite`. Failure path: increment `attempts`, backoff, eventually `status=failed`.
4. **Cost ceiling** — worker polls a `daily_spend_usd` counter (file-based or SQLite) and skips batches when cap hit.
5. **Tests** — unit test for batch formation; unit test for attempt-counter / backoff; unit test for cap-pause.

Full code for the worker loop:

```python
# mempalace_sidecar/kg_queue.py
import asyncio
import aiosqlite
import json
import time
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_pending (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  drawer_ids TEXT NOT NULL,
  drawer_ids_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  enqueued_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(user_id, drawer_ids_hash)
);

CREATE TABLE IF NOT EXISTS kg_spend (
  date TEXT PRIMARY KEY,
  usd REAL NOT NULL DEFAULT 0
);
"""


class KGQueue:
    def __init__(self, db_path: Path):
        self._db_path = db_path

    async def ensure(self):
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def enqueue(self, user_id: str, drawer_ids: list[str]):
        import hashlib
        h = hashlib.sha256((user_id + "|" + ",".join(drawer_ids)).encode()).hexdigest()
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO kg_pending (user_id, drawer_ids, drawer_ids_hash, enqueued_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, json.dumps(drawer_ids), h, now, now),
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                pass  # already enqueued

    async def claim(self, limit: int = 4) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                "SELECT * FROM kg_pending WHERE status='pending' AND attempts < 5 "
                "ORDER BY enqueued_at LIMIT ?", (limit,)
            )).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                qmarks = ",".join("?" * len(ids))
                await db.execute(
                    f"UPDATE kg_pending SET status='running', updated_at=? WHERE id IN ({qmarks})",
                    (time.time(), *ids),
                )
                await db.commit()
            return [dict(r) for r in rows]

    async def mark_done(self, row_id: int):
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE kg_pending SET status='done', updated_at=? WHERE id=?",
                (time.time(), row_id),
            )
            await db.commit()

    async def mark_failed(self, row_id: int, err: str):
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE kg_pending SET status='pending', attempts=attempts+1, last_error=?, updated_at=? WHERE id=?",
                (err[:500], time.time(), row_id),
            )
            await db.commit()

    async def today_spend(self) -> float:
        today = time.strftime("%Y-%m-%d")
        async with aiosqlite.connect(self._db_path) as db:
            row = await (await db.execute("SELECT usd FROM kg_spend WHERE date=?", (today,))).fetchone()
            return float(row[0]) if row else 0.0

    async def add_spend(self, usd: float):
        today = time.strftime("%Y-%m-%d")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO kg_spend (date, usd) VALUES (?, ?) "
                "ON CONFLICT(date) DO UPDATE SET usd = usd + excluded.usd",
                (today, usd),
            )
            await db.commit()
```

Worker loop in a separate file `kg_worker.py` so the lifespan can launch it and shutdown can cancel it:

```python
# mempalace_sidecar/kg_worker.py
import asyncio
from mempalace_sidecar.kg_queue import KGQueue


async def run_kg_worker(queue: KGQueue, registry, kg_cap_usd: float, interval_sec: int = 30):
    while True:
        try:
            if await queue.today_spend() >= kg_cap_usd:
                await asyncio.sleep(interval_sec)
                continue
            batches = await queue.claim(limit=4)
            for row in batches:
                # Stub: real implementation calls the KG LLM provider.
                # Must be idempotent if retried (pair_id / turn_id keys).
                try:
                    # ... extract triples, write to palace.kg ...
                    await queue.mark_done(row["id"])
                except Exception as e:
                    await queue.mark_failed(row["id"], str(e))
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(interval_sec)
```

Tests for each function, committed separately.

---

### M0.8 — Lifecycle: DELETE palace + rebuild

1. `DELETE /palace/{uid}` — two-phase: acquire write-lock, **`entry.handle.close()`** (this calls `kg.close()`, closes `turns.sqlite`, and pops the library backend cache per spec §5.3 cache-eviction contract), remove the entry from registry cache, `os.replace(dir, dir + ".pending_delete." + ts)`, record audit row, `shutil.rmtree` asynchronously, return `DeletionReceipt`. The registry-cache entry MUST be removed before the directory rename — otherwise a concurrent request for the same user_id gets a handle whose `palace_path` no longer exists.
2. `POST /palace/{uid}/rebuild` — accepts `{"conversations": [...]}` body; iterates the conversations and invokes `entry.handle.add_drawer(turn_id=..., session_id=..., pair_id=..., role=..., text=..., ts=...)` for each under the user's write-lock. Because `add_drawer` is idempotent on `turn_id`, re-runs are safe. Returns `{"ingested": N, "skipped": M, "job_id": "..."}` where `skipped` counts the `status="duplicate"` results.

Each is its own task with failing test → implementation → commit. The integration test for DELETE MUST include `assert str(palace_path) not in mempalace.palace._DEFAULT_BACKEND._clients` after the delete completes — same teeth-bearing check as the eviction test in M0.3.4.

Audit log is its own module:

```python
# mempalace_sidecar/audit.py
import aiosqlite
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS deletion_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  user_id TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  before_bytes INTEGER NOT NULL
);
"""


class AuditLog:
    def __init__(self, db_path: Path):
        self._db_path = db_path

    async def ensure(self):
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def record_delete(self, user_id: str, requested_by: str, reason: str, before_bytes: int):
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO deletion_audit (ts, user_id, requested_by, reason, before_bytes) VALUES (?, ?, ?, ?, ?)",
                (time.time(), user_id, requested_by, reason, before_bytes),
            )
            await db.commit()
```

---

### M0.9 — Migration framework

> **Scope reminder (2026-04-20).** Spec §5.6 names three orthogonal version dimensions: Chroma on-disk format (library-managed via `mempalace.migrate.migrate`), drawer `normalize_version` (library-managed via `file_already_mined` auto-rebuild), and our sidecar's `meta.json schema_version` (this milestone). The migration runner below operates only on dimension 3. Dimension 1 (Chroma format) is handled inside `PalaceHandle.open()` as part of M0.3.0 and does NOT go through the runner here. Dimension 2 heals lazily on next mine; no action needed.

Three tasks:

1. **`migrations/_base.py`** — abstract `Migration` with `from_version`, `to_version`, `apply(palace_dir: Path)`.
2. **`migrations/runner.py`** — sibling-rename pattern (`U.migrating/` → `U.replaced_<ts>/` on success). `pytest-forked` test for crash-mid-migration.
3. **`migrations/v0_to_v1.py`** — no-op, just to exercise the harness.

Runner code:

```python
# mempalace_sidecar/migrations/runner.py
import json
import os
import shutil
import time
from pathlib import Path
from typing import Iterable
from mempalace_sidecar.migrations._base import Migration


def current_version(palace_dir: Path) -> int:
    meta = palace_dir / "meta.json"
    if not meta.exists():
        return 0
    return int(json.loads(meta.read_text()).get("schema_version", 0))


def _copy_hardlink_if_possible(src: Path, dst: Path) -> None:
    """Copy tree using hardlinks on same-fs; fall back to regular copy on EXDEV."""
    try:
        shutil.copytree(src, dst, copy_function=os.link)
    except OSError as e:
        # EXDEV (cross-device) or backend that refuses link — fall back.
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)


def _recover_if_interrupted(palace_dir: Path) -> bool:
    """Detect an interrupted-rename state and promote the migrated sibling.

    Spec §7.5 principle: the original U/ dir is never mutated until verification passes,
    and a crash between the two renames must not lose the migrated data. Call this on
    every open before touching palace_dir.

    Returns True if a recovery happened.
    """
    migrating = palace_dir.with_name(palace_dir.name + ".migrating")
    if not palace_dir.exists() and migrating.exists():
        # Crashed after the first rename but before the second.
        # The migrated version is in .migrating; promote it.
        os.rename(migrating, palace_dir)
        os.sync()
        return True
    return False


def apply_migrations(palace_dir: Path, target_version: int, migrations: Iterable[Migration]) -> None:
    _recover_if_interrupted(palace_dir)
    v = current_version(palace_dir)
    if v == target_version:
        return
    if v > target_version:
        raise RuntimeError(f"Palace schema_version {v} > sidecar {target_version}; sidecar too old")
    migrating = palace_dir.with_name(palace_dir.name + ".migrating")
    if migrating.exists():
        shutil.rmtree(migrating)
    _copy_hardlink_if_possible(palace_dir, migrating)
    try:
        for m in sorted(migrations, key=lambda x: x.from_version):
            if m.from_version >= v and m.to_version <= target_version:
                m.apply(migrating)
                v = m.to_version
        (migrating / "meta.json").write_text(json.dumps({
            "schema_version": target_version,
            "migrated_at": time.time(),
        }))
        os.sync()
        replaced = palace_dir.with_name(palace_dir.name + f".replaced_{int(time.time())}")
        # Two renames. A crash here leaves palace_dir gone and migrating still present;
        # _recover_if_interrupted picks that up on next open.
        os.rename(palace_dir, replaced)
        os.rename(migrating, palace_dir)
    except Exception:
        if migrating.exists():
            shutil.rmtree(migrating, ignore_errors=True)
        raise


def reap_stale(palaces_dir: Path, max_age_sec: int = 24 * 3600) -> int:
    """Reap .replaced_* dirs and orphan .migrating dirs older than max_age_sec.

    CAUTION: never reap a .migrating that is the *only* copy of a user's data.
    _recover_if_interrupted must have been called for every user before this.
    This function refuses to delete `<U>.migrating` if `<U>` does not exist.
    """
    n = 0
    now = time.time()
    for child in palaces_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if name.endswith(".migrating"):
            sibling = palaces_dir / name[: -len(".migrating")]
            if not sibling.exists():
                # Recovery not yet run; leave it alone.
                continue
        elif ".replaced_" not in name:
            continue
        try:
            age = now - child.stat().st_mtime
            if age > max_age_sec:
                shutil.rmtree(child, ignore_errors=True)
                n += 1
        except FileNotFoundError:
            continue
    return n
```

Tests in `tests/unit/test_migrations.py`:
- `test_no_op_when_at_target` — version equal → no dirs touched.
- `test_atomic_rename_happy_path` — version below target → migrating dir created, replaced dir exists, final version bumped.
- `test_refuses_newer_schema` — raises `RuntimeError`.
- `test_crash_mid_migration_leaves_migrating_dir` — uses `pytest-forked` to `os._exit` mid-apply; parent process asserts `U.migrating/` exists.
- `test_crash_between_renames_is_recoverable` — simulate the state where `U/` is gone and `U.migrating/` is the migrated version (by hand-placing dirs). Call `_recover_if_interrupted`. Assert `U/` is restored and `U.migrating/` is gone. **This is the regression test for fix #2 from the plan review — its absence let the reap_stale bug exist.**
- `test_reap_stale_never_deletes_orphan_migrating_when_primary_missing` — place only `U.migrating/` (no `U/`), age it 48h, call `reap_stale`. Assert the `.migrating` dir is **kept**. Only once `_recover_if_interrupted` has promoted it does a later reap of a `.replaced_*` sibling proceed.
- `test_reap_stale_cleans_old_replaced_dirs` — create an old `.replaced_<ts>/`, assert reaped.

Each its own commit.

---

### M0.10 — Shutdown protocol + graceful drain

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/shutdown.py`
- Modify: `mempalace-sidecar/mempalace_sidecar/main.py`
- Create: `mempalace-sidecar/tests/unit/test_shutdown.py`

The `_lifespan` finally block:
1. Sets `app.state.shutting_down = True` (served to `/readyz` as false).
2. Cancels `kg_worker` task.
3. Awaits in-flight request counter to drop to zero with a 10s deadline.
4. Calls `registry.close_all()` — iterates `_cache`, closes palace handles.

Drain counter is implemented as a middleware:

```python
class InFlightCounterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, counter):
        super().__init__(app)
        self._c = counter

    async def dispatch(self, request, call_next):
        self._c["n"] += 1
        try:
            return await call_next(request)
        finally:
            self._c["n"] -= 1
```

Test uses `asyncio.wait_for` with a mock in-flight handler and asserts shutdown returns within 10s; forces a still-running handler to test the cancel path.

---

### M0.11 — Metrics + `/readyz`

Prometheus multiprocess is already in the env (`PROMETHEUS_MULTIPROC_DIR`). Register every metric listed in spec §8.1.

> **Benchmark task added during M0.3.1-5 sign-off (2026-04-20):** M0.3's `PalaceRegistry._construct_entry` runs inside `_registry_lock`, which serialises all cold palace opens across the whole sidecar. Per-open cost is 50-200ms (Chroma init + KG sqlite + dir walk); 10 concurrent first-touch opens queue for 0.5-2s total. This shows up as a latency tail at warm startup (post-restart / post-deploy) and during traffic bursts to cold users.
>
> **M0.11 adds a synthetic load test** that deliberately triggers the bottleneck — N concurrent `open()` calls for distinct cold user_ids — and measures queueing delay. Output: a documented threshold at which the sentinel-pattern fix (release the registry lock across `_construct_entry` using a per-user "open-in-progress" event) becomes warranted. If the measured p95 at realistic N stays under the wake-up budget, defer the fix. If it blows the budget, the fix lands under M0.11 rather than as an emergency under P1 traffic.
>
> Add `test_benchmark_concurrent_cold_opens` to the M0.11 task list.

**Files:**
- Create: `mempalace-sidecar/mempalace_sidecar/metrics.py`
- Modify: `mempalace-sidecar/mempalace_sidecar/routes/ops.py`

```python
# mempalace_sidecar/metrics.py
from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, multiprocess, generate_latest, CONTENT_TYPE_LATEST

ingest_total = Counter("mempalace_ingest_total", "Ingest outcomes", ["status"])
wakeup_seconds = Histogram("mempalace_wakeup_seconds", "Wake-up layer latency", ["layer", "hot"])
search_seconds = Histogram("mempalace_search_seconds", "Search latency", ["hot"])
chroma_query_seconds = Histogram("mempalace_chroma_query_seconds", "Chroma embed+search (library-internal)", ["layer"])
palace_cache_size = Gauge("mempalace_palace_cache_size", "Hot palace count", multiprocess_mode="livesum")
palace_cache_bytes = Gauge("mempalace_palace_cache_bytes", "Total resident bytes", multiprocess_mode="livesum")
palace_cache_hits_total = Counter("mempalace_palace_cache_hits_total", "Hot palace hits")
palace_cache_misses_total = Counter("mempalace_palace_cache_misses_total", "Cold palace opens")
palace_cache_evictions_total = Counter("mempalace_palace_cache_evictions_total", "Evictions", ["reason"])
kg_queue_depth = Gauge("mempalace_kg_queue_depth", "KG queue", ["status"], multiprocess_mode="livesum")
kg_daily_usd_spent = Gauge("mempalace_kg_daily_usd_spent", "KG daily spend", multiprocess_mode="livesum")
kg_extraction_total = Counter("mempalace_kg_extraction_total", "KG outcomes", ["status"])
schema_migration_total = Counter("mempalace_schema_migration_total", "Schema migrations", ["from", "to", "status"])
open_palace_errors_total = Counter("mempalace_open_palace_errors_total", "Palace open errors", ["reason"])
delete_total = Counter("mempalace_delete_total", "GDPR deletes")
readyz = Gauge("mempalace_readyz", "Ready state", multiprocess_mode="livesum")


def render_metrics() -> tuple[bytes, str]:
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return generate_latest(registry), CONTENT_TYPE_LATEST
```

Wire `/metrics` route in `ops.py`:

```python
from fastapi.responses import Response
from mempalace_sidecar.metrics import render_metrics


@router.get("/metrics")
async def metrics() -> Response:
    data, ct = render_metrics()
    return Response(content=data, media_type=ct)
```

Wire `/readyz`:

```python
@router.get("/readyz")
async def readyz(request: Request):
    app = request.app
    # embedding_ready flag is set by the lifespan after prime_embedding_function (M0.5.1).
    # Chroma owns the actual sentence-transformers model — we don't hold a direct reference.
    ok_embed = bool(getattr(app.state, "embedding_ready", False))
    ok_registry = hasattr(app.state, "registry") and app.state.registry is not None
    return {
        "ok": ok_embed and ok_registry and not getattr(app.state, "shutting_down", False),
        "embedding_loaded": ok_embed,
        "db_ok": ok_registry,
        "audit_ok": True,
        "schema_version": 1,
    }
```

**Route instrumentation is explicit, not hand-waved** (plan review fix #10). Each existing route adds metric calls at the exact points listed below. Do this as a separate commit on the route after the functional test is green, so the timer wrap doesn't hide bugs in the happy path.

`routes/palace.py` diff — add the timers:

```python
import time as _time
from mempalace_sidecar.metrics import (
    ingest_total, wakeup_seconds, search_seconds,
    chroma_query_seconds, palace_cache_hits_total, palace_cache_misses_total,
)


@router.post("/{user_id}/ingest", response_model=IngestResponse)
async def ingest(user_id: str, body: IngestRequest, request: Request) -> IngestResponse:
    try:
        # ... existing body, unchanged ...
        resp = IngestResponse(status="accepted")  # or "duplicate", as before
        ingest_total.labels(status=resp.status).inc()
        return resp
    except Exception:
        ingest_total.labels(status="error").inc()
        raise


@router.post("/{user_id}/wake-up", response_model=WakeupContext)
async def wake_up(user_id: str, body: WakeupRequest, request: Request, warmup: bool = False) -> WakeupContext:
    entry, was_cold = await _registry(request).open(user_id, track_cold=True)
    if was_cold:
        palace_cache_misses_total.inc()
    else:
        palace_cache_hits_total.inc()
    hot_label = "0" if was_cold else "1"
    t0 = _time.monotonic()
    # ... existing body ...
    wakeup_seconds.labels(layer="total", hot=hot_label).observe(_time.monotonic() - t0)
    return ctx


@router.post("/{user_id}/search", response_model=list[Drawer])
async def search(user_id: str, body: SearchRequest, request: Request) -> list[Drawer]:
    entry, was_cold = await _registry(request).open(user_id, track_cold=True)
    hot_label = "0" if was_cold else "1"
    t0 = _time.monotonic()
    # ... existing body ...
    search_seconds.labels(hot=hot_label).observe(_time.monotonic() - t0)
    return results
```

`wakeup.py` gets an explicit per-layer timer around each `await`:

```python
async def _timed(label: str, hot_label: str, coro):
    t = time.monotonic()
    r = await coro
    wakeup_seconds.labels(layer=label, hot=hot_label).observe(time.monotonic() - t)
    return r

# In build_wakeup, each layer call is already wrapped in asyncio.to_thread (M0.5.2).
# Wrap the to_thread awaitable with _timed for per-layer timing:
l0r, l1r, l2r, l3r = await asyncio.gather(
    _timed("l0", hot_label, asyncio.to_thread(_l0)),
    _timed("l1", hot_label, asyncio.to_thread(_l1)),
    _timed("l2", hot_label, asyncio.to_thread(_l2)),
    _timed("l3", hot_label, asyncio.to_thread(_l3)),
)
```

> **Note on embedding timer.** The original draft wrapped a standalone `embedder.embed(query)` call in its own Prometheus histogram. After the PalaceHandle rewrite (spec §5.3), embedding happens inside ChromaDB as part of `col.query(query_texts=...)` — we no longer hold a direct reference to the sentence-transformers model. The `wakeup_seconds{layer="l3"}` and `wakeup_seconds{layer="l2"}` timers capture the Chroma-internal embed + search time together; that composition is what our p95 budget is defined against, and having a separate embedding timer would overcount. No standalone `embedding_seconds` metric in v1. If p95 regression investigation ever needs embed-vs-search decomposition, swap to `col.query(query_embeddings=[vec])` and time the two phases explicitly — that's a measurement-driven follow-up, not a v1 concern.

Unit tests:
- `test_ingest_increments_accepted_on_success`
- `test_ingest_increments_duplicate_on_second_call`
- `test_ingest_increments_error_on_raise`
- `test_wakeup_records_layer_timers`
- `test_search_records_hot_cold_label`
- `test_cache_hit_miss_counters_increment`

---

### M0.11b — Flask-side metrics module

**Files:**
- Create: `app/mempalace/metrics.py`
- Create: `app/mempalace/tests/test_metrics.py`

- [ ] **Step 1: Write failing test**

```python
# app/mempalace/tests/test_metrics.py
def test_metrics_expose_expected_names():
    from app.mempalace.metrics import Metrics
    m = Metrics()
    assert m.outbox_depth is not None
    assert m.inserts_dropped("mode_off") is None  # method style
    m.circuit_state("closed", worker_pid=1)
    m.flusher_tick()
    m.redaction_hit("openai_key")


def test_inserts_dropped_increments(capsys):
    from app.mempalace.metrics import Metrics
    from prometheus_client import CollectorRegistry
    registry = CollectorRegistry()
    m = Metrics(registry=registry)
    m.inserts_dropped("mode_off")
    m.inserts_dropped("mode_off")
    from prometheus_client import generate_latest
    text = generate_latest(registry).decode()
    assert 'xelasphere_mempalace_inserts_dropped_total{reason="mode_off"} 2.0' in text
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Implement**

```python
# app/mempalace/metrics.py
import time

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, REGISTRY


class Metrics:
    """Flask-side metrics for the mempalace adapter.

    Plan review fix #8 / #9: all metrics referenced elsewhere in the codebase
    are declared here, and `flusher_last_tick_seconds` is a real Prometheus
    gauge (not a private Python attr), so MempalaceFlusherStalled can fire.
    """

    def __init__(self, registry: CollectorRegistry | None = REGISTRY):
        self._r = registry
        self.outbox_depth = Gauge(
            "xelasphere_mempalace_outbox_depth", "Outbox rows by status",
            ["status"], registry=registry,
        )
        self.outbox_age_seconds = Histogram(
            "xelasphere_mempalace_outbox_age_seconds", "Pending-row age at flush",
            registry=registry,
        )
        self.outbox_rejected = Counter(
            "xelasphere_mempalace_outbox_rejected_total", "Outbox at cap", registry=registry,
        )
        self._inserts_dropped = Counter(
            "xelasphere_mempalace_inserts_dropped_total", "Silent drops",
            ["reason"], registry=registry,
        )
        self._flush_batch_seconds = Histogram(
            "xelasphere_mempalace_flush_batch_seconds", "Flusher batch time",
            registry=registry,
        )
        self._flusher_last_tick = Gauge(
            "xelasphere_mempalace_flusher_last_tick_seconds", "Monotonic ts of last flusher loop",
            registry=registry,
        )
        self._client_request_seconds = Histogram(
            "xelasphere_mempalace_client_request_seconds", "Sidecar round-trip",
            ["route", "status_code"], registry=registry,
        )
        self._circuit_state = Gauge(
            "xelasphere_mempalace_circuit_state", "0=closed 1=half 2=open",
            ["worker_pid"], registry=registry,
        )
        self._circuit_transitions = Counter(
            "xelasphere_mempalace_circuit_transitions_total", "State changes",
            ["from_state", "to_state", "worker_pid"], registry=registry,
        )
        self._redaction_hits = Counter(
            "xelasphere_mempalace_redaction_hits_total", "Scrub counts",
            ["rule"], registry=registry,
        )
        self._wakeup_skipped = Counter(
            "xelasphere_mempalace_wakeup_skipped_total", "Why injection was skipped",
            ["reason"], registry=registry,
        )
        self._mode = Gauge(
            "xelasphere_mempalace_mode", "0=off 1=shadow 2=active",
            registry=registry,
        )

    # --- call-site-friendly methods (stable adapter API) ---

    def inserts_dropped(self, reason: str) -> None:
        self._inserts_dropped.labels(reason=reason).inc()

    def flush_batch(self, seconds: float) -> None:
        self._flush_batch_seconds.observe(seconds)

    def flusher_tick(self) -> None:
        # Plan review fix #9: ticked every loop iteration, empty-outbox included.
        self._flusher_last_tick.set(time.monotonic())

    def client_request(self, route: str, status_code: int, seconds: float) -> None:
        self._client_request_seconds.labels(route=route, status_code=str(status_code)).observe(seconds)

    def circuit_state(self, state: str, *, worker_pid: int) -> None:
        mapping = {"closed": 0, "half_open": 1, "open": 2}
        self._circuit_state.labels(worker_pid=str(worker_pid)).set(mapping[state])

    def circuit_transition(self, from_state: str, to_state: str, *, worker_pid: int) -> None:
        self._circuit_transitions.labels(
            from_state=from_state, to_state=to_state, worker_pid=str(worker_pid),
        ).inc()

    def redaction_hit(self, rule: str) -> None:
        self._redaction_hits.labels(rule=rule).inc()

    def wakeup_skipped(self, reason: str) -> None:
        self._wakeup_skipped.labels(reason=reason).inc()

    def mode(self, value: str) -> None:
        mapping = {"off": 0, "shadow": 1, "active": 2}
        self._mode.set(mapping[value])


def build_default_metrics() -> Metrics:
    return Metrics()
```

- [ ] **Step 4: Update `adapter.py`** — replace `self._metrics.inserts_dropped(...)` call-style signature to match (`reason` positional arg). Redaction `rules.py` uses `metrics.redaction_hit(rule["name"])` instead of `metrics.incr(...)`.

- [ ] **Step 5: Commit**

```bash
git add app/mempalace/metrics.py app/mempalace/tests/test_metrics.py
git commit -m "feat(mempalace): Flask-side metrics module with flusher liveness gauge"
```

---

### M0.12 — Contract test: OpenAPI snapshot

**Files:**
- Create: `mempalace-sidecar/tests/contract/test_openapi_snapshot.py`
- Create: `mempalace-sidecar/tests/contract/openapi_schema.v1.json` (snapshot)

- [ ] **Step 1: Generate initial snapshot**

```python
# Script run once to produce the snapshot:
from mempalace_sidecar.main import create_app
import json, os, pathlib
os.environ["MEMPALACE_TOKEN"] = "t" * 32
os.environ["MEMPALACE_DATA_DIR"] = "/tmp/mp"
os.environ["MEMPALACE_USER_ID_HASH_SALT"] = "x" * 16
app = create_app()
pathlib.Path("tests/contract/openapi_schema.v1.json").write_text(json.dumps(app.openapi(), indent=2, sort_keys=True))
```

- [ ] **Step 2: Write snapshot test**

```python
import difflib
import json
import pytest


def test_openapi_schema_matches_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    from mempalace_sidecar.main import create_app
    app = create_app()
    current = app.openapi()
    from pathlib import Path
    snap_path = Path(__file__).parent / "openapi_schema.v1.json"
    expected = json.loads(snap_path.read_text())
    if current == expected:
        return
    # Plan review structural fix: emit a real diff so the failure is actionable.
    expected_str = json.dumps(expected, indent=2, sort_keys=True).splitlines()
    current_str = json.dumps(current, indent=2, sort_keys=True).splitlines()
    diff = "\n".join(difflib.unified_diff(
        expected_str, current_str,
        fromfile="openapi_schema.v1.json (snapshot)",
        tofile="current",
        lineterm="",
    ))
    pytest.fail(
        "OpenAPI schema drifted. If intentional, bump SCHEMA_VERSION in routes/ops.py "
        "and regenerate the snapshot via `python -m mempalace_sidecar.tools.regen_openapi`.\n\n"
        f"{diff}"
    )
```

- [ ] **Step 3: Commit**

---

### M0.13 — Flask: `app/mempalace/feature_flag.py` + startup check

**Files:**
- Create: `app/mempalace/feature_flag.py`
- Create: `app/mempalace/tests/test_feature_flag.py`
- Modify: `app/__init__.py` (add startup check)

- [ ] **Step 1: Failing test**

```python
# app/mempalace/tests/test_feature_flag.py
import os
import pytest


def test_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("MEMPALACE_MODE", raising=False)
    from app.mempalace.feature_flag import mode, MempalaceMode
    assert mode() == MempalaceMode.OFF


def test_legacy_memory_defaults_to_on(monkeypatch):
    monkeypatch.delenv("LEGACY_MEMORY", raising=False)
    from app.mempalace.feature_flag import legacy_memory_active
    assert legacy_memory_active() is True


def test_startup_rejects_both_off(monkeypatch):
    from app.mempalace.feature_flag import assert_startup_sane
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.setenv("LEGACY_MEMORY", "off")
    monkeypatch.setenv("XELASPHERE_POST_P4", "0")
    with pytest.raises(SystemExit):
        assert_startup_sane()


def test_post_p4_allows_off_with_warn(monkeypatch, caplog):
    from app.mempalace.feature_flag import assert_startup_sane
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.delenv("LEGACY_MEMORY", raising=False)
    monkeypatch.setenv("XELASPHERE_POST_P4", "1")
    with caplog.at_level("WARNING"):
        assert_startup_sane()
    assert any("MEMPALACE_MODE=off" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run → fails**

- [ ] **Step 3: Implement**

```python
# app/mempalace/feature_flag.py
import enum
import logging
import os
import sys


log = logging.getLogger(__name__)


class MempalaceMode(str, enum.Enum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


def mode() -> MempalaceMode:
    return MempalaceMode(os.getenv("MEMPALACE_MODE", "off").lower())


def legacy_memory_active() -> bool:
    return os.getenv("LEGACY_MEMORY", "on").lower() in ("on", "true", "1", "yes")


def _is_post_p4() -> bool:
    return os.getenv("XELASPHERE_POST_P4", "0") == "1"


def assert_startup_sane() -> None:
    m = mode()
    legacy_on = legacy_memory_active()
    if m == MempalaceMode.OFF and not legacy_on:
        if _is_post_p4():
            log.warning(
                "MEMPALACE_MODE=off and LEGACY_MEMORY=off (post-P4 environment). "
                "Memory is globally disabled — relief-valve mode. "
                "Chat will function without long-term memory."
            )
            return
        log.critical(
            "Refusing to boot: MEMPALACE_MODE=off AND LEGACY_MEMORY=off (pre-P4). "
            "This would leave users with no memory at all. "
            "Set MEMPALACE_MODE=shadow|active or LEGACY_MEMORY=on. "
            "Post-P4: set XELASPHERE_POST_P4=1 to allow this state."
        )
        raise SystemExit(2)
```

- [ ] **Step 4: Wire into `app/__init__.py`**

Add near top of `create_app()`, after config load:

```python
from app.mempalace.feature_flag import assert_startup_sane
assert_startup_sane()
```

- [ ] **Step 5: Run → passes**

- [ ] **Step 6: Commit**

---

### M0.14 — Flask: UDS client + circuit breaker

**Files:**
- Create: `app/mempalace/client.py`
- Create: `app/mempalace/tests/test_client.py`

- [ ] **Step 1: Failing tests — connect OK, 401, timeout, 5xx, circuit opens after N failures, reopens after cooldown, ChaosTransport injection works**

Full test shown for one case; others follow pattern:

```python
import pytest
import httpx
from unittest.mock import patch

async def test_client_retries_on_5xx_then_gives_up():
    from app.mempalace.client import SidecarClient, ChaosTransport
    transport = ChaosTransport(httpx.MockTransport(lambda req: httpx.Response(503)))
    client = SidecarClient(
        socket_path="/ignored",
        token="t" * 32,
        transport=transport,
        max_retries=2,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert transport.calls == 3  # 1 initial + 2 retries
```

- [ ] **Step 2: Implement**

```python
# app/mempalace/client.py
import asyncio
import time
from enum import Enum

import httpx


class CircuitState(str, Enum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


class ChaosTransport(httpx.AsyncBaseTransport):
    """Test transport wrapping another transport with latency and failure injection."""

    def __init__(self, inner, *, latency_ms: float = 0, fail_every: int = 0):
        self._inner = inner
        self._latency_ms = latency_ms
        self._fail_every = fail_every
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        if self._latency_ms:
            await asyncio.sleep(self._latency_ms / 1000)
        if self._fail_every and self.calls % self._fail_every == 0:
            raise httpx.ConnectError("injected failure")
        return await self._inner.handle_async_request(request)


class SidecarClient:
    def __init__(
        self,
        *,
        socket_path: str,
        token: str,
        timeout_s: float = 2.0,
        max_retries: int = 1,
        breaker_threshold: int = 5,
        breaker_cooldown_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._timeout = timeout_s
        self._token = token
        self._max_retries = max_retries
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown_s = breaker_cooldown_s
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0
        if transport is None:
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
        self._client = httpx.AsyncClient(
            transport=transport, base_url="http://sidecar",
            timeout=timeout_s,
        )

    @property
    def state(self) -> CircuitState:
        now = time.monotonic()
        if self._state == CircuitState.OPEN and (now - self._opened_at) >= self._breaker_cooldown_s:
            self._state = CircuitState.HALF_OPEN
        return self._state

    def _record_success(self):
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    async def _request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None) -> dict:
        if self.state == CircuitState.OPEN:
            raise RuntimeError("sidecar circuit open")
        headers = {"X-Sidecar-Token": self._token, **(headers or {})}
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                # Plan review fix #7: exponential backoff between retries.
                await asyncio.sleep(min(1.0, 0.1 * (2 ** (attempt - 1))))
            try:
                r = await self._client.request(method, path, json=body, headers=headers)
                r.raise_for_status()
                self._record_success()
                return r.json() if r.content else {}
            except Exception as e:
                last_exc = e
                if attempt == self._max_retries:
                    self._record_failure()
                    raise
        assert last_exc is not None
        raise last_exc

    async def post_json(self, path: str, body: dict, headers: dict | None = None) -> dict:
        return await self._request("POST", path, body=body, headers=headers)

    async def get_json(self, path: str, headers: dict | None = None) -> dict:
        # Plan review fix #5: real GET, not POST-to-a-GET-route.
        return await self._request("GET", path, body=None, headers=headers)

    async def delete_json(self, path: str, body: dict | None = None, headers: dict | None = None) -> dict:
        # Plan review fix #6: real DELETE with body, not X-HTTP-Method-Override hack.
        return await self._request("DELETE", path, body=body, headers=headers)

    async def aclose(self):
        await self._client.aclose()


class SyncSidecarClient:
    """Sync variant for thread-based callers (the flusher, specifically).

    Plan review fix #3: the flusher runs in a thread and must NOT call asyncio.run()
    against a persistent AsyncClient — asyncio.run creates a fresh event loop each call,
    while httpx.AsyncClient binds to the loop it was created in. Use this sync client
    from the flusher instead.
    """

    def __init__(
        self,
        *,
        socket_path: str,
        token: str,
        timeout_s: float = 2.0,
        max_retries: int = 1,
        breaker_threshold: int = 5,
        breaker_cooldown_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._token = token
        self._max_retries = max_retries
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown_s = breaker_cooldown_s
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0
        if transport is None:
            transport = httpx.HTTPTransport(uds=socket_path)
        self._client = httpx.Client(transport=transport, base_url="http://sidecar", timeout=timeout_s)

    @property
    def state(self) -> CircuitState:
        now = time.monotonic()
        if self._state == CircuitState.OPEN and (now - self._opened_at) >= self._breaker_cooldown_s:
            self._state = CircuitState.HALF_OPEN
        return self._state

    def _record_success(self):
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def post_json(self, path: str, body: dict, headers: dict | None = None) -> dict:
        if self.state == CircuitState.OPEN:
            raise RuntimeError("sidecar circuit open")
        headers = {"X-Sidecar-Token": self._token, **(headers or {})}
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                time.sleep(min(1.0, 0.1 * (2 ** (attempt - 1))))
            try:
                r = self._client.post(path, json=body, headers=headers)
                r.raise_for_status()
                self._record_success()
                return r.json() if r.content else {}
            except Exception:
                if attempt == self._max_retries:
                    self._record_failure()
                    raise
        raise RuntimeError("unreachable")

    def close(self):
        self._client.close()
```

Add tests for each new method: `test_client_get_json_hits_get_route`, `test_client_delete_json_sends_delete_with_body`, `test_client_backoff_between_retries` (asserts `time.monotonic()` advances at least 100ms between the first and second call in a 5xx run), and a parallel suite for `SyncSidecarClient` with `httpx.MockTransport` (sync).

- [ ] **Step 3: Run → passes**
- [ ] **Step 4: Commit**

---

### M0.15 — Flask: outbox + flusher

Expanded to task-per-method granularity (plan review structural fix). Each method is its own failing test → implementation → commit cycle.

#### Task M0.15.1: Outbox schema + WAL mode

**Files:**
- Create: `app/mempalace/outbox.py`
- Create: `app/mempalace/tests/test_outbox.py`

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id TEXT NOT NULL UNIQUE,
  pair_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  text BLOB NOT NULL,               -- plaintext for v1; consider encryption later
  ts REAL NOT NULL,
  request_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | claimed | sent | abandoned
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  claim_token TEXT,
  claimed_at REAL,
  enqueued_at REAL NOT NULL,
  sent_at REAL
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox(status, enqueued_at);
CREATE INDEX IF NOT EXISTS outbox_sent_purge_idx ON outbox(status, sent_at);
CREATE INDEX IF NOT EXISTS outbox_user_idx ON outbox(user_id);
"""
```

Implement `Outbox.__init__(path)` that opens `sqlite3.connect(path, check_same_thread=False)`, issues `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`, runs `SCHEMA`. Failing test: construct, assert tables/indexes exist via `sqlite_master`.

#### Task M0.15.2: `insert()`

Failing test: call `outbox.insert(turn_id, pair_id, user_id, session_id, role, text, ts, request_id)` twice with same turn_id → second raises `IntegrityError`. Body count assertion.

Implementation: simple `INSERT` with commit; `enqueued_at=time.time()`.

#### Task M0.15.3: `depth(status)` and `claim_batch(limit)` with serialization

Failing test is the critical one — the multi-flusher safety guarantee.

```python
def test_two_claim_batches_never_overlap(outbox_db_path):
    from app.mempalace.outbox import Outbox
    a = Outbox(outbox_db_path)
    b = Outbox(outbox_db_path)  # two instances, single sqlite file
    for i in range(10):
        a.insert(turn_id=f"t{i}", pair_id=f"p{i}", user_id="u",
                 session_id="s", role="user", text=f"msg{i}", ts=i, request_id=None)
    ra = a.claim_batch(10)
    rb = b.claim_batch(10)
    ids_a = {r["id"] for r in ra}
    ids_b = {r["id"] for r in rb}
    assert not (ids_a & ids_b), f"overlap! {ids_a & ids_b}"
    assert len(ids_a) + len(ids_b) <= 10
```

Implementation uses `BEGIN IMMEDIATE` + conditional UPDATE with claim token:

```python
import uuid

def claim_batch(self, limit: int) -> list[dict]:
    token = str(uuid.uuid4())
    with self._conn:  # acts as a transaction
        self._conn.execute("BEGIN IMMEDIATE")
        rows = self._conn.execute(
            "SELECT id FROM outbox WHERE status='pending' "
            "ORDER BY enqueued_at LIMIT ?", (limit,)
        ).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        import time
        self._conn.execute(
            f"UPDATE outbox SET status='claimed', claim_token=?, claimed_at=? "
            f"WHERE id IN ({placeholders}) AND status='pending'",
            (token, time.time(), *ids),
        )
    # Read back the claimed rows
    rows = self._conn.execute(
        "SELECT * FROM outbox WHERE claim_token=?", (token,)
    ).fetchall()
    return [dict(r) for r in rows]
```

Even though v1 runs **one** flusher, claim tokens protect against accidental double-flush — and they make the outbox readable to ops queries without mutation races.

`depth(status)` is a simple `SELECT COUNT(*) WHERE status=?`.

#### Task M0.15.4: `mark_sent(ids)` / `mark_failed(id, err)`

`mark_sent`: `UPDATE outbox SET status='sent', sent_at=?, claim_token=NULL WHERE id IN (...)`.

`mark_failed`: `UPDATE outbox SET status='pending', attempts=attempts+1, last_error=?, claim_token=NULL WHERE id=?`. After `attempts >= 5`, status should go to `abandoned` instead, so a dead poison pill doesn't loop forever. Tests cover both branches.

#### Task M0.15.5: `abandon_user(user_id)` + `drain_then_abandon(timeout_s)`

Plan review fix #11. Methods:

```python
def abandon_user(self, user_id: str) -> int:
    """Mark all non-sent rows for a user as abandoned (used by delete_palace)."""
    with self._conn:
        cur = self._conn.execute(
            "UPDATE outbox SET status='abandoned' WHERE user_id=? AND status IN ('pending', 'claimed')",
            (user_id,),
        )
    return cur.rowcount


def drain_then_abandon(self, flush_callable, *, timeout_s: float = 600.0, poll_s: float = 2.0) -> dict:
    """Flush all pending rows; after timeout, mark remaining as abandoned.

    Used by mempalace-mode off. Spec §3: 'outbox pre-flip rows drain best-effort
    for 10 minutes, then are marked abandoned.'
    """
    import time as _t
    started = _t.monotonic()
    drained = 0
    while _t.monotonic() - started < timeout_s:
        depth = self.depth("pending")
        if depth == 0:
            break
        rows = self.claim_batch(limit=20)
        for row in rows:
            try:
                flush_callable(row)
                self.mark_sent([row["id"]])
                drained += 1
            except Exception as e:
                self.mark_failed(row["id"], str(e)[:500])
        _t.sleep(poll_s)
    abandoned = 0
    with self._conn:
        cur = self._conn.execute(
            "UPDATE outbox SET status='abandoned' WHERE status IN ('pending', 'claimed')"
        )
        abandoned = cur.rowcount
    return {"drained": drained, "abandoned": abandoned}
```

Tests: `abandon_user` only affects the named user; `drain_then_abandon` returns {drained, abandoned} counts accurately under a stub flush_callable.

#### Task M0.15.6: `purge_old_sent(days)` + periodic purge task

Plan review fix #17. Method:

```python
def purge_old_sent(self, days: int = 7) -> int:
    cutoff = time.time() - days * 86400
    with self._conn:
        cur = self._conn.execute(
            "DELETE FROM outbox WHERE status='sent' AND sent_at < ?", (cutoff,)
        )
    return cur.rowcount
```

Scheduled via the flusher: once every N iterations (e.g. every 600s), the flusher's loop calls `self._outbox.purge_old_sent(days=7)`. Keeps the outbox small without a separate timer unit. Test: insert 20 `sent` rows with old `sent_at`, call purge, assert row count drops.

#### Task M0.15.7: Flusher with sync client + tick gauge

**Files:**
- Create: `app/mempalace/flusher.py`
- Create: `app/mempalace/tests/test_flusher.py`

Plan review fix #3: `SyncSidecarClient`, not `asyncio.run(AsyncClient.post_json(...))`.
Plan review fix #9: `flusher_tick()` goes to the Prometheus gauge, not a private attr.

```python
# app/mempalace/flusher.py
import threading
import time


class Flusher(threading.Thread):
    daemon = True

    def __init__(self, outbox, client, metrics, *, poll_interval_s: float = 1.0,
                 batch_size: int = 20, purge_interval_loops: int = 600):
        super().__init__(name="mempalace-flusher")
        self._outbox = outbox
        self._client = client           # SyncSidecarClient, not async
        self._metrics = metrics
        self._poll = poll_interval_s
        self._batch = batch_size
        self._purge_every = purge_interval_loops
        self._stop = threading.Event()
        self._loops = 0

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            # Plan review fix #9: real Prometheus gauge, every iteration, idle included.
            self._metrics.flusher_tick()
            try:
                batch_t0 = time.monotonic()
                rows = self._outbox.claim_batch(self._batch)
                for row in rows:
                    try:
                        self._send_one(row)
                        self._outbox.mark_sent([row["id"]])
                    except Exception as e:
                        self._outbox.mark_failed(row["id"], str(e)[:500])
                if rows:
                    self._metrics.flush_batch(time.monotonic() - batch_t0)
                self._loops += 1
                if self._loops % self._purge_every == 0:
                    try:
                        self._outbox.purge_old_sent(days=7)
                    except Exception:
                        pass
            except Exception:
                pass
            if self._stop.wait(self._poll):
                break

    def _send_one(self, row: dict):
        body = {
            "turn_id": row["turn_id"],
            "session_id": row["session_id"],
            "pair_id": row["pair_id"],
            "role": row["role"],
            "text": row["text"],
            "ts": row["ts"],
        }
        headers = {"Idempotency-Key": row["turn_id"]}
        if row.get("request_id"):
            headers["X-Request-ID"] = row["request_id"]
        self._client.post_json(f"/palace/{row['user_id']}/ingest", body, headers=headers)
```

Tests:
- `test_empty_outbox_still_ticks_gauge` — idle loop, assert gauge updated.
- `test_single_row_flushes_and_marks_sent`.
- `test_sidecar_5xx_marks_pending_attempts_incremented`.
- `test_stop_returns_within_bound` — flusher stops cleanly with no in-flight hangs.
- `test_purge_runs_every_n_loops` — monkeypatch `_outbox.purge_old_sent` to count calls; run loop enough iterations, assert called exactly as expected.

#### Task M0.15.8: Hypothesis invariant test on outbox

**Files:**
- Create: `app/mempalace/tests/test_outbox_property.py`

Property:

```python
from hypothesis import given, strategies as st, settings as hs

@hs(max_examples=30, deadline=None)
@given(
    ops=st.lists(
        st.one_of(
            st.tuples(st.just("insert"), st.text(min_size=1, max_size=3, alphabet="abc"), st.text(min_size=1, max_size=4)),
            st.just(("flush_success",)),
            st.just(("flush_5xx",)),
            st.just(("crash_restart",)),
        ),
        min_size=0, max_size=20,
    )
)
def test_outbox_never_loses_or_dupes(ops, tmp_path):
    """
    Invariant: for any interleaving of inserts, flushes, failures, and restarts,
    the set of distinct (user_id, text) pairs in the final palace state equals
    the set of distinct inserts, and the count matches (no duplicates).
    """
    # Simulated palace is a set of seen turn_ids + a dict keyed by turn_id.
    # Flush_success writes to simulated palace (idempotent on turn_id).
    # Flush_5xx fails all pending.
    # Crash_restart drops in-flight state; pending stays pending.
    ...  # full harness in test file
```

---

### M0.16 — Flask: redaction

Four tasks, one per rule family (API keys / credit cards / JWT / SSN). Each task: add rules to `redaction_rules.yaml`, expand `redaction/tests/redaction_corpus.yaml`, add unit tests with positive and negative cases, implement in `redaction/rules.py`, green tests, commit.

`redaction_rules.yaml` excerpt:

```yaml
rules:
  - name: openai_key
    pattern: "sk-[A-Za-z0-9]{20,}"
    replace: "[REDACTED:openai_key]"
  - name: anthropic_key
    pattern: "sk-ant-[A-Za-z0-9_-]{20,}"
    replace: "[REDACTED:anthropic_key]"
  - name: aws_access_key_id
    pattern: "AKIA[0-9A-Z]{16}"
    replace: "[REDACTED:aws_access_key_id]"
  - name: google_api_key
    pattern: "AIza[0-9A-Za-z_-]{35}"
    replace: "[REDACTED:google_api_key]"
  - name: slack_bot_token
    pattern: "xoxb-[0-9A-Za-z-]{10,}"
    replace: "[REDACTED:slack_bot_token]"
  - name: github_pat
    pattern: "ghp_[A-Za-z0-9]{20,}"
    replace: "[REDACTED:github_pat]"
  - name: jwt
    pattern: "eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}"
    replace: "[REDACTED:jwt]"
  - name: ssn_us
    pattern: "(?<!\\d)\\d{3}-\\d{2}-\\d{4}(?!\\d)"
    replace: "[REDACTED:ssn]"
  - name: credit_card_luhn
    # Candidate pattern; rules.py applies Luhn check before redacting.
    pattern: "(?<!\\d)\\d{13,19}(?!\\d)"
    replace: "[REDACTED:cc]"
    validator: luhn
```

`rules.py`:

```python
import re
import yaml
from pathlib import Path


def _luhn(s: str) -> bool:
    digits = [int(d) for d in s if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


VALIDATORS = {"luhn": _luhn}


def load_rules(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())["rules"]


def scrub(text: str, rules: list[dict], metrics=None) -> str:
    out = text
    for rule in rules:
        pat = re.compile(rule["pattern"])
        validator = VALIDATORS.get(rule.get("validator"))
        def _replace(m):
            if validator and not validator(m.group(0)):
                return m.group(0)
            if metrics is not None:
                metrics.redaction_hit(rule["name"])
            return rule["replace"]
        out = pat.sub(_replace, out)
    return out
```

Corpus test:

```python
# app/mempalace/redaction/tests/test_rules.py
import pytest
import yaml
from pathlib import Path
from app.mempalace.redaction.rules import scrub, load_rules

RULES = load_rules(Path(__file__).parents[1] / "redaction_rules.yaml")
CORPUS = yaml.safe_load((Path(__file__).parent / "redaction_corpus.yaml").read_text())

@pytest.mark.parametrize("case", CORPUS["positives"])
def test_positives_redacted(case):
    out = scrub(case["text"], RULES)
    assert case["redaction_marker"] in out

@pytest.mark.parametrize("case", CORPUS["negatives"])
def test_negatives_preserved(case):
    out = scrub(case["text"], RULES)
    assert out == case["text"]
```

`redaction_corpus.yaml` ships with at least 20 positives and 20 negatives.

`redaction/README.md` spells out the four-step feedback loop:

```markdown
# Redaction Feedback Loop

When a leaked secret is found in a palace:

1. **Collect** — anonymize the leaked value; add to `tests/redaction_corpus.yaml` under `positives`.
2. **Rule** — add or update a regex in `redaction_rules.yaml`.
3. **Test** — verify the positive case redacts AND that every existing negative remains intact.
4. **Ship** — green CI → merge. Metric `xelasphere_mempalace_redaction_hits_total{rule="<new>"}` should begin incrementing on real traffic.
```

---

### M0.17 — Flask: adapter + wake-up formatters + shadow sink + tool

#### Task M0.17.1: `shadow_sink.py` — thread-safe queue + daemon writer

**Files:**
- Create: `app/mempalace/shadow_sink.py`
- Create: `app/mempalace/tests/test_shadow_sink.py`

```python
# app/mempalace/shadow_sink.py
import gzip
import json
import os
import queue
import threading
import time
from pathlib import Path


class ShadowSink:
    """Thread-safe, non-blocking shadow-log writer.

    Called from sync gunicorn workers via put_nowait. A daemon thread drains to disk.
    Overflow (queue full) is dropped with a metric increment; request path is never blocked.
    """

    def __init__(self, *, log_dir: Path, max_queue: int = 10_000, drop_metric=None):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="mempalace-shadow-writer", daemon=True)
        self._t.start()
        self._drop_metric = drop_metric
        self._fh = None
        self._cur_day = None

    def enqueue(self, record: dict) -> None:
        try:
            self._q.put_nowait(record)
        except queue.Full:
            if self._drop_metric is not None:
                self._drop_metric.inc()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._t.join(timeout=timeout)
        if self._fh:
            self._fh.close()

    def _run(self) -> None:
        while not self._stop.is_set() or not self._q.empty():
            try:
                rec = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._rotate_if_needed()
            self._fh.write(json.dumps(rec).encode() + b"\n")
            self._fh.flush()

    def _rotate_if_needed(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if self._cur_day != today:
            if self._fh:
                self._fh.close()
            path = self._log_dir / f"shadow-{today}.jsonl"
            self._fh = open(path, "ab")
            self._cur_day = today
```

Tests: enqueue 1000 records rapidly, stop, assert file has 1000 lines; fill the queue beyond cap, assert drops counted; restart (new sink) writes to same day's file (append mode).

#### Task M0.17.2: Wake-up formatters

**Files:**
- Create: `app/mempalace/wakeup.py`
- Create: `app/mempalace/tests/test_wakeup_formatters.py`

```python
# app/mempalace/wakeup.py
from typing import Any


def _render_drawer(d: dict) -> str:
    ts = d.get("ts", "?")
    wing = d.get("wing", "")
    room = d.get("room", "")
    text = d.get("text", "")
    prefix = f"[{ts}"
    if wing:
        prefix += f" · {wing}"
    if room:
        prefix += f"/{room}"
    prefix += "]"
    return f"{prefix} {text}"


def to_anthropic_system(ctx: dict) -> str:
    lines = ["# Memory context (verbatim)"]
    for label, key in (("Recent", "l0_recent"), ("Session", "l1_session"), ("Topic", "l2_topic"), ("Semantic", "l3_semantic")):
        entries = ctx.get(key) or []
        if not entries:
            continue
        lines.append(f"## {label}")
        for d in entries:
            lines.append(_render_drawer(d))
    return "\n".join(lines)


def to_openai_system_msg(ctx: dict) -> dict:
    return {"role": "system", "content": to_anthropic_system(ctx)}


def to_gemini_contents(ctx: dict) -> list[dict]:
    return [{"role": "user", "parts": [{"text": to_anthropic_system(ctx)}]}]


def to_local_prompt_block(ctx: dict) -> str:
    return to_anthropic_system(ctx)
```

Tests assert structural properties (not byte-exact):

```python
def test_anthropic_includes_all_layers_with_entries(sample_ctx):
    out = to_anthropic_system(sample_ctx)
    assert "## Recent" in out
    assert "## Session" in out
    assert "## Topic" in out
    assert "## Semantic" in out

def test_ts_monotonically_decreasing_within_l0(sample_ctx):
    # Assert that the fixture itself satisfies the invariant the formatter preserves.
    tss = [d["ts"] for d in sample_ctx["l0_recent"]]
    assert tss == sorted(tss, reverse=True)
    # And confirm the rendered output reflects that ordering — rendered lines for L0
    # appear in the same order as the fixture entries.
    out = to_anthropic_system(sample_ctx)
    recent_start = out.index("## Recent")
    recent_end = out.index("## Session", recent_start)
    recent_block = out[recent_start:recent_end]
    rendered_tss = [float(line.split()[0].lstrip("[")) for line in recent_block.splitlines()
                    if line.startswith("[")]
    assert rendered_tss == sorted(rendered_tss, reverse=True)

def test_empty_layer_omitted(empty_layers_ctx):
    out = to_anthropic_system(empty_layers_ctx)
    assert "## Topic" not in out  # empty

def test_pair_id_present_on_all_drawers(sample_ctx):
    for layer in ("l0_recent", "l1_session", "l2_topic", "l3_semantic"):
        for d in sample_ctx.get(layer) or []:
            assert "pair_id" in d
```

#### Task M0.17.3: `tool.py` — `search_memory` tool

```python
# app/mempalace/tool.py
from typing import Any


TOOL_SCHEMA = {
    "name": "search_memory",
    "description": "Search the user's long-term memory palace. Returns verbatim drawers.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "wing": {"type": "string", "description": "Optional wing to scope search."},
            "k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}


async def handle_search_memory(adapter, user_id: str, args: dict) -> dict:
    try:
        drawers = await adapter.search(
            user_id=user_id,
            query=args["query"],
            wing=args.get("wing"),
            k=int(args.get("k", 5)),
        )
        return {"results": [d.model_dump() if hasattr(d, "model_dump") else d for d in drawers]}
    except Exception:
        # Indistinguishable from a genuine empty search. Never "unavailable."
        return {"results": [], "note": "no results"}
```

Tests: the tool stays registered in `off` mode and still returns the empty payload (this is the critical no-regression test from spec §3).

#### Task M0.17.4: `adapter.py` — the stable public surface

```python
# app/mempalace/adapter.py
import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from app.mempalace.client import SidecarClient
from app.mempalace.outbox import Outbox
from app.mempalace.shadow_sink import ShadowSink
from app.mempalace.feature_flag import mode, MempalaceMode
from app.mempalace.redaction.rules import scrub, load_rules


@dataclass
class DeletionReceipt:
    status: str
    bytes_removed: int


class MempalaceAdapter:
    def __init__(self, *, client: SidecarClient, outbox: Outbox, shadow_sink: ShadowSink, redaction_rules: list[dict], metrics=None):
        self._client = client
        self._outbox = outbox
        self._shadow = shadow_sink
        self._redact = redaction_rules
        self._metrics = metrics

    async def wake_up(self, user_id: str, query: str, session_id: str | None = None):
        if mode() == MempalaceMode.OFF:
            return None
        try:
            return await self._client.post_json(
                f"/palace/{user_id}/wake-up",
                {"query": query, "k": 5},
            )
        except Exception:
            return None

    async def search(self, user_id: str, query: str, wing: str | None = None, k: int = 5):
        if mode() == MempalaceMode.OFF:
            return []
        try:
            return await self._client.post_json(
                f"/palace/{user_id}/search",
                {"query": query, "wing": wing, "k": k},
            )
        except Exception:
            return []

    def record_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_id: str,
        pair_id: str,
        role: str,
        text: str,
        attachments: list[dict] | None = None,
        request_id: str | None = None,
        ts: float | None = None,
    ) -> None:
        if mode() == MempalaceMode.OFF:
            # Mode-off drop — metric incremented elsewhere
            return
        import time
        if ts is None:
            ts = time.time()
        scrubbed = scrub(text, self._redact, metrics=self._metrics)
        try:
            self._outbox.insert(
                turn_id=turn_id, pair_id=pair_id, user_id=user_id,
                session_id=session_id, role=role, text=scrubbed, ts=ts,
                request_id=request_id,
            )
        except Exception:
            # Outbox DB down → log + metric, never propagate
            if self._metrics is not None:
                self._metrics.inserts_dropped("outbox_db_down")

    def log_shadow(self, record: dict) -> None:
        if mode() != MempalaceMode.SHADOW:
            return
        self._shadow.enqueue(record)

    async def delete_palace(self, user_id: str, requested_by: str, reason: str) -> DeletionReceipt:
        # Abandon outstanding outbox rows for this user first so the flusher doesn't
        # resurrect a palace we're about to delete (spec §4.4 two-phase delete).
        try:
            self._outbox.abandon_user(user_id)
        except Exception:
            pass
        # Plan review fix #6: real DELETE, not X-HTTP-Method-Override.
        resp = await self._client.delete_json(
            f"/palace/{user_id}",
            body={"requested_by": requested_by, "reason": reason},
        )
        return DeletionReceipt(status=resp["status"], bytes_removed=resp["bytes_removed"])

    async def list_drawers(self, user_id: str, wing: str | None = None, limit: int = 100, offset: int = 0):
        # Plan review fix #5: GET, not POST.
        # Pagination: offset-based — ChromaDB has no cursor primitive (see spec §5.2).
        params = []
        if wing:
            params.append(f"wing={wing}")
        params.append(f"limit={limit}")
        params.append(f"offset={offset}")
        qs = "&".join(params)
        return await self._client.get_json(f"/palace/{user_id}/drawers?{qs}")
```

Tests: each public method with a fake `SidecarClient` (using `MockTransport`); the documented fallback for each failure mode.

---

### M0.18 — Orchestrator integration (guarded call-sites)

This is where Flask meets the adapter. It requires reading the existing orchestrator carefully; each touch is a small commit.

**Files to modify** (identified by `rg -n 'memory_v2|memory_v3' app/orchestrator/`):
- Every file that imports `memory_v2` or `memory_v3`.

- [ ] **Step 1: Enumerate call sites**

Run:
```
rg -n 'from app.memory_v2|from app.memory_v3|import memory_v2|import memory_v3' app/orchestrator/
```

Paste output into a local note; this list drives one task per file.

- [ ] **Step 2: For each file, wrap the legacy call at the call site**

Template:

```python
# BEFORE
result = memory_v3.retrieve(ctx)

# AFTER
from app.mempalace.feature_flag import legacy_memory_active, mode, MempalaceMode
from app.mempalace import get_adapter  # singleton accessor defined below

legacy = memory_v3.retrieve(ctx) if legacy_memory_active() else None
m_mode = mode()
if m_mode != MempalaceMode.OFF:
    wake = await get_adapter().wake_up(user_id=ctx.user_id, query=ctx.last_message)
else:
    wake = None

if m_mode == MempalaceMode.ACTIVE and wake is not None:
    result = wake   # use new system, ignore legacy
elif m_mode == MempalaceMode.SHADOW:
    get_adapter().log_shadow({
        "ts": time.time(), "user_id": ctx.user_id, "query": ctx.last_message,
        "legacy": legacy, "mempalace": wake,
    })
    result = legacy
else:
    result = legacy
```

- [ ] **Step 3: Pre-model hook — inject formatted wake-up** (if applicable in the orchestrator's model-call path)

Where the orchestrator builds the system prompt (provider-specific), add:

```python
from app.mempalace.wakeup import to_anthropic_system, to_openai_system_msg, to_gemini_contents
from app.mempalace.feature_flag import mode, MempalaceMode

if m_mode == MempalaceMode.ACTIVE and wake is not None:
    if provider == "anthropic":
        system_block = to_anthropic_system(wake) + "\n\n" + system_block
    elif provider == "openai":
        messages.insert(0, to_openai_system_msg(wake))
    elif provider == "gemini":
        contents = to_gemini_contents(wake) + contents
    else:
        system_block = to_local_prompt_block(wake) + "\n\n" + system_block
```

- [ ] **Step 4: Post-turn hook — `record_turn` for both halves of the pair**

```python
import uuid
pair_id = str(uuid.uuid4())   # ORCHESTRATOR owns pair_id generation (spec §3)
get_adapter().record_turn(
    user_id=ctx.user_id, session_id=ctx.session_id,
    turn_id=str(uuid.uuid4()), pair_id=pair_id,
    role="user", text=ctx.last_message,
    request_id=ctx.request_id,
)
# ... llm call ...
get_adapter().record_turn(
    user_id=ctx.user_id, session_id=ctx.session_id,
    turn_id=str(uuid.uuid4()), pair_id=pair_id,
    role="assistant", text=assistant_text,
    request_id=ctx.request_id,
)
```

- [ ] **Step 5: Register `search_memory` in the tool list**

Where the orchestrator builds the tools list:

```python
from app.mempalace.tool import TOOL_SCHEMA as MEMPALACE_TOOL
tools.append(MEMPALACE_TOOL)   # always include — never filter on mode
```

- [ ] **Step 6: Handler dispatch**

Where the orchestrator routes tool calls:

```python
if tool_call["name"] == "search_memory":
    from app.mempalace.tool import handle_search_memory
    result = await handle_search_memory(get_adapter(), ctx.user_id, tool_call["input"])
```

- [ ] **Step 7: `get_adapter()` singleton**

```python
# app/mempalace/__init__.py
from threading import Lock

__version__ = "0.1.0"
_adapter = None
_lock = Lock()

def get_adapter():
    global _adapter
    if _adapter is None:
        with _lock:
            if _adapter is None:
                _adapter = _build_adapter()
    return _adapter

def _build_adapter():
    import os
    from pathlib import Path
    from app.mempalace.client import SidecarClient
    from app.mempalace.outbox import Outbox
    from app.mempalace.shadow_sink import ShadowSink
    from app.mempalace.redaction.rules import load_rules
    from app.mempalace.adapter import MempalaceAdapter

    socket = os.getenv("MEMPALACE_SOCKET", "/run/mempalace/sidecar.sock")
    token = os.getenv("MEMPALACE_TOKEN", "")
    outbox_path = Path(os.getenv("MEMPALACE_OUTBOX_PATH", "/var/lib/xelasphere/outbox.db"))
    shadow_dir = Path(os.getenv("MEMPALACE_SHADOW_DIR", "/var/log/mempalace/shadow"))
    rules = load_rules(Path(__file__).parent / "redaction" / "redaction_rules.yaml")

    from app.mempalace.metrics import build_default_metrics
    metrics = build_default_metrics()
    return MempalaceAdapter(
        client=SidecarClient(socket_path=socket, token=token),
        outbox=Outbox(outbox_path),
        shadow_sink=ShadowSink(log_dir=shadow_dir),
        redaction_rules=rules,
        metrics=metrics,
    )
```

- [ ] **Step 8: The flusher runs as its own systemd unit, not a gunicorn subprocess**

Plan review fix #4. Spec §3 says "single flusher thread in v1." Gunicorn typically has N workers; a flusher per worker would race on the outbox SQLite file even with WAL. Running the flusher as a separate systemd service guarantees exactly one, cleanly fits the spec, and keeps its lifecycle independent from the web tier.

The Flask app therefore does **not** start a flusher thread in `post_worker_init`. Gunicorn workers only do writes (`outbox.insert`) — no background loops. The separate flusher service is defined in M0.19.4 below.

- [ ] **Step 9: Commit each file's modification as its own commit**

One commit per orchestrator file keeps diffs reviewable. Commit message pattern:

```
feat(mempalace): wire adapter into <filename> with mode/flag guards
```

- [ ] **Step 10: Document pair_id orphan tolerance**

Add a note to `docs/ops/mempalace-runbook.md`:

> **Orphan pair_ids are expected.** A pair_id is generated before the LLM call. If the LLM
> call fails or the assistant turn is never written, the palace will contain a user-half
> drawer with a pair_id that has no assistant companion. This is **not** a bug — it's an
> accurate record of the conversation state. Orphans are filterable:
> `SELECT pair_id FROM drawers GROUP BY pair_id HAVING COUNT(*) = 1`. No auto-cleanup.

---

### M0.19 — Systemd + provisioning

#### Task M0.19.1: `mempalace-sidecar.service`

**Files:**
- Create: `deploy/mempalace/mempalace-sidecar.service`
- Create: `deploy/mempalace/sidecar.env.example`
- Create: `deploy/mempalace/mempalace.env.example`

Systemd unit content from spec §7.2. Commit.

#### Task M0.19.2: `provision.sh`

**Files:**
- Create: `deploy/mempalace/provision.sh`

Idempotent; steps from spec §7.3. Uses `set -euo pipefail`. Post-check at step 8 uses the bash syntax corrected in the spec.

#### Task M0.19.3: `mempalace-mode` named script

**Files:**
- Create: `deploy/mempalace/mempalace-mode`

```bash
#!/usr/bin/env bash
set -euo pipefail

MODE_FILE=/etc/xelasphere/mempalace.env
NEW_MODE="${1:-}"

case "$NEW_MODE" in
  off|shadow|active) ;;
  *)
    echo "usage: $0 {off|shadow|active}" >&2
    exit 2
    ;;
esac

tmp=$(mktemp)
grep -v '^MEMPALACE_MODE=' "$MODE_FILE" > "$tmp" || true
echo "MEMPALACE_MODE=$NEW_MODE" >> "$tmp"
install -m 0640 -o root -g xelasphere "$tmp" "$MODE_FILE"
rm -f "$tmp"

echo "[$(date -Is)] mempalace-mode set to $NEW_MODE, restarting xelasphere ..."
systemctl restart xelasphere

# Verify
for i in $(seq 1 10); do
  if curl -sf http://localhost:8080/admin/mempalace-mode 2>/dev/null | grep -q "\"mode\":\"$NEW_MODE\""; then
    echo "verified mode=$NEW_MODE"
    exit 0
  fi
  sleep 1
done
echo "WARN: could not verify mode change via probe; manual check required" >&2
exit 1
```

Also add `/admin/mempalace-mode` endpoint to the Flask app that returns `{"mode": ...}` for the probe. Commit.

#### Task M0.19.4: `mempalace-flusher.service` — single-instance flusher as its own unit

Plan review fix #4. Spec §3 says "single flusher thread in v1." Running it as a separate systemd unit enforces single-instance trivially and keeps its lifecycle independent from the web tier.

**Files:**
- Create: `deploy/mempalace/mempalace-flusher.service`
- Create: `app/mempalace/flusher_main.py` (entrypoint binary)
- Modify: `pyproject.toml` for xelasphere (if packaged) — add `mempalace-flusher = "app.mempalace.flusher_main:run"` script

- [ ] **Step 1: Create the flusher entrypoint**

```python
# app/mempalace/flusher_main.py
import logging
import os
import signal
import sys
from pathlib import Path

from app.mempalace.client import SyncSidecarClient
from app.mempalace.outbox import Outbox
from app.mempalace.flusher import Flusher
from app.mempalace.metrics import build_default_metrics


def run() -> None:
    logging.basicConfig(level=os.getenv("MEMPALACE_LOG_LEVEL", "INFO"))
    log = logging.getLogger("mempalace-flusher")

    socket_path = os.getenv("MEMPALACE_SOCKET", "/run/mempalace/sidecar.sock")
    token = os.environ["MEMPALACE_TOKEN"]
    outbox_path = Path(os.environ["MEMPALACE_OUTBOX_PATH"])

    client = SyncSidecarClient(socket_path=socket_path, token=token)
    outbox = Outbox(outbox_path)
    metrics = build_default_metrics()

    flusher = Flusher(outbox, client, metrics)
    flusher.start()

    def _stop(signum, frame):  # noqa: ARG001
        log.info("SIGTERM/SIGINT received, draining flusher ...")
        flusher.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    flusher.join()  # blocks until stop() set + loop exits
    client.close()
    log.info("flusher exited cleanly")
    sys.exit(0)


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Systemd unit**

```ini
# deploy/mempalace/mempalace-flusher.service
[Unit]
Description=MemPalace outbox flusher for XelaSphere
After=mempalace-sidecar.service
Requires=mempalace-sidecar.service

[Service]
Type=simple
User=xelasphere
Group=mempalace
EnvironmentFile=/etc/xelasphere/mempalace.env
Environment=PROMETHEUS_MULTIPROC_DIR=/var/run/xelasphere/prom
ExecStartPre=/bin/mkdir -p /var/run/xelasphere/prom
ExecStart=/opt/xelasphere/venv/bin/mempalace-flusher
KillSignal=SIGTERM
TimeoutStopSec=15s
Restart=on-failure
RestartSec=2s
# Single instance is enforced by systemd automatically (one PID per unit).

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Add to provisioning**

`deploy/mempalace/provision.sh` should also `systemctl enable --now mempalace-flusher`.

- [ ] **Step 4: Commit**

```bash
git add deploy/mempalace/mempalace-flusher.service app/mempalace/flusher_main.py
git commit -m "feat(mempalace): mempalace-flusher as its own systemd unit (single-instance)"
```

---

### M0.20 — Dashboards (committed JSON)

**Files:**
- Create: `docs/ops/dashboards/mempalace-live.json`
- Create: `docs/ops/dashboards/mempalace-rollout.json`

Panel inventory (author the JSON; each dashboard is a standard Grafana export):

`mempalace-live.json`:
- Ingest rate (counter rate)
- Wake-up latency (hot/cold histogram_quantile p50/p95/p99)
- Outbox depth
- Circuit state
- Cache pressure (bytes vs cap)
- Readyz gauge
- (Collapsed row "Capacity") Top palaces by bytes; outbox age distribution; open-error class trend.

`mempalace-rollout.json`:
- Mode gauge
- Shadow log volume
- 2×2 rubric tally (from `docs/mempalace-shadow/*.md` — rendered as a Grafana-annotations-or-text panel)
- KG daily spend vs cap (with `predict_linear` projection)
- Disk growth.

Commit each dashboard.

---

### M0.21 — Runbook with inline + STUB sections

**Files:**
- Create: `docs/ops/mempalace-runbook.md`
- Create: `docs/ops/logging-privacy.md`

Runbook content matches spec §8.3 exactly. `STUB` entries (R7, R8, R13) reference GitHub issues to file at P0 entry. `logging-privacy.md` states pseudonymous vs. anonymous distinction from spec §8.4.

---

### M0.22 — Shadow-rubric harness

**Files:**
- Create: `tools/mempalace-rubric`
- Create: `docs/mempalace-shadow/README.md`
- Create: `docs/mempalace-shadow/phase-log.md` (empty template)

`tools/mempalace-rubric` is a Python script with `click`:

```python
#!/usr/bin/env python3
"""Weekly 2x2 rubric harness for mempalace vs legacy memory quality."""
import click
import gzip
import json
import random
from pathlib import Path


@click.command()
@click.option("--log-dir", type=click.Path(exists=True), default="/var/log/mempalace/shadow")
@click.option("--sample", default=20)
@click.option("--output", type=click.Path(), default="docs/mempalace-shadow/")
def main(log_dir, sample, output):
    paths = list(Path(log_dir).glob("shadow-*.jsonl*"))
    records = []
    for p in paths:
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt") as f:
            for line in f:
                records.append(json.loads(line))
    if not records:
        click.echo("No shadow records found.")
        return
    picked = random.sample(records, min(sample, len(records)))
    tally = {"both_hit": 0, "mempalace_only": 0, "v3_only": 0, "both_miss": 0}
    rated = 0
    skipped = 0
    for i, r in enumerate(picked, 1):
        click.echo(f"\n--- Sample {i}/{len(picked)} ---")
        click.echo(f"Query: {r.get('query')}")
        click.echo(f"\nLegacy result:\n{json.dumps(r.get('legacy'), indent=2)[:500]}")
        click.echo(f"\nMemPalace result:\n{json.dumps(r.get('mempalace'), indent=2)[:500]}")
        while True:
            ans = click.prompt("Outcome [b=both_hit, m=mempalace_only, v=v3_only, n=both_miss, s=skip]").lower()
            if ans in ("b", "m", "v", "n", "s"):
                break
        if ans == "b":
            tally["both_hit"] += 1; rated += 1
        elif ans == "m":
            tally["mempalace_only"] += 1; rated += 1
        elif ans == "v":
            tally["v3_only"] += 1; rated += 1
        elif ans == "n":
            tally["both_miss"] += 1; rated += 1
        else:
            skipped += 1

    import datetime
    iso = datetime.date.today().isocalendar()
    out = Path(output) / f"{iso.year}-{iso.week:02d}.md"
    out.write_text(
        "# Rubric Week {}-W{:02d}\n\n".format(iso.year, iso.week) +
        f"- Samples presented: {len(picked)}\n"
        f"- Rated: {rated}\n"
        f"- Skipped: {skipped}\n"
        f"- both_hit: {tally['both_hit']}\n"
        f"- mempalace_only: {tally['mempalace_only']}\n"
        f"- v3_only: {tally['v3_only']}\n"
        f"- both_miss: {tally['both_miss']}\n"
        f"- mempalace_only_ge_v3_only: {tally['mempalace_only'] >= tally['v3_only']}\n"
    )
    click.echo(f"\nWrote {out}  (rated={rated}, skipped={skipped})")


if __name__ == "__main__":
    main()
```

Commit. Make executable: `chmod +x tools/mempalace-rubric`.

---

### M0.23 — CI guard for `memory_v2`/`memory_v3` imports

**Files:**
- Create or modify: `.github/workflows/<ci-file>.yml` or equivalent
- Create: `tools/check-legacy-memory-imports.py`

The guard script:

```python
#!/usr/bin/env python3
"""Fail CI if new code imports memory_v2 or memory_v3.

Modes:
  - warn (default during P0–P2): print warnings, exit 0
  - block (P3 entry onward): exit 1 on any hit
"""
import argparse, os, re, sys
from pathlib import Path

PATTERN = re.compile(r"^(from app\.memory_v[23]|import app\.memory_v[23]|from \.memory_v[23]|import memory_v[23])", re.M)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["warn", "block"], default="warn")
    ap.add_argument("paths", nargs="*", default=["app/"])
    args = ap.parse_args()
    hits = []
    for root in args.paths:
        for p in Path(root).rglob("*.py"):
            if "memory_v2" in p.parts or "memory_v3" in p.parts:
                continue  # the modules themselves
            try:
                text = p.read_text()
            except Exception:
                continue
            for m in PATTERN.finditer(text):
                line = text[:m.start()].count("\n") + 1
                hits.append(f"{p}:{line}: {m.group(0)}")
    if hits:
        print("\n".join(hits), file=sys.stderr)
    if args.mode == "block" and hits:
        sys.exit(1)
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

Wire into CI (example GitHub Actions step):

```yaml
- name: Check legacy memory imports (warn)
  run: python tools/check-legacy-memory-imports.py --mode warn
```

At P3 entry, change `--mode warn` to `--mode block`.

---

### M0.24 — Pre-P0 exit checklist

Before handing off to P0, verify:

- [ ] All sidecar unit + integration tests green: `cd mempalace-sidecar && pytest -v`
- [ ] All Flask-side tests green: `pytest app/mempalace -v`
- [ ] Contract snapshot committed and stable.
- [ ] systemd unit file lints: `systemd-analyze verify deploy/mempalace/mempalace-sidecar.service`
- [ ] `provision.sh` runs cleanly on a staging box from a clean state.
- [ ] `mempalace-mode off|shadow|active` round-trips correctly.
- [ ] Dashboards import cleanly into Grafana.
- [ ] Runbook compiles; all STUB entries have issues filed.
- [ ] CI guard wired in `warn` mode.

Commit a CHANGELOG entry summarising Pre-P0 completion.

---

## Milestone P0 — Dark Deploy (3 days)

**Entry check:** M0.24 passes.
**Env:** `MEMPALACE_MODE=off`, `LEGACY_MEMORY=on`.
**Duration:** 3 calendar days.

### Task P0.1 — Deploy sidecar and app

- [ ] Ansible / manual deploy: run `deploy/mempalace/provision.sh` on the target host.
- [ ] Verify `systemctl status mempalace-sidecar` reports `active (running)` and `Active: active (running) since …` with no restart loops.
- [ ] Verify `/readyz` returns `{"ok": true, ...}`. If not, stop and triage via runbook §R2.
- [ ] Deploy updated Flask app with `MEMPALACE_MODE=off` and `LEGACY_MEMORY=on` in `/etc/xelasphere/mempalace.env`.
- [ ] Verify gunicorn picks up the `mempalace` group (from M0.19.2 post-check).
- [ ] Open the Grafana `mempalace-live.json` dashboard; confirm metrics are flowing (sidecar shows uptime, readyz=1).

### Task P0.2 — File runbook STUB issues

- [ ] Open a GitHub issue for each `STUB` entry in `docs/ops/mempalace-runbook.md`:
  - R7: "Expand KG-provider-failover runbook section" — target: P1 entry.
  - R8: "Document circuit-breaker threshold re-tuning after perf measurement" — target: P1 entry.
  - R13: "Expand mode-drift detection runbook" — target: P1 entry.
- [ ] Reference each issue number in the runbook.

### Task P0.3 — Observe for 3 days

Daily checks (short — ~10 minutes):

- [ ] `journalctl -u mempalace-sidecar --since "24 hours ago" | grep -iE 'error|critical|exception' | head`
- [ ] `curl -fsS --unix-socket /run/mempalace/sidecar.sock -H "X-Sidecar-Token: $MEMPALACE_TOKEN" http://sidecar/readyz | jq .`
- [ ] Check `xelasphere_mempalace_*` metrics: all should be at zero or baseline — outbox depth 0, circuit closed.
- [ ] Check for any mempalace-related Python tracebacks in the Flask logs.

### Task P0.4 — P0 exit

Exit criteria: **No mempalace-attributed errors over the full 3-day window.**

- [ ] Write a one-paragraph phase summary to `docs/mempalace-shadow/phase-log.md`:

```markdown
## P0 — Dark Deploy
- Started: YYYY-MM-DD
- Ended: YYYY-MM-DD (+3 days)
- Result: clean / escalated
- Notes: [observations]
- Decision: proceed to P1 / defer
- Approved by: Xela
```

- [ ] If clean: proceed to P1.
- [ ] If escalated: fix in place, restart P0 clock.

---

## Milestone P1 — Shadow Writes (14 days)

**Entry check:** P0 clean.
**Env:** `MEMPALACE_MODE=shadow`, `LEGACY_MEMORY=on`.
**Duration:** 14 calendar days.

### Task P1.1 — Flip to shadow

- [ ] `sudo mempalace-mode shadow` (uses the named script from M0.19.3).
- [ ] Verify `/admin/mempalace-mode` returns `{"mode": "shadow"}`.
- [ ] Grafana: watch `mempalace_ingest_total{status="accepted"}` begin to climb as real traffic arrives.

### Task P1.2 — Validate daily

Day 1, 3, 7, 14:

- [ ] Outbox p95 depth ≤ 1000 rows (Grafana panel).
- [ ] `mempalace_ingest_total{status="error"}` rate < 0.5% of total.
- [ ] `shadow_logs/` dir growth on track (<1 GB/day at current user count; revisit cap if >5 GB).
- [ ] No page-severity alerts fired.

### Task P1.3 — Measure single-user rebuild benchmark

- [ ] Pick one user with representative history (~200 drawers).
- [ ] Run: `mempalace rebuild --user <uid> --from-conversations --dry-run` → record drawer count.
- [ ] Run: `mempalace rebuild --user <uid> --from-conversations` and time it end-to-end.
- [ ] Record: wall time, LLM cost (KG), Chroma write throughput, peak RSS. Extrapolate linearly to fleet size (assume worst case).
- [ ] Write result to `docs/ops/mempalace-runbook.md` → new section "DR budget — measured at P1 (YYYY-MM-DD)".

### Task P1.4 — P1 exit

Exit criteria from spec §7.4. Verify each:

- [ ] Outbox steady ≤ 1k rows p95.
- [ ] Ingest success ≥ 99.5%.
- [ ] Shadow log populated (at least 1k records).
- [ ] Disk growth within forecast.
- [ ] Rebuild benchmark documented.

Append phase-log entry; approver signature; proceed to P2 or defer.

---

## Milestone P2 — Shadow Reads (21 days)

**Entry check:** P1 clean + all exit items checked.
**Env:** `MEMPALACE_MODE=shadow` (unchanged from P1 — the shadow-reads behavior is already live, we're now actively measuring).
**Duration:** 21 calendar days.

### Task P2.1 — Weekly rubric runs

Each week (×3):

- [ ] Run the harness: `tools/mempalace-rubric --sample 20`.
- [ ] Rate 20 random turns; harness writes `docs/mempalace-shadow/YYYY-WW.md`.
- [ ] Record the 2×2 tally in the phase-log.

### Task P2.2 — Monitor shadow log sampling

- [ ] Verify the 20% sampling rate is holding (`shadow_logs/` disk growth ≈ 40 MB/day baseline).
- [ ] If growth exceeds 100 MB/day, investigate (sampling broken, or user count jumped).

### Task P2.3 — P2 exit (quality decision)

Exit criteria (spec §7.4):

- [ ] `mempalace_only_hit ≥ v3_only_hit` for 3 consecutive weeks.
- [ ] `(both_hit + mempalace_only_hit) ≥ 85%` of sampled turns.

- [ ] If both true: document rubric math; append phase-log entry; decision "promote to P3"; approver signature.
- [ ] If not: extend P2 by a week; investigate mis-retrievals by hand; consider rule-tuning in the wake-up layer; repeat.

---

## Milestone P3 — Active, Legacy Standby (60 days)

**Entry check:** P2 exit gate passed + written approval.
**Env:** `MEMPALACE_MODE=active`, `LEGACY_MEMORY=on`.
**Duration:** 60 calendar days.

### Task P3.1 — Flip to active + monitor initial window

- [ ] `sudo mempalace-mode active`.
- [ ] Watch hot-wake-up p95 for 24 hours (Grafana). Target ≤150ms; rollback to `shadow` if sustained above 500ms.
- [ ] Watch ingest error rate and circuit-breaker state.
- [ ] Create `app/memory_v2/DEPRECATED.md` and `app/memory_v3/DEPRECATED.md` with:
  - Deletion target date (P3 entry date + 60 days).
  - Pointer to this plan and the spec.
  - Rollback procedure (`sudo mempalace-mode off` → restart).
  - Grep for remaining callsites.

- [ ] Flip CI guard to block mode:

```yaml
- name: Check legacy memory imports (block)
  run: python tools/check-legacy-memory-imports.py --mode block
```

Commit the CI change.

### Task P3.2 — Day-30 rubric re-run (required for exit)

- [ ] Day 30 of P3: `tools/mempalace-rubric --sample 20`.
- [ ] Record 2×2. Must confirm `mempalace_only_hit ≥ v3_only_hit`.
- [ ] Record in phase-log: "P3 day-30 rubric: ..."
- [ ] If failing: rollback to `shadow` via `mempalace-mode shadow`; root-cause; document; re-evaluate.

### Task P3.3 — Day-55 rubric re-run (required for exit)

Same structure as P3.2 at day 55.

### Task P3.4 — Monthly capacity review

Once per month during P3:

- [ ] Review `mempalace-live.json` Capacity row: top palaces by bytes; cache evictions; cold-wake-up fraction.
- [ ] If cold-wake-up fraction > 5%: raise `MEMPALACE_IDLE_TTL_SEC` to 28800 (8h), reload sidecar. Document.
- [ ] Review runbook for any drift — new alerts without runbook sections? Fix.
- [ ] Review DR budget vs. measured P1 benchmark; update if fleet grew materially.

### Task P3.5 — P3 exit

Exit criteria (spec §7.4):

- [ ] Zero production rollbacks during P3.
- [ ] Hot wake-up p95 ≤ 150ms sustained.
- [ ] Day-30 + day-55 rubric re-runs both confirm mempalace_only ≥ v3_only.
- [ ] Two monthly capacity reviews logged.

- [ ] Written approval committed to `phase-log.md`: proceed to P4.

---

## Milestone P4 — Legacy Removed

**Entry check:** P3 exit passed + written approval.
**Goal:** single reviewable PR.
**Duration:** ~1 day.

### Task P4.1 — The single removal PR

All changes in one branch, one PR.

- [ ] **Delete `app/memory_v2/` and `app/memory_v3/`** — entire directories.
- [ ] **Remove the `LEGACY_MEMORY` flag** from `app/mempalace/feature_flag.py`:
  - Delete `legacy_memory_active()`.
  - Delete its callers (grep for `legacy_memory_active`).
- [ ] **Update `assert_startup_sane()`** — the spec §3 "Post-P4" note requires the both-off refusal be removed. Implementation:

```python
def assert_startup_sane() -> None:
    m = mode()
    if m == MempalaceMode.OFF:
        log.warning(
            "MEMPALACE_MODE=off (post-P4 environment). Memory is disabled globally — "
            "relief-valve mode. Chat will function without long-term memory."
        )
```

No boot refusal. The `XELASPHERE_POST_P4` env var is deleted along with its callers.

- [ ] **Remove call-site guards** across the orchestrator:

`rg -n 'legacy_memory_active|LEGACY_MEMORY|memory_v2|memory_v3' app/`

Each hit gets its guard unwrapped. The `if legacy_memory_active(): ...` blocks collapse to the `else` branch (or, if no else existed because legacy was the only path, the whole block is deleted — the mempalace `wake_up`/`record_turn` replaces it).

- [ ] **Remove `DEPRECATED.md` files** (they have served their purpose).

- [ ] **Remove the CI guard script** (`tools/check-legacy-memory-imports.py`) and the workflow step — there's nothing to guard against any more.

- [ ] **Update the docstring in `app/mempalace/feature_flag.py`** to reflect the simplified contract.

- [ ] **Test suite** — should pass without `memory_v2`/`memory_v3` modules. Run:

```
pytest app/ tests/
```

Any test that imports `memory_v2`/`v3` → delete the test (they tested removed code).

- [ ] **Deploy**:
  1. Merge the PR.
  2. Deploy to staging; run smoke tests (chat with memory injection works; `mempalace-mode off` boots cleanly).
  3. Deploy to production.
  4. Watch Grafana for 24 hours.

- [ ] **Phase-log close-out**:

```markdown
## P4 — Legacy Removed
- Ended: YYYY-MM-DD
- PR: #NNN
- Lines removed: XXXX
- Approver: Xela
- Notes: mempalace is now the sole memory system. `mempalace-mode off` is the
  universal relief valve and now boots cleanly with a WARN.
```

---

## Global rollback procedures (reference)

From the spec, usable at any phase:

- **Fast rollback (mode flip):** `sudo mempalace-mode off` (pre-P4) or `sudo mempalace-mode shadow` (post-P4 if shadow makes sense) — restarts xelasphere, memory degrades cleanly, chat keeps working.
- **Sidecar rollback:** `ln -sfn /opt/mempalace-sidecar/venv-<prev-tag> /opt/mempalace-sidecar/venv && systemctl restart mempalace-sidecar`. Previous two wheels are always on disk.
- **Flask app rollback:** standard app deploy rollback (not in scope of this plan).
- **Palace restore:** `restic restore latest --target /var/mempalace/palaces` — documented in runbook §R14.
- **Single user palace rebuild:** `mempalace rebuild --user <uid> --from-conversations`.

---

## Self-review

**Spec coverage.** Walked spec §1–§10 and cross-referenced the plan:

- §1 Problem + goals: covered by plan preamble.
- §2 Architecture: M0.1–M0.3, M0.19.
- §3 Flask components: M0.13–M0.18.
- §4 Data flow: M0.4, M0.15, M0.5 (read paths).
- §5 Sidecar internals: M0.2–M0.11.
- §6 Testing: M0.12 (contract), plus every TDD task's paired test.
- §7 Rollout: M0.19 (artifacts), P0–P4.
- §8 Observability: M0.11 (sidecar metrics + /readyz + route instrumentation), M0.11b (Flask-side metrics module), M0.20 (dashboards), M0.21 (runbook + privacy doc).
- §9 Glossary: not code, no plan task needed.
- §10 Open questions deferred to implementation: P1 task P1.3 measures rebuild cost; P3 task P3.4 tunes idle TTL; KG provider choice is a manual decision captured at P1 runbook update.

No gaps.

**Placeholder scan.** Two acceptable abbreviations remain:
- M0.6, M0.7, M0.8, M0.10 describe template structure for sub-tasks rather than every line of code. The implementation code, test code, and test names are given. These are not placeholders; they are smaller tasks the executor composes from explicit templates.
- M0.18 delegates "enumerate call sites via `rg`" to the executor rather than listing them at plan-authoring time because the orchestrator's file list is authoritative only in the repo at execution time. This is an explicit instruction ("paste output into a local note"), not a TBD.

No "TODO/TBD/implement later/add error handling" placeholders.

**Type consistency.** Cross-checked method signatures end-to-end:
- `wake_up(user_id, query, session_id=None)` → returns `WakeupContext | None` everywhere.
- `record_turn(user_id, session_id, turn_id, pair_id, role, text, attachments=None, request_id=None, ts=None)` — consistent in adapter, outbox, flusher, orchestrator integration.
- `SidecarClient.post_json(path, body, headers=None)`, `.get_json(path, headers=None)`, `.delete_json(path, body=None, headers=None)` — consistent across all callers. `SyncSidecarClient.post_json(path, body, headers=None)` has the same shape, sync variant.
- `PalaceRegistry.open(user_id, *, track_cold=False)` — returns `PalaceEntry` by default, `(PalaceEntry, was_cold)` when `track_cold=True`.
- `Outbox` sync methods: `insert`, `depth`, `claim_batch`, `mark_sent`, `mark_failed`, `abandon_user`, `drain_then_abandon`, `purge_old_sent`. Consistent with `Metrics` method naming (`inserts_dropped(reason)`, `redaction_hit(rule)`, `flusher_tick()`).
- `Drawer`/`WakeupContext` model fields match between `schemas.py` (sidecar) and adapter consumer (Flask).

No mismatches found.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-20-mempalace-integration.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task (one sub-milestone at a time, e.g., "execute all of M0.3"), review between tasks, fast iteration. Best fit for this plan's scale — the sub-milestones are naturally isolated units.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints for review. Best if you want every step surfaced in this chat; longer but fully visible.

**Which approach?**
