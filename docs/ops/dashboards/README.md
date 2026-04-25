# MemPalace dashboards

Two Grafana dashboards for the mempalace integration:

- `mempalace-live.json` — real-time operational view (ingest rate, wake-up latency, outbox depth, circuit state, cache pressure, readyz).
- `mempalace-rollout.json` — rollout/phase-tracking view (mode gauge, shadow log volume, 2×2 rubric tally, KG daily spend vs cap, disk growth).

## These files are skeletons, not polished dashboards

The JSON committed here is a starting point: every panel is defined with the correct PromQL query against the metrics in spec §8.1, but grid layout / axis tuning / color schemes / thresholds are not visually verified. Grafana dashboard authoring is a visual iterative process; it's not productive to hand-craft polished JSON outside Grafana.

**Deployment workflow:**

1. In Grafana, **Dashboards → Import → Upload JSON file** → import one of these JSONs.
2. The panels will render but may need layout/sizing adjustments. Tune in Grafana.
3. When satisfied, **Dashboard settings → JSON Model → Copy**. Export back to this file.
4. Commit the updated JSON; it replaces the skeleton. Future operators import the tuned version.

## Metrics referenced

Both dashboards query the metrics declared in spec §8.1. Live dashboard uses Flask-side metrics (`xelasphere_mempalace_*`). Rollout dashboard mixes Flask-side metrics with sidecar-side metrics (`mempalace_*`).

If a panel shows "No data" after import, verify the metric name matches what's actually exported by the running service — metric catalog additions at later milestones may not yet be in the skeleton JSON.

## Grafana version targeted

`schemaVersion: 37` (Grafana 9.x+). Re-saving in newer Grafana will auto-upgrade.
