# Structure Intelligence v2 — Design Spec

**Date:** 2026-04-15
**Branch:** `feat/structure-intelligence-v2`
**Author:** Xela Uvas + Claude (Opus 4.6)
**Status:** Design — not yet approved for implementation
**Supersedes:** Structure Intelligence v1 (spec: implicit; branch `feat/structure-intelligence-v1`, merged to history but not to `main`)

---

## 1. Context — why v2

v1 shipped a pipeline that plans response structure pre-stream and emits it via SSE before the first content token. It works for the plumbing (server decides, client receives, CSS reacts). It fails on enforcement: the generation model is asked to follow a markdown rule list, and it complies ~60% of the time. The v1.1 patch added skeleton-based instructions which lifted compliance but kept the architecture: prose prompt → prose output, hope for the best.

v2 changes the substrate. Instead of shipping formatting rules as text to a prose-generating model, v2 asks the model for **structured JSON** and renders that to markdown client-side. Compliance becomes an API guarantee instead of a probability. The scaffold planner from v1 is kept; the serializer and the generation contract are replaced.

v2 also closes the feedback loop. v1 had no way to measure whether output respected the scaffold. v2 adds a compliance audit that parses every completed stream and logs conformance per layout. This is the data that makes every future scaffold change measurable rather than subjective.

Finally, v2 formalizes evaluation. v1's 14-prompt accuracy test is a toy. v2 ships a 200-prompt eval harness that gates classifier changes: if layout accuracy drops on the eval set, the change doesn't ship.

---

## 2. Scope

### In scope (v2.0)

| Component | Spec section |
|-----------|--------------|
| 1 — Structured JSON output with markdown rendering | §4 |
| 2 — Compliance audit loop | §5 |
| 3 — Eval harness (≥200 prompts) | §6 |

### Explicitly deferred

| Deferred to | Item |
|-------------|------|
| v2.1 | User mode prefix (`/explain`, `/compare`, `/code`) |
| v2.1 | Temperature per layout |
| v2.1 | Vocabulary expansion beyond current 7 layouts |
| v2.2 | Signal layer replacement with a trained classifier |
| v2.2 | Zone 1 system prompt duplication fix (architectural — own spec) |
| v3 | Per-user scaffold learning |

v2 does **not** introduce new layout vocabulary, new modes, new user-visible controls, or touch the existing prompt-building pipeline beyond the scaffold serializer.

---

## 3. What v2 inherits from v1

Kept on the v2 branch (carried forward):

- `Scaffold` dataclass + `ALLOWED_LAYOUTS`, `ALLOWED_OPENINGS`, `ALLOWED_EMPHASIS`, `ALLOWED_WIDTHS` constants
- `_scaffold_from_task_type()` fallback
- `_TASK_TYPE_SCAFFOLD_DEFAULTS` map
- `tests/services/test_scaffold_css_vocabulary.py` — layout-vs-CSS drift guard
- `tests/services/conftest.py` — circular-import stub

Explicitly **not** carried forward (will be redesigned):

- `scaffold_to_instruction()` — v2 produces JSON schema, not markdown rules
- `analyze_prompt_signals()` + `_apply_signal_override()` — v2 keeps the LLM classifier but replaces signal-layer regex with richer prompt signals inside the classifier itself
- Unified classifier prompt body (lines ~705-736 on v1) — redesigned for JSON-schema emission
- SSE `scaffold` event handler — kept in spirit but payload shape changes
- Client-side `data-ctx` application — retained, same contract

---

## 4. Component 1 — Structured JSON output

### 4.1 Problem

v1.1 tells the model: "fill this markdown template verbatim." Models comply ~80-95% for simple templates. For rich explainers with 3+ sections and paragraph constraints, compliance drops below 70%, and failures are silent: you get prose masquerading as a template or two versions emitted back-to-back.

The root issue is that markdown is a freeform output. The model has full creative license on whitespace, header levels, paragraph breaks, and ordering. The instruction is advisory; the output is unconstrained.

### 4.2 Solution

For any scaffold whose `layout` is in the **structured set** (defined below), the generation call switches from free-form content to a structured-output call. The model returns JSON conforming to a strict schema. The client renders that JSON to markdown.

**Structured set (v2.0):** `structured`, `comparison`-shaped prompts that got routed to `structured`, and any layout where `scaffold.sections` is non-empty.

**Unstructured set (v2.0):** `conversational`, `prose`, `long-prose`, `technical`, `code-heavy`, `list-heavy` when `scaffold.sections` is empty. These stay on the current free-form path with the v1.1 rule-list contract.

### 4.3 JSON schema for structured output

```json
{
  "opening": "string — 1-3 sentences framing the topic, may be empty if scaffold.opening == 'none'",
  "sections": [
    {
      "title": "string — matches scaffold.sections[i].title exactly",
      "emoji": "string — matches scaffold.sections[i].emoji (may be empty)",
      "body": "string — markdown-safe prose, bullets, or steps per section.style",
      "style": "enum: prose | list | steps"
    }
  ],
  "closing": "string — optional, 1-2 sentence wrap-up, may be empty"
}
```

Constraints enforced by the schema:
- `sections` has `len == scaffold.sections` (same count, same order, same titles).
- Each `section.body` is a non-empty string.
- `opening` and `closing` may be empty strings.

The schema lives in `app/services/scaffold_schema.py`:

```python
STRUCTURED_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["opening", "sections", "closing"],
    "properties": {
        "opening": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "body", "style"],
                "properties": {
                    "title": {"type": "string"},
                    "emoji": {"type": "string"},
                    "body":  {"type": "string"},
                    "style": {"enum": ["prose", "list", "steps"]},
                },
            },
        },
        "closing": {"type": "string"},
    },
}
```

### 4.4 Provider support

| Provider | Mechanism | Status |
|----------|-----------|--------|
| OpenAI (`gpt-4.1`, `gpt-4.1-mini`, `gpt-4o`) | `response_format: {type: "json_schema", json_schema: {...}}` | Native, enforced at decode |
| OpenAI (`o1`, `o4-mini-deep-research`) | JSON mode + post-parse validation | Reasoning models — fall back to JSON mode |
| Groq (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`) | `response_format: {type: "json_object"}` + schema in prompt | Semi-native — validates shape, not every constraint |
| Groq (`qwen3-32b`) | Same as above | Semi-native |
| Moonshot (`kimi-k2.5`, `kimi-k2-thinking-turbo`) | JSON mode + in-prompt schema | Semi-native |
| DeepSeek (`deepseek-reasoner`, `deepseek-chat`) | JSON mode | Semi-native — schema in prompt |
| Cerebras (`qwen-3-235b`, `gpt-oss-120b`) | JSON mode | Semi-native |
| Gemini | `response_mime_type: "application/json"` + `response_schema` | Native |
| Ollama (local) | JSON mode via prompt | Best-effort |

v2 routing logic:

1. If the resolved model supports **native json_schema enforcement** (OpenAI gpt-4.1 family, Gemini), use it. Compliance ≥ 99%.
2. If the model supports **JSON mode** (Groq, DeepSeek, Moonshot, Cerebras, Ollama), use JSON mode + inject the schema into the system prompt. Validate the returned JSON against the schema; on validation failure, retry once with stricter wording; on second failure, fall through to step 3.
3. **Fallback:** pass through to the current v1.1 markdown skeleton path. Log the fallback event. User-visible behavior is v1.1-identical. No regression.

The provider → mechanism mapping lives in `app/services/scaffold_schema.py:PROVIDER_JSON_MODE`.

### 4.5 Generation call

New function: `app/services/structured_generator.py:stream_structured_response(prompt, model, scaffold, system_prompt_override)`.

Responsibilities:
- Determine JSON mode capability from `model_catalog`.
- Build the JSON-schema-augmented system prompt.
- Call the provider with the appropriate `response_format`.
- Stream the response. Because JSON mode buffers until the object closes on most providers, the function yields one `{type: "structured", data: <parsed JSON>}` chunk at the end, not token-by-token content.
- On any failure (schema violation, timeout, provider doesn't support JSON mode), yield `{type: "fallback_markdown"}` so the caller can switch to the v1.1 path.

### 4.6 Client-side rendering

New module: `app/static/js/scaffoldRenderer.js`.

Exports `renderStructuredResponse(data, targetEl)` where `data` matches the schema in §4.3.

Rendering rules:
- `opening` → one `<p>` (or empty if blank).
- If the scaffold had a separator, render `<hr>` after opening.
- For each `section`:
  - `<h2>{emoji} {title}</h2>` (emoji skipped if blank).
  - If `style == "prose"`: split `body` on blank lines into `<p>`.
  - If `style == "list"`: parse `body` as markdown list, render `<ul><li>`.
  - If `style == "steps"`: parse as numbered list, render `<ol><li>`.
- `closing` → one `<p>`.

The existing markdown-rendering pipeline (`codeWindow.js` streaming renderer) is retained for the unstructured path and for the fallback. `scaffoldRenderer.js` is additive.

### 4.7 SSE event flow (v2)

Server yields, in order:
1. `event: scaffold` (unchanged from v1) — tells client the layout.
2. `event: render_mode` with `{mode: "structured" | "markdown"}` — tells client which renderer to use.
3. For `structured`: one `event: structured` payload with the parsed JSON at the end of generation (no token streaming for structured path — JSON mode is buffered anyway).
4. For `markdown`: `event: token` chunks as today.
5. `event: done` (unchanged).

### 4.8 Fallback chain

```
1. Structured layout?  ────no──→ markdown path (v1.1 rule list)
       │ yes
       ▼
2. Model supports native json_schema?  ──yes──→ call with schema, stream, done.
       │ no
       ▼
3. Model supports JSON mode?  ──no──→ markdown path (v1.1)
       │ yes
       ▼
4. Call with JSON mode + schema in prompt.
   ├── valid JSON matching schema?  ──yes──→ done.
   ├── invalid JSON?  ──retry once──→ valid? done. invalid? → step 5.
   └── schema mismatch?  ──retry once with stricter wording──→ valid? done. invalid? → step 5.

5. Markdown path (v1.1). Log fallback event with reason to compliance audit (§5).
```

Every fallback is logged. Steady-state fallback rate is an observable metric that informs provider-catalog tuning.

### 4.9 File locations

| File | Purpose |
|------|---------|
| `app/services/scaffold_schema.py` | JSON schema + provider-capability map (new) |
| `app/services/structured_generator.py` | `stream_structured_response()` and schema validation (new) |
| `app/blueprints/chat_routes.py` | Route switching on `scaffold.sections` emptiness (modified) |
| `app/static/js/scaffoldRenderer.js` | Client-side JSON → DOM renderer (new) |
| `app/static/js/codeWindow.js` | Dispatch to `scaffoldRenderer` or existing markdown renderer based on `render_mode` SSE event (modified) |

---

## 5. Component 2 — Compliance audit loop

### 5.1 Problem

v1 ships scaffolds and has no idea how often the generation respects them. Every "this looks better" judgment is subjective. Every regression is invisible. Every classifier tweak is faith-based.

### 5.2 Solution

After every stream completes (both structured and markdown paths), a lightweight parser runs server-side on the final content and computes a compliance report against the planned scaffold. The report is logged to a Firestore collection (`scaffold_audits`). There is no user-visible change.

### 5.3 Audit metrics (per response)

| Metric | How measured | Scaffold field compared |
|--------|--------------|-------------------------|
| `sections_planned` | `len(scaffold.sections)` | — |
| `sections_rendered` | Count of `##` headers in final output | `sections` |
| `section_title_match` | For each planned section title, is it present in the rendered headers? (normalized — case-insensitive, whitespace-collapsed) | `sections[].title` |
| `separator_planned` | `scaffold.separator` | `separator` |
| `separator_present` | Does output contain `---` on its own line in the first 30% of content? | `separator` |
| `max_paragraph_sentences_planned` | `scaffold.max_paragraph_sentences` | `max_paragraph_sentences` |
| `paragraph_violations` | Count of paragraphs whose sentence count exceeds the planned max | `max_paragraph_sentences` |
| `layout` | `scaffold.layout` | — |
| `render_path` | `structured` \| `markdown` \| `markdown_fallback` | — |
| `fallback_reason` | `schema_mismatch` \| `json_invalid` \| `provider_unsupported` \| null | — |
| `compliance_rate` | Composite 0.0-1.0 score, weighted: title_match 40%, section_count_match 20%, separator 10%, paragraph_compliance 30% | derived |

### 5.4 Firestore schema

Collection: `scaffold_audits`
Document ID: auto (timestamp-prefixed for range queries)

```json
{
  "ts": "2026-04-15T21:34:12Z",
  "user_id_hash": "sha256(user_id)[:16]",
  "session_id": "session_xxx",
  "model": "deepseek-reasoner",
  "provider": "deepseek",
  "layout": "structured",
  "render_path": "structured",
  "fallback_reason": null,
  "scaffold_planned": { ...full Scaffold.to_dict() },
  "metrics": {
    "sections_planned": 3,
    "sections_rendered": 3,
    "section_title_match": 3,
    "separator_planned": true,
    "separator_present": true,
    "paragraph_violations": 0,
    "compliance_rate": 1.0
  },
  "output_length_chars": 2847,
  "duration_ms": 4121
}
```

### 5.5 Parser

New module: `app/services/scaffold_auditor.py`.

Exports `audit_response(scaffold, rendered_output, meta) -> AuditReport`.

Parser rules (markdown path):
- Sections: `^##\s+.+$` matches via regex; strip leading emoji/whitespace before comparison.
- Paragraphs: split on `\n\s*\n`; count sentences with a simple regex (`\.\s+|\?\s+|!\s+`). Good enough; we're measuring drift, not perfection.
- Separator: look for `^---$` on its own line in the first 30% of the output by character count.

Parser rules (structured path):
- JSON itself guarantees section shape — audit just confirms `len(data.sections) == len(scaffold.sections)` and title match, plus emits paragraph checks on each `section.body`.

The auditor is called from `chat_routes.py` after the final `done` SSE event is yielded. Async-firing (not on request thread) so it doesn't block response close. If audit fails, log `WARNING` and drop — never fail a user request because audit broke.

### 5.6 Observability

v2.0 logs the audit records to Firestore. Dashboard building is out of scope for v2.0 (would require a read path and frontend). In practice the developer inspects via:

```bash
# Aggregate compliance rate by layout over last 24h
gcloud firestore export ... | jq '...'
```

A proper dashboard is deferred to v2.2 once there's ≥1 week of data to visualize.

### 5.7 File locations

| File | Purpose |
|------|---------|
| `app/services/scaffold_auditor.py` | Parser + metric computation (new) |
| `app/blueprints/chat_routes.py` | Fire audit post-stream (modified) |
| `app/firestore_admin.py` | Existing Firestore client — audit writes go here (modified, new method `write_audit()`) |

---

## 6. Component 3 — Eval harness

### 6.1 Problem

v1 has a 14-prompt accuracy test (`tests/services/test_intent_accuracy.py`). It's not enough data to distinguish a real improvement from LLM variance. Today's flake (Groq 401 → intent_type=unknown on 3 prompts) dropped the score 20 percentage points. The test is noise-dominated.

### 6.2 Solution

Ship a curated 200-prompt eval set with expected scaffold layouts. Every change to `classify_user_intent`, prompt wording, provider ordering, or scaffold vocabulary must be gated on this eval. If accuracy drops, the change doesn't ship.

### 6.3 Dataset composition

Target: 200 prompts, distributed across layout classes.

| Source | Count | Provenance |
|--------|-------|------------|
| Existing v1 fixtures | 14 | Kept verbatim |
| Anonymized XelaSphere chat history | 120 | Sampled from Firestore `chat_sessions` collection; user prompts only; PII stripped (names → `{NAME}`, emails → `{EMAIL}`, URLs → `{URL}`) |
| Hand-curated hard cases | 40 | Explainers with 2-5 angles; comparisons; edge cases (single char, very long, emoji-only) |
| Adversarial | 26 | Prompts designed to trip v1 (monolith-prone, ambiguous, multi-intent) |

Target layout distribution (approximate — reflects real chat shape):

| Layout | Target count | Why |
|--------|-------------|-----|
| conversational | 45 | Most common |
| prose | 35 | Common short answers |
| long-prose | 20 | Explainers without named angles |
| technical | 30 | Code, bash, queries |
| code-heavy | 10 | Full-file artifacts |
| list-heavy | 20 | "Top N" questions |
| structured | 40 | Multi-angle explainers, comparisons |

### 6.4 Fixture format

File: `tests/evals/scaffold_v2_fixtures.jsonl`

One prompt per line:

```json
{"id": "explainer-transformer-attention", "prompt": "write a deep explainer on how transformer attention works, including history and intuition", "expected_layout": "structured", "expected_section_count_min": 2, "expected_section_count_max": 5, "tags": ["explainer", "multi-angle", "v1-regression"]}
```

Fields:
- `id` — unique slug, used to track per-prompt regression
- `prompt` — the user text
- `expected_layout` — single allowed layout (string) OR list of acceptable layouts (e.g. `["prose", "long-prose"]`)
- `expected_section_count_min` / `expected_section_count_max` — only when structured
- `tags` — free-form for filtering (e.g. run only `--tag v1-regression`)

### 6.5 Runner

File: `tests/evals/test_scaffold_v2_eval.py`

```python
@pytest.mark.eval
@pytest.mark.skipif(not (os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")),
                    reason="Requires LLM access")
def test_scaffold_layout_accuracy():
    fixtures = load_fixtures()
    results = []
    for fx in fixtures:
        r = classify_user_intent(fx["prompt"], use_cache=False)
        layout = r["scaffold"]["layout"]
        expected = fx["expected_layout"]
        ok = layout in ([expected] if isinstance(expected, str) else expected)
        if ok and "expected_section_count_min" in fx:
            n = len(r["scaffold"]["sections"])
            ok = fx["expected_section_count_min"] <= n <= fx["expected_section_count_max"]
        results.append({"id": fx["id"], "ok": ok, "got": layout, "expected": expected})

    accuracy = sum(r["ok"] for r in results) / len(results)
    save_eval_run(results, accuracy)  # → docs/superpowers/evals/
    assert accuracy >= EVAL_ACCURACY_FLOOR, f"Accuracy {accuracy:.1%} below floor {EVAL_ACCURACY_FLOOR:.1%}"
```

### 6.6 Eval result storage

Directory: `docs/superpowers/evals/`

Each eval run writes:
- `docs/superpowers/evals/{YYYY-MM-DD}-{git_sha}.json` — full per-prompt results
- `docs/superpowers/evals/latest.json` — symlink to most recent run
- `docs/superpowers/evals/history.jsonl` — append-only: `{ts, sha, accuracy, per_layout: {...}}`

This lets us compare runs over time and see which prompts regressed.

### 6.7 CI integration

The eval runs in CI **only on changes to**:
- `app/services/chat_engine.py`
- `app/services/structured_generator.py`
- `app/services/scaffold_schema.py`
- `tests/evals/scaffold_v2_fixtures.jsonl`

On other branches, the eval runs on-demand via `pytest -m eval`.

`EVAL_ACCURACY_FLOOR` starts at the accuracy measured on v2.0 launch (baseline). Future PRs must not drop below baseline. Increases are promoted via commit bumping the constant.

### 6.8 Anonymization pipeline

File: `scripts/build_eval_fixtures.py`

One-shot tool (not a cron) that:
1. Pulls ≤500 user prompts from Firestore `chat_sessions`.
2. Strips PII via regex (names, emails, URLs, phone numbers).
3. De-duplicates near-duplicates (fuzzy match on first 50 chars).
4. Shuffles, truncates to 120.
5. Prints to stdout for manual review before committing.

Human-in-the-loop is required — the script never writes the fixture file directly.

### 6.9 File locations

| File | Purpose |
|------|---------|
| `tests/evals/scaffold_v2_fixtures.jsonl` | 200-prompt dataset (new) |
| `tests/evals/test_scaffold_v2_eval.py` | Pytest runner (new) |
| `tests/evals/__init__.py` | Package marker (new) |
| `docs/superpowers/evals/` | Results directory (new) |
| `scripts/build_eval_fixtures.py` | Anonymization helper (new) |
| `pytest.ini` | Add `eval` marker + opt-in default (modified) |

---

## 7. Test plan

### 7.1 New unit tests

| Test | File | What it verifies |
|------|------|------------------|
| `test_schema_validates_valid_payload` | `tests/services/test_scaffold_schema.py` | Schema accepts well-formed structured response |
| `test_schema_rejects_missing_sections` | same | Schema rejects `sections: []` when scaffold had sections |
| `test_schema_rejects_wrong_title_order` | same | Schema rejects reordered sections |
| `test_provider_capability_map_covers_catalog` | same | Every model in `MODEL_PROVIDERS` has a JSON-mode entry |
| `test_structured_generator_falls_back_on_invalid_json` | `tests/services/test_structured_generator.py` | Mocked provider returns garbage → fallback fires |
| `test_structured_generator_retries_once_on_schema_mismatch` | same | First response wrong shape → retry → success |
| `test_auditor_counts_sections_correctly` | `tests/services/test_scaffold_auditor.py` | Markdown with 3 `##` headers scores `sections_rendered: 3` |
| `test_auditor_detects_separator_in_first_third` | same | `---` at 20% of content passes, at 80% fails |
| `test_auditor_paragraph_violation_count` | same | Output with one 5-sentence paragraph on max=3 → 1 violation |
| `test_auditor_structured_path_uses_json_not_regex` | same | When path=structured, audit uses JSON directly |

### 7.2 Integration tests

| Test | File | What it verifies |
|------|------|------------------|
| `test_structured_sse_flow_end_to_end` | `tests/integration/test_structured_stream.py` | Full flow: scaffold event → render_mode event → structured event → done |
| `test_fallback_sse_flow_when_provider_lacks_json_mode` | same | Model without JSON mode falls through cleanly |
| `test_audit_fires_after_every_stream` | same | Mocked Firestore receives write per completed stream |

### 7.3 Eval tests

| Test | File | What it verifies |
|------|------|------------------|
| `test_scaffold_layout_accuracy` | `tests/evals/test_scaffold_v2_eval.py` | ≥ baseline accuracy on 200-prompt fixture |
| `test_v1_regression_transformer_attention` | same | The v1 failure case classifies structured with ≥2 sections |

### 7.4 Kept from v1

- `tests/services/test_scaffold_css_vocabulary.py` (unchanged)

### 7.5 Retired

Tests that stop applying because their target code is redesigned:
- `test_instruction_for_structured_scaffold_includes_sections`
- `test_instruction_structured_emits_markdown_skeleton_not_rule_list`
- `test_instruction_unstructured_layout_skips_skeleton`
- `test_signals_*` (signal layer replaced — rule list retired)
- `test_classify_signals_override_weak_llm_layout`

These are either ported to v2 equivalents against the new JSON schema path, or deleted as v1-specific.

---

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| JSON mode providers stream slowly (buffer until close) | High | Medium | Accept — structured path gives a single final payload, not tokens. Frontend UX: show streaming skeleton with section placeholders until payload arrives. |
| 200-prompt eval is slow (≥200 × 600ms classifier = 2 min) | Certain | Low | Run eval only on classifier/scaffold changes. Skip by default. |
| Anonymization regex misses a PII case | Medium | High | Human review gate. Never commit auto-generated fixtures without inspection. |
| Firestore audit writes bloat costs | Low | Low | 1 write per chat response, batch per-minute flushes. Estimate: ~$5/mo at 10k DAU. |
| Models disagree on "structured" vs "prose" on borderline prompts | High | Medium | Eval accepts layout lists (`["prose", "long-prose"]`) for ambiguous prompts. |
| v2 breaks existing chat flow during rollout | Medium | High | Feature-flag the structured path via env var `SCAFFOLD_V2_ENABLED`. Default off in prod until eval accuracy ≥ 85%. |

---

## 9. Rollout plan

1. **Week 1:** Ship §4 (structured output) behind `SCAFFOLD_V2_ENABLED=false`. All unit + integration tests pass.
2. **Week 1:** Ship §5 (compliance audit) running on 100% of traffic (no gate — it's observational).
3. **Week 1:** Ship §6 (eval harness) with initial 50 hand-curated prompts. Expand to 200 in week 2 via anonymization script.
4. **Week 2:** Flip `SCAFFOLD_V2_ENABLED=true` for internal accounts only. Monitor audit compliance rate.
5. **Week 2:** If compliance rate ≥ 85% across structured paths, flip feature flag for 10% of users.
6. **Week 3:** If metrics hold, 100%.
7. **Week 4:** Delete `SCAFFOLD_V2_ENABLED` flag and v1.1 markdown skeleton path once audit confirms structured path dominates.

No commits to `main` merge v2 code until the internal-only rollout step passes its acceptance criteria.

---

## 10. Success criteria (what "v2.0 done" looks like)

1. Structured-layout responses hit ≥95% compliance rate in audit logs over a 7-day window.
2. Eval harness runs green at ≥ baseline accuracy (measured on v2.0 day 1).
3. Fallback rate (structured → markdown path) is ≤10% — catches only genuinely unsupported models.
4. The "deep explainer with history and intuition" v1 regression produces a structured response with correct sections on ≥90% of runs across all supported providers.
5. No user-reported regressions on conversational / prose / technical paths (unchanged from v1).

---

## 11. Open questions for review

1. Should the audit write to Firestore, or a cheaper append-only store (GCS JSONL)? Firestore is easier to query; GCS is ~100x cheaper at scale. Current plan is Firestore for velocity; revisit at 100k DAU.
2. Should structured-path streaming show token-by-token progress inside each section, or the "show all at once" behavior JSON mode gives us? v2.0 proposes all-at-once. v2.1 could add per-section token streaming if providers support tool streaming.
3. For the eval, should layout accuracy be graded "exact match" or "close-enough cluster" (e.g. prose / long-prose both acceptable)? Current plan: support list-of-acceptable in fixtures.
4. Do we want the auditor to score output quality (not just shape)? Out of scope for v2.0 — that's an LLM-judge problem and belongs in a separate spec.

---

**End of spec.** Implementation plan follows in a separate document once this is approved.
