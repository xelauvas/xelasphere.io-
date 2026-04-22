# Dispatch Template for M-series Implementation Milestones

> **Scope.** This template is for the implementation milestones of the
> MemPalace integration plan — M0.1 through M0.24. It codifies the dispatch
> shape that emerged across M0.1 → M0.6 and the review-response loop that
> fires after each. It does **not** cover the rollout-phase milestones (P0
> through P4). Those are different animals — observation windows, metric
> checks, rubric runs, phase-log entries — and will get their own template
> when P0 actually ships.
>
> **How to use.** When dispatching the next M-series milestone, don't
> re-write the discipline from scratch. Copy the Section-A boilerplate
> verbatim, fill in the Section-B milestone-specific body, and send. Read
> Parts 1 and 3 as the dispatcher (main session / human); they shape
> pre-dispatch and post-dispatch work that the subagent never sees. Part 4
> covers the case where you execute the milestone yourself instead of
> dispatching.

---

## The milestone lifecycle

Every M-series milestone runs through the same loop. The dispatch prompt is
one artifact inside it, not the whole thing:

```
  [Pre-resolve ambiguities]           <- dispatcher (main session / human)
        |
        v
  [Write dispatch prompt] -----+
        |                      |
        v                      |
  [Dispatch implementer]       |      <- subagent (or main session, see Part 4)
        |                      |
        v                      |
  [RED commit -> GREEN commit] |
        |                      |
        v                      |
  [Dispatch review pair in     |      <- two parallel subagents
   parallel: spec + quality]   |
        |                      |
        v                      |
  [Classify findings:          |
   Critical/Important/Minor]   |      <- dispatcher
        |                      |
        v                      |
  [Fold Important into one     |
   review-response commit]     |
        |                      |
        v                      |
  [Minors defer per reviewer   |
   guidance]                   |
        |                      |
        v                      |
  [Clear to next milestone] ---+
```

Each arrow is load-bearing. Skipping the review pair because "I already
know it's right" is where discipline rots. See Part 3 and Part 4 for the
gates.

---

## Part 1 — Pre-dispatch discipline (dispatcher, not subagent)

### Pre-resolve ambiguities before firing

The plan was written before implementation started. Every task sketch
contains assumptions that may be stale by the time you dispatch. Pre-resolve
them — don't let the subagent discover them at runtime.

**The 5-minute discipline.** Before writing the dispatch body:

1. Read the plan's task sketch for this milestone.
2. For every `# <-- SEE DEVIATION #N` marker, every parenthetical `(plan
   predates ...)` note, and every spec cross-reference: open the current
   code for the referenced symbol and verify the assumption still holds.
3. For every new fixture seeding pattern the tests will need: verify the
   seeding path exists and is reachable from a test.
4. **Plan-half consistency check (named at M0.13).** When the plan sketch
   includes BOTH a test body AND an impl body for the same behavior, read
   them together and name the shared semantic they jointly encode. Any
   disagreement — assertion expects X, impl produces Y — surfaces here,
   not at Stage 3 test-run. **Diagnostic model:** plan-verbatim on both
   sides doesn't imply internal consistency. The plan author may have
   drafted the test half and the implementation half at different times,
   with different mental models of what the behavior was supposed to be.
   Each half read in isolation looks correct against its own local
   semantic; it's only reading them together that surfaces the mismatch.
   **The defense mechanism for plan-half inconsistency is this
   pre-resolution cross-check, NOT nastiest-context test design.** Hit #6
   at M0.13 cost a Stage 3 round-trip because pre-resolution read each
   half independently; a 30-second re-read together would have surfaced
   the mismatch at dispatch time. Sibling discipline to B4 (write-the-
   tests) — distinct defense, distinct class of bugs caught. See appendix
   doctrine entry for the class separation.
5. **Parametrized-case-count estimation (named at M0.13).** When tests
   will be parametrized via `@pytest.mark.parametrize`, estimate
   COLLECTED-CASE count, not DECLARED-TEST count. pytest reports each
   parametrized invocation independently — a single test declaration with
   18 parametrized values lands as 18 collected cases. Ladder-rung
   predictions based on declared-test count will be off by (n-1) per
   parametrized test where n is the parametrization count. M0.13 surfaced
   this: predicted "+7 tests" landed as "+24 cases" via one test's 18
   whitespace/empty-string parametrizations.
6. Any gap you find becomes a **pre-declared deviation** in the dispatch
   prompt, with `(a) if X world, do Y; (b) if Z world, do W; (c) if neither,
   defer with a note` decisions already made.
7. **Test-harness configuration review (promoted from Watching appendix at
   M0.15 Stage 4b).** Dispatcher-owned discipline. Primary value is for
   (a) and (c) below — (a) catches collection-time silent failures that
   can masquerade as "tests aren't being collected yet" for extended
   periods; (c) catches ladder-rung prediction errors that shape Stage 1
   reporting. (b) and (d) surface cleanly at Stage 1 RED either way with
   legible errors; pre-resolution value for them is modest. Before Stage
   1 RED, verify:

   (a) **pytest.ini / pyproject.toml test settings align with tests'
   requirements** — strict mode, asyncio mode, plugin config, `testpaths`,
   `python_files` conventions. Test settings govern what pytest collects
   and how; mismatches produce collection-time errors or silent
   no-collection rather than runtime failures.

   (b) **Fixture decorators are correct for each fixture's async/plugin
   context** — `@pytest_asyncio.fixture` vs `@pytest.fixture`, scope
   parameter (function / module / session), whether the fixture is
   parametrized. Wrong decorator for the plugin version silently fails
   (e.g., `@pytest.fixture` on `async def` in `asyncio_mode=strict`
   produces `TypeError: 'async_generator' object is not callable`).

   (c) **When tests will be parametrized, estimate collected-case count,
   not declared-test count.** (Duplicates item #5 above; included here
   for checklist completeness when this item is run as a standalone
   pre-dispatch check.) pytest reports each parametrized invocation
   independently; a single test declaration with N parametrized values
   lands as N collected cases.

   (d) **When tests use property-based testing frameworks (hypothesis is
   the current instance; other frameworks may exist in future
   milestones), verify fixture-scope compatibility with the generator's
   execution model.** Function-scoped fixtures don't reset between
   generated examples — the shared-state pattern lets later examples see
   earlier examples' state, weakening invariant claims. The principle
   (generator execution model vs fixture scope semantics) is
   framework-agnostic; the specific detection mechanism is framework-
   specific. Hypothesis flags this via `FailedHealthCheck`; the fix is a
   context manager inside the @given body, not suppressing the warning.

   **Evidence for promotion from Watching appendix.** Three orthogonal
   data points across three milestones: M0.13 parametrize-case-count
   (+24 cases predicted as +7); M0.14 pytest-asyncio strict-mode fixture
   decorator; M0.15 hypothesis + pytest function-scoped-fixture
   interaction. Each in a distinct sub-axis (case counting / async
   fixture decorators / cross-plugin fixture-scope); a single
   "test-harness semantics" checklist item at pre-resolution would have
   caught each at a different layer. Plugin interactions at the
   test-harness layer are a known hazard class, orthogonal to
   code-under-test behavior.

8. **Hazard-class vocabulary enumeration (promoted at M0.17 Stage 4b
   after two operational applications at M0.16 + M0.17).** Before
   finalizing the pre-resolution hazard list, cross-read the milestone's
   in-scope surface against the project's shared hazard-class
   vocabulary. Current vocabulary:

   - **Primary classes:** library-semantic, filesystem-semantic,
     runtime-semantic, framework-semantic, spec-intent-semantic.
   - **Candidate classes:** concurrent-state-machine, cross-process-
     coordination, transaction-boundary-under-crash, regex-semantic
     (status varies per project; consult the project's experiment
     ledger for current candidate tallies and promotion thresholds).

   **How to apply.** For each class, ask: does the milestone's code
   interact with this surface in a way that could produce the class's
   characteristic failure mode? Enumerate the hazards that surface
   per-class. The vocabulary is a *classifier*, not a generator —
   don't iterate class names to manufacture hazards you wouldn't
   otherwise consider; use it to CHECK whether your existing hazard
   enumeration missed a class the milestone's surface actually
   exercises.

   **Load-bearing against over-generalization.** At M0.16 the discipline
   was framed specifically as vocabulary-as-classifier (not vocabulary-
   as-generator) to prevent the pre-resolution from producing
   speculative hazards that don't correspond to real surface
   interactions. Generating hazards by class-iteration inflates the
   hazard list with items the milestone's code doesn't actually risk
   hitting, dilutes Stage 4c review focus, and muddies the post-close
   tally by mixing "hazard we considered" with "hazard the surface
   actually produces."

   **Class-assignment rubric for overlapping cases.** For each catch,
   ask: which interface's assumption was wrong? The interface being
   assumed-about is the class, not the caller exercising the
   assumption. For interactions that don't fit any single-interface
   rule (e.g., WAL-mode SQLite on a filesystem that violates POSIX
   fsync ordering — neither purely library nor purely filesystem but
   the interaction), log as a NEW class with a provisional name,
   1/1 orthogonal track. Don't retrofit into an existing class.

   **Class-concentration counting rule.** Multiple catches within a
   single class at one milestone count as 1 class-data-point for
   taxonomy-saturation purposes, regardless of individual catch count.
   Applies to any class with ≥3 hazards in a pre-resolution list.
   Prevents concentrated classes from dominating the taxonomy-
   saturation signal.

   **Evidence for promotion from ledger to Part 1 codification.** Two
   operational applications at M0.16 + M0.17, both executing vocabulary-
   as-classifier without generating speculative hazards; both producing
   class-appropriate hazard enumeration that matched the milestone's
   actual surface; both resulting in clean pre-commit-to-review
   alignment. Scenario A ratification at M0.16 adjudication (`dfde720`)
   promoted the vocabulary from B4-internal scaffolding to project-
   wide shared taxonomy; this step codifies the pre-resolution
   application that ratification scheduled for Stage 4b.

**Evidence this discipline pays.** M0.6 dispatch originally left the
wing-filter seeding ambiguous ("if `add_drawer` doesn't expose `wing=`,
NEEDS_CONTEXT"). Pre-resolving via a 5-minute read of `palace_handle.py:295`
confirmed World A and produced the `handle.add_drawer(wing="kitchen", ...)`
fixture pattern directly in the dispatch. The subagent never had to
escalate. If the ambiguity had gone unresolved, the subagent would have
hit the wall mid-dispatch and round-tripped for clarification — ~20 min
of wasted subagent time + a re-dispatch.

**Rule of thumb.** An ambiguity resolved at dispatch time costs 5 minutes;
an ambiguity discovered mid-dispatch costs a full review round-trip.

### Sanity-check the review gates will work

Before firing, also verify:

- The plan file and relevant spec sections are accessible from the subagent's
  working directory (i.e., committed, not staged-only).
- Reviewer prompts at
  `~/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7/skills/subagent-driven-development/`
  are still where you expect them.
- The previous milestone's review-response commit has landed (don't dispatch
  M0.N while M0.N-1 is still pending review fold-in).

### Stage-boundary git check (before every stage, not just at milestone start)

Before starting ANY stage of a multi-stage milestone (refactor → RED → GREEN
→ deltas → review-response), run:

```bash
git log --oneline -3
```

and confirm the previous stage landed as a committed artifact. Uncommitted
Stage N state contaminates Stage N+1's diff — the two become one commit, the
review pair has trouble reasoning about which change produced which test
outcome, and bisection over that range degrades silently.

**Forcing function from M0.8 Stage 1 → Stage 2.** The refactor commit was
ready to stage but I went straight into Stage 2 scaffolding without
committing. Caught mid-Stage-2 when `git diff --cached --stat` showed 789
insertions across 10 files — clearly two stages squashed together. Had to
split via `git restore --staged` + selective re-add. The check is cheap
(two seconds); the failure is expensive (untangling a combined diff, rewriting
history, explaining the squash to reviewers). Run the check at the top of
every stage.

---

## Part 2 — The dispatch prompt

The prompt has two parts. **Section A is verbatim boilerplate — copy into
every dispatch.** **Section B is milestone-specific — fill in per
dispatch.** Treat Section A's wording as load-bearing; don't rewrite
unless a specific lesson has shown a sentence needs changing.

### Section A — Verbatim boilerplate

```
## Tool authorization

Bash is pre-authorized for routine infrastructure commands in this
dispatch: `pytest`, `git` (add / commit / log / show / status / diff /
reset-soft), `grep`, `ls`, `cat`, `source`, `python3`, `pip`, `rm`
(scoped to `.venv/`, `__pycache__/`, `.pytest_cache/`),
`python3 -m venv`. Run these without asking for permission.

If the sandbox blocks a specific command (e.g. `pytest` in a reviewer
sandbox), that's not a blocker — inspect the code against the diff
instead. The implementer's test count is the evidence; reviewers verify
shape, not re-run the suite.

If you encounter a Bash error (command-not-found, path-not-found,
missing dependency), diagnose and fix before escalating. A damaged
`.venv` gets rebuilt with:

    rm -rf .venv && python3 -m venv .venv && \
      .venv/bin/python3 -m pip install -e ".[dev]"

Do not report "Bash denied" as a blocker. Do not self-censor on tool
permissions. If you see a permission failure, report the exact error and
the exact command, not a blanket refusal.

## What "stop on ambiguity" means — and what it doesn't

The stop-on-ambiguity rule exists to protect correctness when the
codebase disagrees with the plan. It applies to:

- A library function returning a shape the plan didn't describe
- An exception type the spec didn't name
- A fixture returning data the test wasn't written for
- A spec section that contradicts another spec section

It does NOT apply to:

- Tool permissions (Bash, Read, Write, Edit are authorized — run them)
- Missing dependencies (rebuild the venv, install what's needed)
- Routine infrastructure (commit a RED test, run `pytest`, push to a
  branch)
- Environmental damage (broken `.venv`, missing `__pycache__` — fix it)

When in doubt: is this a technical question about what the CODE should
do? -> stop and ask. Is this a logistical question about how to RUN the
code? -> fix it and move on.

## Standing dispatch discipline (every milestone, every dispatch)

1. **Red-commit-first TDD, always.** The first commit MUST be red: tests
   written, target module stubbed or absent, verified failing by running
   `pytest -v`. The second commit makes them green. Single-commit
   submissions are REJECTED at review regardless of test pass rate.

   "RED" means the tests fail because the functionality doesn't exist
   yet — 404 Not Found on the route, the target class absent, the
   function NameError. Failures due to import errors, fixture errors,
   typos, or missing-schema are NOT the right kind of red. Fix those
   first, then re-run until the RED state is specifically "the thing
   this test is about doesn't exist." THEN commit.

   **Standalone-commit discipline for cross-milestone carry-ins
   (named at M0.13, stable pattern across M0.8 → M0.10).** When a
   prior-milestone review surfaced Minor findings that defer to a
   later milestone, land them as a **standalone commit between RED
   and GREEN of the receiving milestone**, NOT bundled into GREEN.
   Observed carry-ins: M0.8 chromadb close-path Minors → M0.9
   Stage 2 commit; M0.9 `reap_stale` onexc → M0.10 Stage 2 commit.
   Clean bisection across the milestone boundary: the milestone
   work and the tech-debt cleanup become separately attributable
   in `git log`. Don't skip this step by squashing the carry-in
   into GREEN — a future `git bisect` on the GREEN commit will
   then attribute the GREEN functionality to the carry-in fix,
   which is false.

2. **Mandatory deviations note.** End your report with a "Deviations
   from plan" section. List every deviation from the plan sketch
   (pre-declared + any you discovered during implementation). If you
   discovered zero additional deviations beyond the pre-declared ones,
   name three candidate deviations you considered and rejected, with
   one-line rationales. Zero-discovery without the three-rejected
   fallback reads as "didn't look."

3. **Stop on ambiguity, don't decide.** (See the scope clarification
   above for what counts as ambiguity.) If you hit a genuine technical
   ambiguity — a library return shape that disagrees with the plan, an
   exception type that isn't named, a spec contradiction — STOP and
   file a `NEEDS_CONTEXT` report. Don't pick a path unilaterally.

4. **Teeth-bearing tests are non-skippable.** A test that only asserts
   `r.status_code == 200` is not teeth-bearing. A teeth-bearing test
   asserts something a broken implementation would fail: pagination
   boundary cases, honest-totals contracts, response-shape invariants,
   ordering or ranking properties. If your test would pass against a
   stub that returns `{}`, rewrite it.

   **Absence-vs-presence principle (added post-M0.8).** Assertions on
   "absence of bad state" (e.g., `key not in cache`, `assert ... is None`
   for post-cleanup state, `not hasattr(...)`) are WEAKER than
   assertions on "presence of good state" (e.g., "reopen the resource
   and a write succeeds," "query returns the expected new value").
   Absence-only passes whether or not the underlying cleanup actually
   happened; presence fails when cleanup silently drifts. For
   load-bearing teeth-bearing tests, prefer presence-of-good-state
   wherever practical. Lineage: M0.8 chromadb close-before-pop drift,
   silently-latent since M0.3 because every eviction-related test
   asserted key-absent rather than reopen-succeeds. Caught only when
   a new teeth-bearing test (`test_delete_then_reopen_creates_fresh_palace`)
   exercised the full lifecycle. `test_registry_eviction_bounds_backend_cache`
   was upgraded post-hoc with a reopen+write assertion to prevent
   future regressions.

5. **RED scaffolding: prefer "method missing" over "method raises
   NotImplementedError."** When writing the RED commit's stubs,
   leave the target method/attribute UNDEFINED entirely rather than
   defining a stub that raises. The RED test then fails with
   `AttributeError: 'Foo' object has no attribute 'bar'` — boring,
   unambiguous, no coupling to the stub's exception message.
   Whereas `raise NotImplementedError("M0.8 stub")` couples the
   test's assertion to the exact string "M0.8 stub" (or whatever),
   and some callers catch `NotImplementedError` for unrelated
   reasons. Lineage: M0.8 AuditLog scaffolding — `record_delete`
   was deliberately left undefined rather than stubbed, RED test
   fell through to `AttributeError`, and the GREEN commit added
   the method cleanly without stub-removal ceremony.

## Before You Begin

Raise any questions about requirements, approach, or dependencies NOW,
before starting. It's always OK to pause and clarify. Don't guess or
make assumptions.

Specifically, check:
- Is the reader/writer lock choice obvious from the read-vs-write
  nature of the operation?
- Are the response-shape expectations fully specified in the prompt?
- What should the zero-case return (empty palace, empty filter result,
  etc.)?

If any of these are unclear, ask.

## When You're in Over Your Head

Bad work is worse than no work. STOP and escalate (NEEDS_CONTEXT) when:

- A library function returns a shape disagreeing with its docstring
  (report the actual shape, don't adapt silently).
- The RED state tests pass on first run before you've implemented
  anything (something is wrong with fixtures — report, don't press on).
- A pre-existing test turns red after your GREEN commit (don't "fix"
  pre-existing tests to paper over).
- You can't achieve a "functionality-missing" RED no matter what you
  try (something about the scaffold disagrees with expectations).

Escalation format:
```
STATUS: BLOCKED | NEEDS_CONTEXT
REASON: [1-2 sentences]
ATTEMPTED: [what you tried]
RECOMMENDATION: [what the dispatcher should do next]
```

## Before Reporting Back: Self-Review

Review with fresh eyes. Ask:

**Completeness:**
- Did I fully implement what the task description asked for?
- Are there edge cases I didn't handle?

**Quality:**
- Is this my best work? Clean names? Consistent with existing patterns?
- Did I avoid overbuilding (YAGNI)?

**Discipline:**
- Did I commit RED first? Was the RED "functionality-missing," not
  import-error?
- Every deviation from the plan sketch listed?
- Every teeth-bearing test actually teeth-bearing?

**Testing:**
- Do tests verify behavior, not mock-behavior?
- Does the test count match the ladder rung?

Fix issues during self-review before reporting back.

## Report Format

- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- **What landed** — commit SHAs on the branch, in order (RED, GREEN,
  optional deltas).
- **Test counts** — before / after. Named rung from the ladder +
  which tests you wrote. Any count off the ladder must be surfaced.
- **Files changed** — paths, one per line.
- **Deviations from plan** — every deviation (pre-declared +
  discovered). If zero discovered, three candidates considered and
  rejected, with rationales.
- **Any concerns** — DONE_WITH_CONCERNS? Spell them out.
```

### Section B — Milestone-specific body (fill in per dispatch)

Each dispatch needs these sections below Section A. Section A is the "what
discipline applies"; Section B is the "what this specific milestone is."

#### B1. Task description from plan

Paste the full text of the relevant plan section directly. Don't make the
subagent open the plan file; context-switches are friction. If the plan
section is long (>200 lines), paste the task sketches + plan intro
discipline lines + cut irrelevant sub-sections.

#### B2. What already exists (DO NOT rebuild)

This is the section that pays for itself every time. List:

- **Schemas already defined** — Pydantic models, dataclasses — with file:line
  references.
- **Existing methods to bridge to** — handle methods, library functions,
  registry methods. File:line references. Signatures. Return shapes.
- **Fixtures the tests can reuse** — conftest.py fixtures, existing test
  helpers. File:line.
- **Reference routes/modules** — where to copy the existing pattern from
  (e.g., "ingest route at `routes/palace.py:64-162` is the reference
  pattern for registry.open + lock + to_thread + 503-on-shutdown").

The subagent should be able to implement from this section without
context-switching to read other files. If they have to go figure out how
the registry lock works, the "What already exists" section wasn't thorough
enough.

#### B3. Pre-declared deviations

These are the deviations you discovered during pre-resolution (Part 1).
Each gets a numbered entry with:

- **What the plan sketch says** (often: stale method name, stale default
  value, deprecated pattern).
- **What the code actually does now** (file:line).
- **Why the deviation is correct** (reason the plan got stale: later review
  response, cross-milestone decision, library API mismatch).
- **Explicit instruction:** "Use X, not what the plan sketch shows. Record
  this in your deviations note."

Pre-declared deviations are NOT optional. The subagent must list them in
its report even if every one was applied exactly as specified. This
enforces "proof the subagent read the dispatch."

#### B4. Teeth-bearing test coverage to hit

Name the tests you expect. For each, specify:

- **Test name** (imperative, describes what it proves).
- **Setup** (what to seed, how, via what path).
- **Assertion depth** (what a broken impl would fail — not just "200 OK").
- **Nastiest reachable context** (mandatory for any test exercising
  a filesystem / library / runtime interface). What specific context
  will the test exercise the interface under — and why is that context
  the one most likely to violate an assumption the plan-sketch didn't
  name? Examples from M0.8-M0.10 lineage:
    - M0.8 close-then-reopen: reopen on the same path within the
      same process lifetime, immediately after `.close()`. Nominal
      context is "close at process exit"; nastiest reachable is
      "close during test, reopen same path in the next test line."
    - M0.9 atomic-rename meta.json: asserting `.replaced_<ts>/meta.json`
      preserves pre-migration `schema_version`. Nominal is "new
      primary has new schema"; nastiest is "the inode shared with
      primary survived the migration untouched."
    - M0.10 middleware exception path: scope has no `"app"` entry.
      Nominal is "request arrived through real Starlette app";
      nastiest reachable is "unit harness built a minimal scope."
  If the interface has no surprising-context surface (pure-logic
  data structure, pure computation), write "N/A — pure logic" and
  explain. Silence here is the failure mode; forcing one line per
  test is the load-bearing discipline. See the "nastiest reachable
  context" doctrine entry in the appendix for the why.

Mark minimum tests vs optional tests. Give a test-count ladder (see B6).

#### B4b. Fix-propagation check on doctrine-class bugs

When a Stage 3 GREEN fix resolves a doctrine-class bug — or any
bug whose shape suggests it could recur at sibling call sites —
grep for the same pattern across the codebase BEFORE the fix is
considered complete. Mechanical checklist, not a judgment call:

1. **Name the pattern in grep-able form.** e.g., "bare `raise`
   after `except RuntimeError`", "`request.app.` vs `request.scope.
   get('app')`", "`write_text` on a path that might be hardlinked."
2. **Grep across `mempalace_sidecar/` and `tests/`.** Single
   ripgrep command, not a mental scan.
3. **Triage every match.** Does it need the same fix, a similar
   fix, or is it structurally different? Write down the answer
   per match.
4. **Apply the fix to every site that needs it in the same commit
   as the original fix** — OR if scope genuinely warrants splitting,
   file an explicit follow-up task listing the remaining sites so
   they can't rot.

**Why a checklist item, not a heuristic.** M0.11 shipped doctrine
hit #4's `HTTPException(500)` fix at the ingest `add_drawer` path
only. Five sibling call sites had the identical bare-raise hazard
(ingest/wake-up/search/list_drawers/delete/rebuild on
`registry.open`'s non-"shutting down" `RuntimeError` path). The
review pair caught the gap as Important #1; the Stage 3 grep would
have taken 30 seconds and surfaced all five. The fix-after-review
version cost a whole review-response cycle of investigation + a
uniform-fix commit. Mechanical rule, load-bearing.

**Relationship to the B4 "nastiest reachable context" doctrine.**
B4 is the WRITE-the-tests discipline (design the test to fail if
the plan-sketch OR implementation got the context wrong). B4b is
the PROPAGATE-the-fix discipline (when the test catches a bug,
check if the same bug lives at sibling sites). Different in kind —
sit side-by-side, neither subsumes the other.

**Grep-ability gate on B4b (flagged post-M0.12 consolidated
reflection).** B4b only works when the hazard class is grep-able.
"Bare `raise` after `except RuntimeError`" — grep-able. "`write_text`
on a path that might be hardlinked" — grep-able. "Implicit assumption
about `request.app` under minimal ASGI scope" — NOT grep-able; you'd
have to reason about every scope instantiation and middleware
invocation. When future findings surface, ask "is this grep-able?"
before promoting the fix to B4b discipline. If it isn't, the applicable
discipline is **B4's nastiest-reachable-context test design**, not
B4b's grep. Non-grep-able hazards demand enumerate-the-axis-of-recurrence
thinking, which the test-design discipline already encodes. B4b is the
fast tool for the cases it fits; don't stretch it past its reach.

#### B5. Milestone-specific escalation triggers

Beyond the generic "in over your head" list in Section A, name the
specific walls this milestone might hit:

- "If `handle.search` returns a shape that disagrees with the docstring,
  STOP and report the actual shape."
- "If the seed-via-handle fixture pattern can't set `wing` because the
  signature changed, STOP and report — don't poke `handle.col` directly."

These narrow the general escalation rule to this milestone's specific
risk surface.

#### B6. The test-count ladder

Every dispatch produces a test-count transition: `N_before -> N_after`.
Name the rungs explicitly:

- **Base:** "If you write exactly the minimum tests (X tests),
  count = N_before + X."
- **+1 rung:** "If you split test Y into two, count = N_before + X + 1."
- **+2 rung:** "If you also add the optional test Z, count = N_before + X + 2."

Any count OFF these rungs is a signal the subagent added or dropped
something unexpected — require surfacing in the report, not silent
shipping. From M0.6: {61, 62, 63} ladder was the formulation that closed
the "or whatever" ambiguity from a prior dispatch.

---

## Part 3 — Post-dispatch: the review-response pattern

Once the implementer reports back with RED + GREEN commits, the
review-response phase runs. This has stabilized across M0.3 - M0.6 into a
fixed shape:

### Dispatch both reviewers in parallel

- **Spec compliance reviewer** (`general-purpose` subagent). Verifies
  implementation matches plan and spec line-by-line. Template at
  `~/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7/skills/subagent-driven-development/spec-reviewer-prompt.md`.
- **Code quality reviewer** (`superpowers:code-reviewer` subagent).
  Verifies clean / tested / maintainable. Template at
  `code-quality-reviewer-prompt.md` in the same directory.

Run both in the background in a single dispatch message. They're
independent; parallel halves wall time.

Both dispatches should carry the Section-A Bash-preamble + stop-on-
ambiguity clarification. Reviewers are just as prone to self-censoring as
implementers.

### Known environmental constraint: pytest-denial on reviewer subagents

Reviewer subagents in this project's environment CANNOT run `pytest`.
Three data points (M0.6, M0.7, M0.8 reviewer pairs) all show the same
narrow pattern: `pytest` specifically blocked, while Read + Grep + Glob
+ Bash-for-inspect (git log, git show, ls, cat, wc) all work fine.
Reviewers complete successfully via diff-inspection against the
RED→GREEN commit lineage — they don't need to re-run the suite because
the implementer's test count is the evidence and the RED→GREEN shape
shows what changed.

**This is environmental, not a dispatch-prompt problem.** Do NOT attempt
to work around it with different preamble language, don't file it as a
subagent bug, don't try enabling pytest via Bash flags. The pattern is
stable across sessions and narrow enough not to block review work.
Dispatch prompts already tell reviewers "if pytest is sandboxed, that's
fine — inspect against the diff"; that instruction is correctly
load-bearing and should stay.

### Classify findings

Every reviewer returns in the `{Critical, Important, Minor}` severity
bands. Cross-check the two reviews for overlap.

- **Critical** — blocks milestone closure. Address before the next
  dispatch. If both reviewers flag the same Critical, double-weighted.
- **Important** — folds into one review-response commit before the next
  milestone dispatches. Message pattern: `fix(m0.N): quality-review
  response - <summary>`. Models: `6f87359`, `4e133a1`, `6d7875e`, `f8382f4`.
- **Minor** — defers to natural milestones per reviewer guidance.
  Sometimes fold in anyway if the Minor is in the same class as an
  Important (e.g., a rename paired with another rename — don't calcify
  stale shape before the next milestone copies it).

### Fold-in discipline

One review-response commit per milestone, not one per finding. Commit
message lists each folded finding (I1, I2, M2) with a short rationale and
names the Minors explicitly deferred. This keeps git history a coherent
story rather than a trickle of `fix: address review nit` commits.

Before committing the fold-in, run the full suite and confirm the test
count is unchanged from GREEN (or up, if the response added tests).

### Milestone closure

M0.N is closed when:

1. RED + GREEN + review-response commits have landed on the feature
   branch.
2. Full test suite passes at the post-response count.
3. Any deferred Minors are either captured in deltas or assigned to a
   natural milestone.
4. The commit trail is named in the checkpoint.

Only then dispatch M0.N+1.

---

## Part 4 — When you execute in main session instead of a subagent

**Default mode for implementer dispatch (post-M0.7): main session, not
subagent.** This is the revised default based on M0.6 + M0.7 data —
see deltas "2026-04-21 M0.7 subagent dispatch architecture — read/write
split validated." Three data points confirmed a clean pattern: subagent
dispatches that need to mutate the filesystem (Write / Edit / Bash-for-
commit) hit sandbox permission denials; subagent dispatches that only
read (Bash-for-git-log / Read / Grep) succeed reliably.

**The hybrid pattern that works for this project:**

| Task type | Dispatch mode |
|---|---|
| Exploration / research / codebase analysis | Subagent |
| Spec-compliance review | Subagent |
| Code-quality review | Subagent |
| Implementer dispatch (write code, commit) | Main session |
| One-off read-heavy questions | Subagent |

Main-session implementer execution is not a fallback anymore — it's
the primary mode. The review-pair pattern in Part 3 stays fully
subagent-driven; that's where the author-isolation value lives and
it's empirically reliable.

Other reasons main-session execution may still be appropriate even if
subagent dispatch becomes viable again:

- The remaining work is a mechanical execution of a plan that's
  already been fully specified (no context-isolation benefit left to
  protect).
- Main session has the cache-hot context and re-briefing a subagent
  costs more than executing.

**The rule that does NOT bend:** if you execute the milestone yourself,
the review pair still runs. No exceptions.

The main-session-execution temptation is: "I wrote this code, I already
know it's right. Skip the reviews, they won't find anything." That's
exactly where the author-eye fails and exactly why the review pair exists.
The two reviewers catch classes of issue the author cannot see precisely
because they're not the author:

- Spec reviewer catches "you implemented what you THOUGHT the spec said,
  not what it actually said."
- Quality reviewer catches "this defensive code exists without evidence
  / this test lacks teeth / this helper should be public / this rename
  should happen now."

M0.6 validates this: main-session execution shipped clean code, and the
quality review still produced 3 Important findings worth folding in. The
author-eye missed all three.

**Other disciplines that still apply under main-session execution:**

- Red-commit-first TDD (write tests, run, verify route-missing RED,
  commit; THEN implement, run, commit GREEN).
- Deviations note, produced in the same format the subagent would
  produce. Not a commit message — a structured report that the review
  pair can reference as an artifact.
- Teeth-bearing tests — same non-skip standard.
- Stop on ambiguity — same rule. If you can't resolve a technical
  ambiguity without context-switching for an hour, stop and ask rather
  than guess.

The main-session-execute report should be pasted into the conversation in
the same shape a subagent report would take. Treat the review pair as
consumers of that report.

---

## Appendix — Lineage of the pieces

Brief notes on where each piece of this template came from, so that
future edits can see the forcing function:

- **Red-commit-first TDD.** Established M0.3.0; singleton-commit
  submissions started getting rejected.
- **Three-gaps-considered-and-rejected fallback.** M0.4; aspirational at
  first, baked in by M0.6.
- **Stop-on-ambiguity scope clarification.** M0.6; first-attempt
  subagent self-censored on Bash availability. Diagnostic probe (2 min)
  proved Bash was structurally available, so the scope narrowing is the
  real fix.
- **Bash preamble specifics.** M0.6 review-response; the spec reviewer
  hit a `pytest`-specific sandbox block (while `echo`, `git`, `ls`
  worked). Preamble now names this and tells reviewers code inspection
  against the diff is sufficient.
- **Pre-resolve-ambiguities discipline.** M0.6; World-A wing-setting
  check at dispatch time saved a full review round-trip.
- **Test-count ladder.** M0.6; the `{61, 62, 63}` formulation closed
  the "or whatever" ambiguity from an earlier dispatch.
- **Main-session-still-runs-review.** M0.6; the temptation to skip
  review after author-executed was explicitly named and held.
- **Two-reviewer parallel pattern.** Established M0.3, refined M0.4.
  Reviewers don't see each other's output — independent assessments.
- **One review-response commit per milestone.** M0.4 - M0.6; fold-in
  discipline prevents commit trickle.
- **Deltas file.** M0.1 - M0.2; drift-log discipline separated from plan
  freeze.
- **Main-session default for implementer dispatch.** M0.6 + M0.7.
  Three data points (two implementer failures + three reviewer
  successes) confirmed the read/write split: subagent sandbox denies
  mutation operations but permits reads + review. Template default
  flipped post-M0.7.
- **Cross-reviewer severity reconciliation.** M0.7 review pair:
  spec-reviewer flagged a log-line as Important (§8.4 spec "always"
  language); quality-reviewer flagged same line as Minor (framed as
  comment clarity). Rule: when two reviewers disagree on severity of
  the same finding, the spec-reviewer's classification wins for
  spec-compliance questions.
- **Stage-boundary git-check discipline.** M0.8. Stage 1 refactor and
  Stage 2 scaffolding nearly shipped as one commit because the
  stage-between-commit wasn't made. Caught via `git diff --cached
  --stat` showing a suspicious line count. Added as a Part 1 rule:
  `git log --oneline -3` at the top of every stage.
- **Absence-vs-presence testing principle.** M0.8 chromadb
  close-before-pop drift. Silent since M0.3 because every eviction
  test asserted "key absent from cache" (absence of bad state) rather
  than "reopen + write succeeds" (presence of good state). Caught only
  when a new teeth-bearing test (`test_delete_then_reopen_creates_fresh_palace`)
  exercised the full lifecycle. Upgraded the M0.3 eviction test
  post-hoc. Added to Part 2 B4 standing discipline.
- **RED "method missing" over "stub raises."** M0.8. AuditLog's
  `record_delete` was left undefined rather than stubbed with
  `NotImplementedError`; the cleaner `AttributeError` decoupled the
  RED test from a stub-specific exception message. Generalized to a
  Part 2 rule.
- **pytest-denial on reviewer subagents is environmental.** M0.6 + M0.7
  + M0.8 reviewer pairs all hit the same narrow denial (`pytest`
  specifically blocked, everything else works). Reviewers complete via
  diff-inspection. Not fixable from prompts. Named in Part 3.
- **Spec-reviewer-findings trend (holding at M0.9).** M0.6: 4
  Importants. M0.7: 1 Important. M0.8: 0 findings. M0.9: 0 Critical
  + 0 Important + 2 Minor (lifespan comment-vs-code mismatch,
  redundant schema check — both low-stakes). Reading: pre-resolution
  + code catching spec ambiguities upstream + discovered-deviation
  fixes landing same-session before reviewers see code. NOT
  decay-toward-lazy review — quality reviewer still produced 3
  Importants at M0.9 (plan-sketch latent bug, spec prominence,
  untested chain). So the review pair is NOT redundant; quality
  reviewer is load-bearing. But spec-reviewer's value proposition is
  shifting: from "catches spec compliance bugs" to "ratifies that
  pre-resolution caught everything spec-relevant." If the pattern
  holds at M0.10/M0.11 (spec reviewer zero Critical + zero Important
  despite migrations/shutdown/metrics complexity), revisit whether
  spec-reviewer dispatch still earns its cost. Don't cut yet.

- **"Nastiest reachable context" doctrine (M0.8 + M0.9 + M0.10
  + M0.11 — five-for-five across orthogonal bug classes, layers at
  plan-sketch AND implementation).** The invariant that defines
  doctrine hits is NOT "plan-sketch had a wrong assumption." It
  is: an interface behaved differently under conditions the
  [plan-sketch OR implementation] didn't imagine, AND a
  teeth-bearing test exercising the nastiest reachable context
  caught it before the bug shipped. Whether the faulty assumption
  lived in the plan-sketch or the implementation is secondary to
  the test doing its job.

    - **M0.8 (library-semantic, plan-sketch layer).** chromadb
      close-before-pop. Spec §5.3 assumed GC-sufficient close;
      chromadb 1.5.x requires explicit `client.close()`. Caught by
      `test_delete_then_reopen_creates_fresh_palace` — first test
      to exercise close-then-reopen on the same path within a
      process lifetime.
    - **M0.9 (filesystem-semantic, plan-sketch layer).** Runner's
      meta.json write-through-hardlink. Plan code at line 2784
      assumed `write_text` was COW; Linux truncates the shared
      inode and propagates through every hardlink. Caught by
      `test_atomic_rename_happy_path` asserting
      `.replaced_<ts>/meta.json` preserved pre-migration
      `schema_version`.
    - **M0.10 (runtime-semantic, plan-sketch layer).** Shutdown
      gate read `request.app.state.accepting`;
      `starlette.Request.app` is a property that raises `KeyError`
      on scopes without an `"app"` entry. Caught by
      `test_middleware_counter_sequence_under_exception` (unit)
      with its deliberately-minimal ASGI scope.
    - **M0.11 hit #1 (framework-semantic, implementation layer).**
      Ingest route's bare `raise` after bumping the error counter
      propagated through Starlette's `BaseHTTPMiddleware` in a way
      that bypasses `ServerErrorMiddleware`'s 500-conversion —
      known Starlette quirk where exceptions escape through the
      `call_next` task boundary. Caught by
      `test_ingest_increments_error_on_raise` at `assert r.status_code
      == 500`. Fix: `raise HTTPException(500) from exc`.
    - **M0.11 hit #2 (spec-intent-semantic, implementation layer).**
      Structured-log middleware set `route` field to
      `request.url.path`, which contains raw user_id verbatim —
      a sibling-field leak despite the `user_id` field itself
      being hashed correctly. Caught by
      `test_structured_log_schema`'s `assert raw_user not in
      payload` check. Fix: `_normalize_route(path)` replaces the
      user_id segment with `{user_id}` template.

  Five hits over four milestones, structurally orthogonal bug
  classes (library / filesystem / runtime / framework / spec-intent),
  two-layer distribution (three plan-sketch, two implementation).
  Rules out both "narrow domain luck" AND "plan-sketch-specific
  failure mode" — the mechanism generalises across interface
  categories AND across where-the-assumption-lives.

  **Plan-half consistency is a SIBLING discipline, NOT a sixth
  class under this doctrine (named at M0.13 post-reflection).**
  M0.13 surfaced a new failure class: plan test-half and plan
  impl-half encoded disagreeing semantics for the same behavior.
  Caught at Stage 3 test-run; defense was plan-half consistency
  check at pre-resolution (Part 1 item #4), NOT nastiest-context
  test design. Structural difference from hits 1-5: the code
  behaves EXACTLY AS WRITTEN; what's inconsistent is the PLAN
  (two halves disagree with each other). Hits 1-5's defense is
  "write the test against the nastiest context the code will
  see"; hit 6's defense is "verify the plan's test-half and
  impl-half encode the same semantic before writing either one."
  Different defense → different discipline → not a sixth class
  under the nastiest-reachable-context doctrine but a sibling
  discipline that sits next to it. Do NOT fold the plan-half
  class into a "6 over 5 milestones" tally that implies the
  same defense mechanism is catching both — it isn't.

  **The accounting question that surfaced at M0.11 close.** Counting
  only plan-sketch hits undercounts what the B4 checklist catches.
  The checklist is defined at the interface/test level; an
  implementation-layer assumption about an interface is the same
  failure shape as a plan-sketch-layer assumption, and the same
  defense mechanism catches it. For reflection accounting at M0.12+,
  the honest metric is "nastiest-context tests caught something
  that would have shipped" — not "plan-sketches had bugs."

  **Two layers of verification, both load-bearing, neither subsumes
  the other:**

    - **Pre-resolution** (Part 1 discipline, enforced before dispatch)
      catches **plan-vs-reality drift**: plan assumes API X, the
      code currently has API Y. Stale method names, deprecated
      patterns, library-boundary shifts. Fixed by reading current
      code against the plan sketch.
    - **Teeth-bearing tests** (Part 2 B4 discipline, enforced
      inside the milestone's RED/GREEN cycle) catch
      **plan-vs-correctness drift**: plan code compiles and looks
      right but produces wrong behavior under specific conditions.
      Fixed by designing the test to exercise the nastiest
      reachable context, not the nominal one.

  **Sharper heuristic (supersedes the M0.8+M0.9 formulation).** The
  plan-sketch isn't wrong about what the interface DOES. It's wrong
  about what the interface does in the specific **context** the
  code will actually run in. `.close()` under a subsequent same-path
  reopen. `.write_text()` on a file with `st_nlink > 1`.
  `request.app` on an ASGI scope without an `"app"` key. Doctrine:
  **when writing a teeth-bearing test for any filesystem / library
  / runtime interface, exercise it under the nastiest reachable
  context, not the nominal one.** Reachable = a context the
  production path or a unit-test harness will actually produce.
  Nastiest = the one most likely to violate an assumption the
  sketch didn't name.

  **Operational promotion — doctrine moves to B4 checklist item.**
  As of post-M0.10, Part 2 B4 requires each test-plan entry to
  name the nasty context it exercises, not just the assertion
  depth. Named principles are easy to skip when fatigued;
  checklist items aren't. Lower variance — the discipline
  survives a bad day. See B4 above for the checklist form.

  **Forward-looking.** Every milestone with new filesystem /
  library / runtime / framework / spec-intent interaction surface
  (M0.15 outbox WAL + fsync, M0.19 systemd socket activation, M1
  backend evaluation) will likely continue to surface such findings,
  at either the plan-sketch OR implementation layer. Not failures
  — the discipline working.

  **Saturation-vs-regression-to-mean test (open at M0.12).** If
  M0.12 closes with 0 doctrine hits (either layer), that's data for
  saturation — specific interface categories covered by existing
  tests may have exhausted their assumption-violation surface. If
  hits continue at ~1-2 per milestone, that's regression-to-mean —
  the defense mechanism is steadily catching a real ongoing failure
  rate, and "three consecutive" was not a saturation signal but a
  sampling artifact. Either outcome informs the M1 scope review;
  neither is a problem for the defense itself.

## Appendix — "Watching" section (candidate disciplines not yet promoted)

Two-tier template evolution: **promoted** items live in the
checklist (Part 1 / Part 2 / Part 3); **candidate** items live
here with their data-point count. Migration from watching to
checklist requires meeting the promotion criterion below. The
watching section exists so candidate classes stay visible
across milestones without inflating the checklist with patterns
that aren't yet stable.

**Promotion criterion for new discipline classes (named at M0.14
post-reflection).** Three orthogonal data points across milestones,
where **orthogonal means different manifestations of the same
class**, not just repeat occurrences of the same specific bug.
Two data points is a trend worth naming; three orthogonal is a
discipline worth codifying. The orthogonality requirement is
load-bearing: without it, a single recurring-instance bug could
be pattern-matched into a false class by counting each occurrence
separately. The same specific fixture bug appearing three times
across three milestones is one data point, not three.

**Why a formal promotion criterion, not a heuristic.** Patterns
that get named early and promoted late tend to be stronger patterns
than ones that get promoted the moment they're spotted. The
promotion bar is intentionally high so the checklist stays small
and load-bearing; items in the checklist carry "do this every
milestone" weight, which dilutes if the checklist grows too long
too fast.

### Test-harness-semantics (2 data points, M0.13 + M0.14)

**Candidate class.** Pre-resolution estimation errors about how
the test harness reports or executes tests, distinct from
production-code plan-sketch errors.

**Data points:**
  - **M0.13 Stage 1:** `@pytest.mark.parametrize` with 18
    whitespace/empty-string values produced 18 collected cases
    against 1 declared test. Pre-resolution predicted "+7 tests"
    on the ladder; actual was "+24 cases." Surfaced at first
    pytest run.
  - **M0.14 Stage 1:** `@pytest.fixture` on an async generator
    produced `TypeError: 'async_generator' object is not callable`
    because pytest.ini doesn't set `asyncio_mode = auto`, so
    pytest-asyncio 1.3.0 runs in strict mode requiring
    `@pytest_asyncio.fixture` on async fixtures. Surfaced at
    first pytest run.

**Why not promoted yet.** Two data points; orthogonality is
borderline (both are pre-resolution estimation errors about test
harness behavior, but in different axes — case counting vs fixture
execution model). A third orthogonal data point in a clearly
different sub-axis (e.g., fixture scope semantics, plugin
interaction, pytest-xdist coordination) would confirm the class
is real and not just "two instances of the same thing expressed
differently."

**M0.15 watchpoint.** M0.15 (outbox + flusher) introduces SQLite
fixtures, potentially pytest-xdist or pytest-forked concerns, and
cross-process test coordination. At M0.15 pre-resolution, scan
for: fixture scope / session vs function vs module; any pytest
plugin required by the test plan that hasn't been verified present
in the Flask venv; SQLite fixture concurrency semantics. If a
third orthogonal test-harness-semantic finding surfaces at Stage 1
or first-pytest-run, promote to a Part 1 checklist item.

**Promotion draft (for when the third data point lands).**
"Test-harness configuration review — pytest.ini settings, conftest
decorator types for async/plugin fixtures, fixture scope
implications for the test surface you're adding." Sits next to
Part 1 #4 (plan-half consistency) and #5 (parametrized-case-count
estimation) as a dispatch-time test-harness verification pass.

This template itself is expected to evolve. After M0.7 lands, reflect on
what the subagent tripped over, what you had to re-explain, what you
found yourself adding milestone-specific that should have been general.
Revise. After M0.8, reflect again. Two milestones of evolution gets the
template to a stable shape; one is insufficient. **As of post-M0.9: four
milestones of evolution have landed (M0.6 template extraction, M0.7
hybrid-default flip, M0.8 absence-vs-presence + stage-boundary checks,
M0.9 plan-sketch-verification doctrine). Template is considered stable
baseline. Future edits should pass a higher bar (i.e., a specific
milestone-forced lesson worth encoding) rather than speculative
refinement.**
