# FHS migration cutover runbook (issue #6 + bundled #7 deploy)

This runbook is the operator-side procedure for executing
`deploy/migrate-fhs.sh`. The script does the heavy lifting; this
document captures the things that have to happen *outside* the
script — context, verification, rollback.

## What this cutover does

| | Before | After |
|---|---|---|
| Code dir | `/root/xelas.io/xelasphere.io-` | `/opt/xelasphere` |
| Service user | `root` | `xelasphere` |
| Env file | `/root/xelas.io/xelasphere.io-/.env` (root:root 0600) | `/etc/xelasphere/xelasphere.env` (root:xelasphere 0640) |
| Systemd unit | `/etc/systemd/system/xelasphere.service` (User=root) | same path, User=xelasphere |
| Flusher unit | unable to run as `User=xelasphere` (cannot traverse `/root` 0700) | runs cleanly |

State directories (`flask_session/`, `uploads/`, `logs/`, `instance/`,
`nltk_data/`, `firebase-creds.json`) move *with* the code tree to
preserve the no-code-changes scope rule of issue #6. They live under
`/opt/xelasphere/` rather than `/var/lib/xelasphere/` for now;
relocation to `/var/lib` would require code changes (relative paths in
`config.py` would need to become absolute) and is intentionally
deferred. `outbox.db` is *already* at `/var/lib/xelasphere/outbox.db`
under the existing flusher unit and is unaffected by this cutover.

## Pre-flight

1. **Pick a low-traffic window.** Migration causes ~30s of downtime
   for the Flask app. The mempalace sidecar is unaffected (separate
   unit) and the mempalace flusher will be restarted at the end of
   the script (also brief).

2. **Read this whole document before starting.** Especially the
   rollback section.

3. **Use a tmux session NOT inside the source tree.** The script
   has a guard for this, but humans deserve the warning too:
   ```
   cd /root  # or anywhere outside /root/xelas.io/xelasphere.io-
   tmux new -s fhs-cutover
   ```

4. **Confirm the script is the one you intend to run.** It should be
   in your working tree from a `git pull` of branch
   `feature/mempalace-p1-6-fhs-migration`. Diff against the version
   in the open PR if you want belt-and-braces verification.

## Pre-flight dry run (recommended)

Before committing to the cutover, run the script's pre-flight stage in
isolation. This validates the environment (xelasphere user exists,
`/etc/xelasphere/` in place, `/opt/xelasphere/` empty, current shell
not inside the source tree) without changing any state:

```bash
sudo bash /root/xelas.io/xelasphere.io-/deploy/migrate-fhs.sh --check-only
```

Re-run as needed. If pre-flight fails, fix the underlying condition
and re-check before proceeding.

## The cutover

```bash
sudo bash /root/xelas.io/xelasphere.io-/deploy/migrate-fhs.sh
```

The script prints colored stage banners (`[migrate-fhs]` cyan, errors
red). Each stage is bail-on-failure with `set -euo pipefail`. If a
stage fails, you'll see the failing command's output; the script
exits and leaves partial state in place for inspection.

Total runtime: ~30-90 seconds depending on rsync size.

## What to verify after the script finishes

The script prints these in its tail, but they're worth restating:

1. **HTTP serves on the public side.** Load `https://xelasphere.com/`
   in a browser — full page should render (not the nginx default,
   not a 502).

2. **Process is running as `xelasphere`.**
   ```
   systemctl status xelasphere | grep -i 'main pid'
   ps -p <PID> -o user=
   # expect: xelasphere
   ```

3. **Flusher is healthy.** This is the unit that was *blocked* before
   the migration; it should now be active and not in a restart loop:
   ```
   systemctl status mempalace-flusher
   journalctl -u mempalace-flusher --since '5 min ago' | tail -30
   ```

4. **Mempalace mode round-trips.**
   ```
   curl -s http://127.0.0.1:5000/admin/mempalace-mode
   # expect: {"mode":"off"}  (still off until you flip)
   ```

## Bundled #7 deploy (do this AFTER FHS migration is verified stable)

The sidecar UDS socket-mode tightening (issue #7) was deferred to
this deploy window per its own issue body. Once FHS is stable
(green for at least 5 minutes, no error spikes in the journal):

```bash
# Build the new sidecar wheel from the current branch
cd /opt/xelasphere/mempalace-sidecar
sudo -u xelasphere ./.venv/bin/python -m build --wheel

# Reinstall into the sidecar's venv
sudo /opt/mempalace-sidecar/venv/bin/pip install --force-reinstall \
  /opt/xelasphere/mempalace-sidecar/dist/mempalace_sidecar-*.whl

# Restart the sidecar service
sudo systemctl restart mempalace-sidecar.service

# Verify socket mode is now 0660
ls -la /run/mempalace/sidecar.sock
# expect: srw-rw---- (mode 0660)
```

If the wheel build or reinstall fails, the sidecar continues running
on the previous wheel — no production impact. Investigate at leisure
and retry.

## P1 entry (the rotation)

Once both #6 and #7 are deployed and stable, the P1 entry rotation
is one command (manual ops step):

```bash
sudo /usr/local/bin/mempalace-mode shadow
curl -s http://127.0.0.1:5000/admin/mempalace-mode
# expect: {"mode":"shadow"}
```

Then update `docs/mempalace-shadow/phase-log.md` P1 section:
fill the `Entered:` line with the current UTC timestamp.

## Rollback procedure

If verification fails after the script runs:

```bash
# 1. Stop the (broken) new service
sudo systemctl stop xelasphere.service

# 2. Restore the prior unit from the backup the script made
ls /etc/systemd/system/xelasphere.service.pre-fhs-*.bak  # pick the most recent
sudo cp /etc/systemd/system/xelasphere.service.pre-fhs-<TIMESTAMP>.bak \
        /etc/systemd/system/xelasphere.service
sudo systemctl daemon-reload

# 3. Restore nginx if it was touched
ls /etc/nginx/sites-available/xelasphere.conf.pre-fhs-*.bak
# (similar restore + nginx -t + systemctl reload nginx)

# 4. Start the old service back up (it points at /root/xelas.io/...
#    which is still intact — the script rsync'd, not moved).
sudo systemctl start xelasphere.service

# 5. Verify HTTP serves again on the old layout
curl -sf http://127.0.0.1:5000/ >/dev/null && echo OK

# 6. Tear down the failed migration so a re-attempt can proceed cleanly
sudo mv /opt/xelasphere /opt/xelasphere.failed-$(date -u +%Y%m%dT%H%M%SZ)
sudo rm /etc/xelasphere/xelasphere.env  # the new env file
```

The `/root/xelas.io/xelasphere.io-` source tree is **rsync'd, not moved**
by the script. After a successful migration it's still on disk and is
safe to leave in place for 24-48h as a recovery handle. After the soak
window, archive it:

```bash
sudo mv /root/xelas.io/xelasphere.io- /root/xelas.io/xelasphere.io-.archived-$(date -u +%Y%m%d)
```

## Post-cutover housekeeping (within ~7 days)

- [ ] Archive `/root/xelas.io/xelasphere.io-` once the new layout has
      soaked clean for 48h.
- [ ] Remove the `.pre-fhs-*.bak` backups under `/etc/systemd/system/`
      and `/etc/nginx/sites-available/` once you're confident.
- [ ] Update any operator runbooks / wikis that reference the old
      path.
- [ ] Close issue #6 with a link to the cutover commit.

## Defects surfaced on the 2026-04-27 cutover (now fixed in source)

The first run of this cutover took the Flask app down for ~2.5h
(18:34 → 21:00 UTC) while four latent defects were patched in runtime.
All four are now baked into the script + units; this section is here
so future readers know what those changes are *for* without grepping
git history.

1. **Venv shebang sweep (Stage 4b).** Python venvs hardcode the
   interpreter path in script shebangs at venv-creation time. After
   `rsync`, every `*/bin/*` script under `/opt/xelasphere/venv/` and
   `/opt/xelasphere/mempalace-sidecar/.venv/` still pointed at
   `/root/xelas.io/xelasphere.io-` — unreachable by the
   `User=xelasphere` service. Manifested as `status=203/EXEC` on
   `gunicorn` startup. Stage 4b now sed-rewrites all venv files
   uniformly.
2. **Firebase creds path translation (Stage 5).** `FIREBASE_CRED_PATH`
   in the source `.env` pointed at `/root/.config/xelas/firebase.json`
   — a user-home path the `xelasphere` user can't traverse (`/root` is
   `0700`). Stage 5's sed now also rewrites that path to the in-tree
   `${NEW_DIR}/firebase-creds.json` (md5-identical copy, present in
   the migrated tree).
3. **Flusher unit `WorkingDirectory`.** `mempalace-flusher.service`
   ran `python -m app.mempalace.flusher_main` with an empty
   `WorkingDirectory` (default `/`), so `app` was unimportable —
   the unit ModuleNotFoundError-looped. Now sets
   `WorkingDirectory=/opt/xelasphere` (matching `xelasphere.service`).
   Also adds `Environment=FIREBASE_CRED_PATH=…` because the flusher's
   transitive `app.config` import requires it at module load even
   though the flusher itself doesn't use Firebase.
4. **Sidecar UDS chmod via `ExecStartPost` (issue #7 follow-up).**
   The in-process `os.chmod(socket, 0o660)` hook in
   `mempalace_sidecar/main.py` lifespan loses a race against
   uvicorn 0.45's bind ordering — the `sock_path.exists()` guard
   returns `False` when the lifespan startup runs before bind,
   silently skipping the chmod and leaving the socket at `0666`.
   `mempalace-sidecar.service` now polls for the socket and chmods
   from systemd as a belt-and-suspenders. The in-process hook is
   still in place; reworking it to wait for bind is a future-issue
   item, but the systemd-side chmod is sufficient for the security
   posture #7 was opened to fix.

If you re-run this script on a fresh host, all four are handled
automatically. None of these need operator intervention.
