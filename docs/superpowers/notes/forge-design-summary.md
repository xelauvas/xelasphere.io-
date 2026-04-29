# Forge — design summary (the "if I were you" call)

**Date:** 2026-04-29
**Scope:** Working notes captured during the brainstorm before the M1 spec
was authored. Snapshot of the design call across all milestones (M1, M2, M3),
not just M1. M1 spec is `2026-04-29-forge-m1-validation-loop.md`.

These notes are a record of the architectural reasoning, not a binding
contract. The locked decisions live in the spec.

---

## The big idea

Working name **Forge** — the coding-infrastructure layer between the existing
chat / artifact panel and the model. Goal: even when the model produces
broken code (Netflix-clone-class JSX bug), the *infrastructure* catches it,
retries surgically, and presents the result beautifully. Inspired by Claude
Code (agent loop, tool calls visible) layered into Claude Desktop's
artifact-panel polish.

The model is strong enough; what's missing is everything around it.

## Decision matrix — what I'd choose, with reasoning

| Decision | My pick | Reasoning |
|---|---|---|
| Trigger | Hybrid (Code pill always; Auto on coding intent; Think never) | best of both, keeps your pills meaningful — Claude Desktop "model self-triggers" loses brand control; Claude Code "always agent" makes every casual hi cost 4 model calls |
| Sandbox | In-browser v1, server v2 | Docker is broken in /opt; ship value first |
| Transcript | Full inline, **collapsed-by-default** | rhythm matters — Plan expanded, WriteFile collapsed (header only), Run-success collapsed, Run-failure expanded, Patch expanded |
| Cascade order | V4-Flash → V4-Pro → Kimi-K2.5 → GPT-4.1 | already locked in Q6 |
| Cascade start | **Always V4-Flash** (regardless of Code/Auto pill) | save premium for when earned; V4-Flash with thinking-mode default is genuinely strong |
| Tools | 6 in M1-M2 MVP, expand later | 11 is months, 6 is weeks |
| Panel | Don't touch | user said "the artifact is already almost what we need" — don't open that battle |
| Multi-turn | Incremental edits | huge UX win over full-regen — "add a search bar" should not rewrite 5 files |
| Failure | Cancel + 4-iter cap + budget pause | cheap, high trust |
| Phasing | **3 milestones, M1 in 1 week** | this is the most important call |

## Phasing (the whole-Forge picture)

### M1 — Validation Loop (1 week)
Smallest unit that solves the Netflix bug today.
- Server-side parser/lint after each artifact generation
- If parser fails → feed error back to model, retry; escalate per cascade
- Single visible "Retry — escalating to V4-Pro" status block
- No new tools, no Plan, no Snapshot
- Zero changes to artifact panel

This catches every Netflix-class bug. Ship Monday, validate by Friday.

### M2 — Visible Agent Loop (2-3 weeks)
- Add `Plan`, `WriteFile`, `PatchFile`, `Run`, `Preview`, `Snapshot` tools
- Build the transcript-block visual system in chat
- Multi-model cascade visible in transcript
- Multi-turn incremental edits
- Where the "wow" lands publicly

### M3 — Server sandbox + polish (3-4 weeks)
- Fix Docker in /opt + add server-sandbox tier
- `EditFile`, `MultiEdit`, `Rollback`, `LookupDocs`, `AskUser`
- Cost meter + token budget pauses
- Mobile-collapsed transcript view

Total ~6-9 weeks; user-visible benefit kicks in at end of week 1.

## Why this beats the alternatives

- **vs Claude.ai-faithful (kill pills, model self-triggers, no agent
  loop)**: loses brand control; doesn't catch syntax bugs at all
- **vs Claude Code-faithful (always-agent + plan-mode toggle)**: every
  message costs 4 model calls; pills become meaningless
- **vs full-Forge-as-one-PR**: months of work before any user benefit;
  drowns the existing roadmap (mempalace P3, settings modal, crypto chart
  hotfixes already in flight)

The phased hybrid keeps existing UX, ships value in week 1, and scales up
to "claude code in your browser" by week 9.

## Net (the "if I were you")

1. Lock the design exactly as proposed (cascade, hybrid trigger, custom
   tools, full transcript, in-browser+server tiered sandbox).
2. **Phase it** — M1 (validation loop only, ~1 week), M2 (full agent loop
   visible, ~2-3 weeks), M3 (server sandbox + nice-to-haves, ~3-4 weeks).
3. Ship M1 immediately because it solves the Netflix bug today with the
   smallest possible change.
4. Tweaks to the original design:
   - Always start cascade at V4-Flash regardless of pill (save premium for
     when needed)
   - Default-collapse non-error transcript blocks (visual rhythm)
   - Defer LookupDocs and AskUser to M3 (not critical for MVP)
