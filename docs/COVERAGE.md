# Runtime Code Coverage — Dead Code Detection

XelaSphere traces runtime coverage in production to identify dead code. Unlike
static analyzers (vulture, pyflakes) which guess, this measures what actually
runs during real user traffic.

## How it's wired

`run.py` starts `coverage.Coverage` before importing the app, so module-level
code (blueprints, decorators, registry init) is traced too. Each gunicorn
worker writes its own `.coverage.*` file to `/var/log/xelasphere/coverage/`.

A periodic 60-second save ensures data persists even if gunicorn force-kills
a worker on max_requests rotation — no reliance on atexit firing.

Enabled via `Environment=COVERAGE_ENABLED=1` in the systemd unit. To disable
(for a small perf reclaim: coverage adds ~1-2% overhead), remove that line
and restart.

## Reading the data

After at least a week of real usage (longer is better — rarely-used admin
routes, error paths, etc. need diverse traffic to be exercised):

### 1. Merge all worker files

```bash
cd /var/log/xelasphere/coverage
cp .coverage.* /tmp/cov/
cd /tmp/cov
/root/xelas.io/xelasphere.io-/venv/bin/coverage combine .coverage.*
```

This produces a single `.coverage` file with merged data.

### 2. Summary report — sorted by lowest coverage first

```bash
coverage report --skip-empty --sort=cover | head -50
```

Files at 0% after a week of usage are strong dead-code candidates.

### 3. HTML drill-down

```bash
coverage html -d /tmp/cov/html --skip-empty
```

Open `/tmp/cov/html/index.html` in a browser. Click any file to see
line-by-line which code ran (green) vs never ran (red). Best tool for
identifying dead branches inside live files.

### 4. Function-level with annotations

```bash
coverage annotate -d /tmp/cov/annotated
```

Writes `*.py,cover` files with `!` prefixes on uncovered lines.

## Interpreting results — what's real vs false positive

**Real dead code** (safe to delete):
- Entire files at 0% after diverse usage
- Helper functions in otherwise-covered files with 0% hits
- Old error handlers for APIs that have changed

**False positives** (keep despite 0%):
- Error paths for conditions that didn't occur in the sample period
  (out-of-memory handlers, DB connection loss, etc.)
- Migration / bootstrap scripts (run once per deploy)
- Admin endpoints only used manually
- Paid-tier-only code paths if you only had free-tier traffic
- Code behind feature flags that are off
- Code that runs only for specific user roles not sampled

## Cross-reference with static analysis

Static tools generate many false positives on a Flask app, but when they
AGREE with runtime coverage, confidence is high:

```bash
cd /root/xelas.io/xelasphere.io-
./venv/bin/vulture app/ --min-confidence 80 > /tmp/vulture.txt
coverage report --sort=cover --skip-empty | awk '$NF == "0%" {print $1}' > /tmp/zero.txt
# Items in both lists
comm -12 <(sort /tmp/vulture.txt) <(sort /tmp/zero.txt)
```

## Cleaning up old data

Coverage files accumulate. Clean periodically:

```bash
# After a report, archive and clear
mv /var/log/xelasphere/coverage/.coverage.* /tmp/cov-$(date +%Y-%m-%d)/
```

## Disabling for a specific release

If you need to disable temporarily (e.g., performance benchmark):

```bash
sudo systemctl edit xelasphere
# Add: Environment=COVERAGE_ENABLED=0
sudo systemctl restart xelasphere
```

Or comment out the `Environment=COVERAGE_ENABLED=1` line in the unit file.

## Performance impact

Branch coverage on Python adds roughly 1-2% overhead. Negligible for an
I/O-bound Flask app where most time is spent waiting on Firestore / LLM /
TTS. Acceptable as a permanent production feature.

## Timeline

- **Week 1:** Baseline coverage numbers. Many files under 10% — that's normal
  because usage patterns take time to cover rare paths.
- **Week 2-4:** Numbers stabilize. Use this snapshot for dead-code review.
- **Ongoing:** Re-run reports monthly. Files that consistently stay at 0%
  across months are genuine dead code.
