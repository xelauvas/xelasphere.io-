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
