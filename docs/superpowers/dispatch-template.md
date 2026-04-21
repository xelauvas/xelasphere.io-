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
4. Any gap you find becomes a **pre-declared deviation** in the dispatch
   prompt, with `(a) if X world, do Y; (b) if Z world, do W; (c) if neither,
   defer with a note` decisions already made.

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

- Test name (imperative, describes what it proves).
- Setup (what to seed, how, via what path).
- Assertion depth (what a broken impl would fail — not just "200 OK").

Mark minimum tests vs optional tests. Give a test-count ladder (see B6).

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

Sometimes subagent dispatch isn't the right call:

- Subagent hit a structural block (the M0.6 first-attempt case — should
  be rare after the Bash-preamble fix, but not impossible).
- The remaining work is a mechanical execution of a plan that's already
  been fully specified (no context-isolation benefit left to protect).
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

This template itself is expected to evolve. After M0.7 lands, reflect on
what the subagent tripped over, what you had to re-explain, what you
found yourself adding milestone-specific that should have been general.
Revise. After M0.8, reflect again. Two milestones of evolution gets the
template to a stable shape; one is insufficient.
