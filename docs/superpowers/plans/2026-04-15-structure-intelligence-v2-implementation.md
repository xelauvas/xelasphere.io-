# Structure Intelligence v2 — Implementation Plan

**Date:** 2026-04-15
**Branch:** `feat/structure-intelligence-v2`
**Spec:** `docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md`
**Status:** Ready for execution

---

## Execution principles

- **TDD throughout.** Every work unit writes failing tests first, then the minimum code to make them pass. No exceptions.
- **One commit per work unit.** Commits are atomic, revertible, and name the unit (e.g. `W-02`).
- **Gate on the eval from W-10 onward.** After the eval harness lands, no classifier or scaffold change ships without passing it.
- **Feature flag from day one.** `SCAFFOLD_V2_ENABLED` env var defaults to `false`. Everything behind it until rollout (§9 of spec).
- **Preserve v1.1 path as fallback.** Do not delete the v1.1 markdown skeleton code until §9 rollout step 7.

---

## Phase map and dependency order

```
  Phase 0 (foundation)
      │
      ▼
  Phase 1 (classifier v2) ─────────┐
      │                            │
      ▼                            ▼
  Phase 2 (eval harness) ◄─── validates Phase 1
      │
      ▼
  Phase 3 (structured gen + audit infra) ◄── parallel OK
      │
      ▼
  Phase 4 (wire audit to stream)
      │
      ▼
  Phase 5 (client renderer + SSE)
      │
      ▼
  Phase 6 (integration + flag)
      │
      ▼
  Phase 7 (rollout per spec §9)
```

---

## Phase 0 — Foundation

**Goal:** feature flag in place, new module skeletons exist, test infra ready. No behavior change.

### W-01 — Feature flag + env wiring

**Files:** `app/config.py`, `.env.example`

**Tests:** `tests/services/test_v2_feature_flag.py`
- `test_flag_defaults_false_when_unset`
- `test_flag_reads_env_var`

**Acceptance:**
- `Config.SCAFFOLD_V2_ENABLED` exists, defaults `False`.
- Reading `SCAFFOLD_V2_ENABLED=true` env → `True`.

**Effort:** 15 min (CC).

---

### W-02 — Module skeletons (empty, typed, test-ready)

**Files (new, stubs only):**
- `app/services/scaffold_schema.py` — module docstring + `STRUCTURED_RESPONSE_SCHEMA: Dict = {}` placeholder
- `app/services/structured_generator.py` — module docstring + `def stream_structured_response(...): raise NotImplementedError`
- `app/services/scaffold_auditor.py` — module docstring + `def audit_response(...): raise NotImplementedError`
- `tests/evals/__init__.py` — empty
- `tests/services/test_scaffold_schema.py` — empty (markers only)
- `tests/services/test_structured_generator.py` — empty
- `tests/services/test_scaffold_auditor.py` — empty

**Tests:** `test_imports.py`
- `test_v2_modules_importable` — each module imports without error.

**Acceptance:** imports succeed, pytest collects empty modules without complaint.

**Effort:** 10 min.

---

### W-03 — Pytest `eval` marker + config

**Files:** `pytest.ini`

Add:
```ini
markers =
    eval: slow LLM-backed evaluation tests (run with -m eval)
```

Ensure `-m "not eval"` is default (evals only run opt-in).

**Tests:** `test_pytest_markers.py`
- `test_eval_marker_is_opt_in` — verifies default pytest run does not collect `@pytest.mark.eval` tests.

**Effort:** 10 min.

---

## Phase 1 — Classifier v2

**Goal:** `classify_user_intent()` returns a valid `Scaffold` via JSON-schema-emitting LLM call. No SSE, no audit, no client yet.

### W-04 — JSON schema for classifier response

**Files:** `app/services/scaffold_schema.py`

Define:
```python
CLASSIFIER_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["intent_type", "scaffold"],
    "properties": {
        "intent_type": {"enum": ["financial_query", "creative_task", "question",
                                  "recipe_request", "code_request", "image_request", "general_chat"]},
        "has_crypto_mention": {"type": "boolean"},
        "has_stock_mention": {"type": "boolean"},
        "asset_symbol": {"type": ["string", "null"]},
        "wants_chart": {"type": "boolean"},
        "wants_image": {"type": "boolean"},
        "wants_code": {"type": "boolean"},
        "scaffold": {
            "type": "object",
            "required": ["layout", "opening", "separator", "sections",
                         "emphasis", "max_paragraph_sentences", "list_threshold", "width_hint"],
            "properties": {
                "layout": {"enum": list(ALLOWED_LAYOUTS)},
                "opening": {"enum": list(ALLOWED_OPENINGS)},
                "separator": {"type": "boolean"},
                "sections": {"type": "array", "items": {...}},
                "emphasis": {"enum": list(ALLOWED_EMPHASIS)},
                "max_paragraph_sentences": {"type": "integer", "minimum": 2, "maximum": 4},
                "list_threshold": {"type": "integer", "minimum": 2, "maximum": 4},
                "width_hint": {"enum": list(ALLOWED_WIDTHS)},
            },
        },
    },
}
```

**Tests (failing first):**
- `test_classifier_schema_has_all_allowed_layouts`
- `test_classifier_schema_rejects_unknown_intent`
- `test_classifier_schema_validates_v1_golden_sample` — a known-good v1 response passes schema.

**Acceptance:** schema is importable, self-validates against 3 golden samples.

**Effort:** 25 min.

---

### W-05 — Provider capability map

**Files:** `app/services/scaffold_schema.py`

```python
PROVIDER_JSON_MODE = {
    # Native json_schema enforcement:
    "openai": {"gpt-4.1": "schema", "gpt-4.1-mini": "schema", "gpt-4.1-nano": "schema",
               "gpt-4o": "schema", "gpt-4o-mini": "schema"},
    "gemini": {"*": "schema"},
    # JSON mode + in-prompt schema:
    "groq":      {"*": "json_mode"},
    "deepseek":  {"*": "json_mode"},
    "moonshot":  {"*": "json_mode"},
    "cerebras":  {"*": "json_mode"},
    "ollama":    {"*": "json_mode"},
    # Reasoning models: JSON mode + stricter prompt:
    "openai_reasoning": {"o1": "json_mode", "o4-mini-deep-research": "json_mode"},
}

def get_json_mode(provider: str, model: str) -> str:
    """Returns 'schema' | 'json_mode' | 'unsupported'."""
```

**Tests:**
- `test_every_catalog_model_has_json_mode_entry` — iterates `app.model_catalog.MODEL_PROVIDERS`, asserts coverage.
- `test_get_json_mode_falls_through_to_wildcard`
- `test_get_json_mode_returns_unsupported_for_unknown_provider`

**Acceptance:** 100% of `MODEL_PROVIDERS` mapped.

**Effort:** 20 min.

---

### W-06 — Classifier v2 core — OpenAI native schema path

**Files:** `app/services/chat_engine.py` — new `classify_user_intent()`.

Replace the v1 signature; keep the same return shape. Internally:
1. Build system prompt: `"Classify this user message. Return JSON matching this schema."` + JSON.dumps(schema).
2. Call OpenAI with `response_format={"type": "json_schema", "json_schema": {...}}`.
3. Parse response → validate against schema → build `Scaffold` → return dict with `scaffold` key.

**Tests (mocked provider):**
- `test_classify_v2_returns_valid_scaffold_on_openai_schema_path`
- `test_classify_v2_rejects_response_failing_schema`
- `test_classify_v2_falls_back_to_task_type_on_openai_error`

**Acceptance:** mocked OpenAI → valid scaffold; invalid schema → fallback scaffold; no exceptions leak.

**Effort:** 45 min.

---

### W-07 — Classifier v2 — Groq/DeepSeek JSON-mode path

**Files:** `app/services/chat_engine.py`, `app/services/scaffold_schema.py` (add `build_json_mode_prompt()`).

Extend classifier with fallthrough:
1. Try provider 1 (OpenAI schema).
2. On failure → provider 2 (Groq json_mode) with schema injected into prompt body.
3. On failure → task-type fallback (no override logic; keep simple).

**Tests:**
- `test_classify_v2_groq_json_mode_path`
- `test_classify_v2_schema_validates_groq_output` — Groq JSON mode doesn't guarantee schema, so we validate post-parse.
- `test_classify_v2_both_providers_fail_uses_task_fallback`

**Acceptance:** classifier works on Groq-only key; cross-provider fallback exercised.

**Effort:** 40 min.

---

### W-08 — Classifier cache with TTL and bounded size

**Files:** `app/services/chat_engine.py` — replace v1's unbounded dict.

Use `functools.lru_cache(maxsize=1000)` wrapper + TTL check in call site. Or `cachetools.TTLCache(maxsize=1000, ttl=300)`.

**Tests:**
- `test_cache_evicts_oldest_at_limit`
- `test_cache_expires_entries_after_ttl`
- `test_cache_keyed_by_prompt_exact_string`

**Acceptance:** cache bound and expiring; no memory leak at 10k unique prompts.

**Effort:** 20 min.

---

### W-09 — Wire classifier into existing chat_routes (behind flag)

**Files:** `app/blueprints/chat_routes.py`

When `Config.SCAFFOLD_V2_ENABLED`, call v2 classifier; else skip scaffold entirely (no v1 path on this branch — v1 code was dropped in cherry-pick).

When flag is off: routes work exactly as `main` currently does. No scaffold, no injection.

**Tests:** `tests/integration/test_v2_classifier_wired.py`
- `test_flag_off_no_scaffold_in_system_prompt` — request with flag off, mocked stream, assert system prompt has no scaffold section.
- `test_flag_on_scaffold_logged` — flag on, assert classifier runs and logs layout.

**Acceptance:** behind-flag wiring verified; flag-off is a no-op.

**Effort:** 30 min.

---

## Phase 2 — Eval harness

**Goal:** classifier v2 quality is measurable. Gates everything after this.

### W-10 — Eval fixture file with hand-curated seed

**Files:** `tests/evals/scaffold_v2_fixtures.jsonl`

Initial 54 prompts: 14 v1 fixtures + 40 hand-curated hard cases. No anonymized real-chat data yet (W-13 adds those).

Format per spec §6.4. Every entry has `id`, `prompt`, `expected_layout`, `tags`.

**Tests:** `tests/evals/test_fixtures_well_formed.py`
- `test_every_fixture_has_required_keys`
- `test_expected_layouts_are_in_allowed_set`
- `test_no_duplicate_ids`
- `test_no_duplicate_prompts`

**Acceptance:** fixture loads, shape validates, 54 entries.

**Effort:** 60 min (most of it hand-curating prompts).

---

### W-11 — Eval runner

**Files:** `tests/evals/test_scaffold_v2_eval.py`

Implements the runner per spec §6.5. Reads fixtures, calls `classify_user_intent`, compares against expected. Computes per-layout accuracy. Writes result to `docs/superpowers/evals/{date}-{sha}.json`.

**Tests:**
- `test_eval_runner_skips_when_no_api_keys` — harness respects skipif.
- `test_eval_runner_writes_results_file` — mocked fixtures + mocked classifier → result file exists.
- `test_eval_runner_fails_below_floor` — mocked low-accuracy result → test fails.

**Acceptance:** `pytest -m eval` runs end-to-end on the seed fixtures.

**Effort:** 45 min.

---

### W-12 — Set baseline accuracy floor

**Files:** `tests/evals/test_scaffold_v2_eval.py` — `EVAL_ACCURACY_FLOOR` constant.

**Process:**
1. Run `pytest -m eval tests/evals/` against classifier v2 (post-W-09) three times.
2. Take the lower bound (worst run) as baseline.
3. Set `EVAL_ACCURACY_FLOOR = baseline - 0.03` (3 pp noise tolerance).
4. Commit the number.

**Acceptance:** floor committed, 3 runs all pass floor.

**Effort:** 30 min (mostly waiting for LLM runs).

---

### W-13 — Anonymization script + expanded fixture set

**Files:**
- `scripts/build_eval_fixtures.py` — pulls, anonymizes, prints.
- `tests/evals/scaffold_v2_fixtures.jsonl` — expanded to ≥200 entries.

**Tests:** `tests/services/test_anonymizer.py`
- `test_anonymizer_scrubs_emails`
- `test_anonymizer_scrubs_phone_numbers`
- `test_anonymizer_scrubs_urls`
- `test_anonymizer_scrubs_proper_names_from_seed_list`
- `test_anonymizer_leaves_technical_terms_alone` — "JWT", "OAuth", "Bitcoin" must survive.

**Process:**
1. Write script. Run tests.
2. Run script against Firestore prod data. Dump to stdout.
3. **Human review** (Xela): eyeball 500 lines, reject bad ones, tag expected layouts.
4. Append ≥146 approved entries to fixture JSONL.
5. Re-run eval; floor may shift — retune if needed.

**Acceptance:** fixture at ≥200 entries; eval passes; no PII present on inspection.

**Effort:** 90 min (code) + 60 min (human review).

---

## Phase 3 — Structured generator + audit infra

**Goal:** structured-layout generation produces schema-validated JSON. Audit parser exists and correct. No SSE integration yet.

### W-14 — Structured response schema

**Files:** `app/services/scaffold_schema.py` — add `STRUCTURED_RESPONSE_SCHEMA` per spec §4.3.

**Tests:** `tests/services/test_scaffold_schema.py`
- `test_structured_schema_validates_well_formed`
- `test_structured_schema_rejects_missing_sections`
- `test_structured_schema_rejects_wrong_section_count`
- `test_structured_schema_rejects_invalid_style_enum`

**Acceptance:** schema + 4 tests.

**Effort:** 25 min.

---

### W-15 — `stream_structured_response` — OpenAI native path

**Files:** `app/services/structured_generator.py`

Implements the native-schema call path only (simplest). Returns single `{type: "structured", data: {...}}` chunk when complete.

**Tests:**
- `test_structured_generator_native_schema_path_happy`
- `test_structured_generator_returns_single_structured_chunk`
- `test_structured_generator_raises_on_provider_error`

**Acceptance:** OpenAI-native path works end-to-end against mocked provider.

**Effort:** 45 min.

---

### W-16 — `stream_structured_response` — JSON-mode fallback

**Files:** `app/services/structured_generator.py`

Adds JSON-mode path for Groq/DeepSeek/etc. Parses response, validates against schema, retries once on validation failure with stricter wording.

**Tests:**
- `test_structured_generator_json_mode_path`
- `test_structured_generator_retries_once_on_invalid_json`
- `test_structured_generator_falls_back_to_markdown_on_second_failure`

**Acceptance:** fallback chain per spec §4.8 matches tests.

**Effort:** 60 min.

---

### W-17 — Scaffold auditor — markdown parser

**Files:** `app/services/scaffold_auditor.py`

Implements `audit_response(scaffold, rendered_output, meta)` per spec §5.3.

**Tests:**
- `test_auditor_counts_sections_from_markdown`
- `test_auditor_normalizes_section_titles_for_matching`
- `test_auditor_detects_separator_position`
- `test_auditor_counts_paragraph_violations`
- `test_auditor_computes_compliance_rate_weighted_correctly`
- `test_auditor_handles_empty_output_gracefully`
- `test_auditor_handles_scaffold_with_zero_planned_sections`

**Acceptance:** all metrics computed correctly on synthetic fixtures.

**Effort:** 50 min.

---

### W-18 — Scaffold auditor — structured-path short-circuit

**Files:** `app/services/scaffold_auditor.py`

When `render_path == "structured"`, skip regex parsing. Use JSON directly: count `data["sections"]`, verify title match, apply paragraph rule per `section.body`.

**Tests:**
- `test_auditor_uses_json_for_structured_path`
- `test_auditor_structured_path_skips_regex_parsing`

**Acceptance:** structured path is ~10x faster than markdown path; both produce equivalent `compliance_rate` on matched input.

**Effort:** 25 min.

---

### W-19 — Firestore audit writer

**Files:** `app/firestore_admin.py` — add `write_scaffold_audit(audit_dict)` method.

**Tests:** `tests/services/test_firestore_audit_writer.py` (mocked Firestore client).
- `test_write_audit_serializes_dict_to_firestore`
- `test_write_audit_drops_on_client_error_without_raising`
- `test_write_audit_hashes_user_id_before_storing`

**Acceptance:** writes go through a mocked client; errors swallowed per spec §5.5.

**Effort:** 30 min.

---

## Phase 4 — Wire audit to stream

### W-20 — Post-stream audit fire

**Files:** `app/blueprints/chat_routes.py`

In the `generate()` SSE coroutine, after yielding `done`, fire `audit_response()` asynchronously (thread pool or `concurrent.futures`). Never block the response close.

**Tests:** `tests/integration/test_audit_fires_post_stream.py`
- `test_audit_fires_after_done_event` — mock a full stream, assert audit writer called.
- `test_audit_failure_does_not_break_stream` — audit raises → stream completes cleanly.

**Acceptance:** audit is non-blocking, non-breaking.

**Effort:** 30 min.

---

## Phase 5 — Client renderer + SSE

### W-21 — SSE event: `render_mode`

**Files:** `app/blueprints/chat_routes.py`

Before first content token, emit `event: render_mode` with `{mode: "structured" | "markdown"}` per spec §4.7.

**Tests:** `tests/integration/test_render_mode_sse_event.py`
- `test_render_mode_event_precedes_content`
- `test_render_mode_structured_when_sections_nonempty`
- `test_render_mode_markdown_when_sections_empty`

**Acceptance:** wire-level SSE order verified.

**Effort:** 20 min.

---

### W-22 — SSE event: `structured` payload

**Files:** `app/blueprints/chat_routes.py`

When using structured path, emit single `event: structured` with the parsed JSON before `done`.

**Tests:**
- `test_structured_event_carries_valid_json`
- `test_no_token_events_on_structured_path`

**Acceptance:** structured stream shape matches spec §4.7.

**Effort:** 20 min.

---

### W-23 — `scaffoldRenderer.js` — client-side renderer

**Files:** `app/static/js/scaffoldRenderer.js` (new)

Exports `renderStructuredResponse(data, targetEl)` per spec §4.6.

Pure function, no deps except the existing markdown parser used for `section.body`.

**Tests:** `tests/client/test_scaffold_renderer.js` — use the existing Jest/jsdom setup.
- `test_renders_opening_paragraph`
- `test_renders_section_headers_with_emoji`
- `test_renders_list_style_as_ul`
- `test_renders_steps_style_as_ol`
- `test_handles_empty_opening_and_closing`
- `test_escapes_html_in_body_content`

**Acceptance:** 6 jest tests pass.

**Effort:** 60 min.

---

### W-24 — Wire `scaffoldRenderer` into `codeWindow.js`

**Files:** `app/static/js/codeWindow.js`

Dispatch on `render_mode` SSE event: `structured` → `scaffoldRenderer.renderStructuredResponse()`, `markdown` → existing streaming renderer.

**Tests:**
- `test_codewindow_dispatches_to_scaffold_renderer_on_structured`
- `test_codewindow_falls_back_to_markdown_renderer_on_markdown`

**Acceptance:** both paths render correctly in jsdom.

**Effort:** 40 min.

---

## Phase 6 — Integration + flag-on smoke

### W-25 — End-to-end integration test

**Files:** `tests/integration/test_v2_full_flow.py`

Exercises: classifier → scaffold → structured generator → SSE events → client parse → audit write. All mocked at boundaries (LLM, Firestore). No real API calls.

**Tests:**
- `test_structured_flow_end_to_end`
- `test_markdown_fallback_flow_end_to_end`
- `test_audit_captures_every_flow`

**Acceptance:** all 3 scenarios pass.

**Effort:** 45 min.

---

### W-26 — Internal-only rollout gate

**Files:** `app/blueprints/chat_routes.py`

Add check: if `SCAFFOLD_V2_ENABLED=true` AND `session['user']['email']` in `INTERNAL_EMAILS` list → enable v2. All others get v1.1-equivalent (no scaffold, no audit).

`INTERNAL_EMAILS` is `Config.INTERNAL_EMAILS` — comma-separated env var, defaults to Xela's email only.

**Tests:**
- `test_non_internal_user_bypasses_v2_even_when_flag_on`
- `test_internal_user_gets_v2_when_flag_on`

**Acceptance:** rollout gate works.

**Effort:** 20 min.

---

### W-27 — Manual QA on the 10 v1 test prompts

**Files:** none. Human-driven.

Run the 10 prompts from the v1 QA list (conversational, prose, technical, structured × 2-3 variants each). Verify:
- Correct layout per `[Stream] scaffold injected:` log.
- Structured path actually renders per `scaffoldRenderer`.
- Audit writes land in Firestore (check collection).
- No console errors.
- Markdown fallback works (disable OpenAI key temporarily to force Groq path).

**Acceptance:** all 10 pass; audit records inspected for compliance signal.

**Effort:** 30 min.

---

## Phase 7 — Rollout

Follows spec §9 week-by-week. Not work units — calendar steps.

---

## Deferred (explicitly — don't sneak in during v2.0)

| Item | Deferred to |
|------|-------------|
| User mode prefix (`/explain`, `/compare`) | v2.1 |
| Temperature per layout | v2.1 |
| Vocabulary expansion beyond 7 layouts | v2.1 |
| Replace regex signal layer with trained classifier | v2.2 |
| Zone 1 system-prompt duplication fix | v2.2 (own spec) |
| Per-user scaffold learning | v3 |
| Audit dashboard / visualization frontend | v2.2 (after 1 week of data) |
| Token-by-token streaming inside structured sections | v2.1 |
| LLM-judge output quality scoring | Separate spec |

If any of these start feeling tempting during v2 execution — stop. Add a TODO with spec reference. Move on.

---

## Total effort

| Phase | Work units | Estimated CC time |
|-------|-----------|-------------------|
| 0 | W-01 to W-03 | 35 min |
| 1 | W-04 to W-09 | ~3h 20m |
| 2 | W-10 to W-13 | ~3h (+ 1h human review) |
| 3 | W-14 to W-19 | ~3h 45m |
| 4 | W-20 | 30 min |
| 5 | W-21 to W-24 | ~2h 20m |
| 6 | W-25 to W-27 | ~1h 35m |
| **Total** | **27 units** | **~15 CC hours + 1h human review** |

Calendar: 2-3 focused sessions to ship through Phase 6. Rollout (Phase 7) runs 3-4 weeks.

---

## Exit criteria

v2.0 is "done" when all of these hold:

1. All 27 work units committed, all tests pass.
2. Eval accuracy ≥ `EVAL_ACCURACY_FLOOR` consistently across 3 runs.
3. Manual QA (W-27) green.
4. Audit collection has ≥100 records from internal-only rollout, with ≥85% structured-path compliance rate.
5. No console errors in any test browser (Chrome/Firefox/Safari).
6. Spec §10 success criteria all green for 7 consecutive days.

Then and only then: flip flag to 10%, per spec §9.

---

## Kickoff

Next actionable step on approval: begin **W-01** (feature flag). First commit in ~15 min.
