# Logging privacy — pseudonymous, not anonymous

Per spec §8.4. Operator-facing statement of what is and isn't protected in
mempalace AND xelasphere Flask logs.

**Scope note:** The discipline below originated for mempalace (sidecar +
Flask-mempalace). It was extended to cover the broader Flask codebase
(memory_v2/v3, chat_engine, chat_routes, smart_router, file_processor,
artifacts, image_generator, orchestrator/cache, utilities, blueprints) on
2026-04-28 after issue #11 surfaced that user message content was being
logged at INFO across many call sites. The principle ("logs never contain
always-personal-data payloads") applies uniformly across the codebase —
not just mempalace.

## Always-personal-data payloads (never log content; counts/lengths only)

The following fields are always-personal-data when they originate from a
user request or a model response derived from one. Logs may record their
**presence, count, or length** but never their **content** (no `[:N]`
slicing, no `repr()`, no embedded interpolation):

- User chat messages, prompts, queries (`user_message`, `user_prompt`,
  `user_prompt_raw`, `prompt`, `query`, `instruction`, `description`)
- AI model responses and excerpts (`response_text`, `chat_summary`,
  `chat_payload`, any `response.text` from the chat path)
- Memory-extraction candidate values (`candidate.value`, `entry.text`,
  `resolution.final_text`, `removed_entry.text`)
- Artifact content and patches (`old_string`, `new_string`, `content`,
  `meta_json`, `raw_json`, `search_text`)
- Email subject/body parsed from AI output
- Pattern match groups extracted from user text (`m.group(0)`)
- Image generation prompts
- File RAG queries

Acceptable variants for diagnostic value:
- `len(field)` — record the length without the content
- `field is not None` / boolean presence flags
- The field's category or schema key (e.g., `candidate.key` is the schema
  name like "favorite_color"; the value is the personal data)

## Pseudonymous vs. anonymous

Mempalace logs **pseudonymize** user identifiers: every log entry writes `sha256(user_id + salt)[:12]` in place of the raw `user_id`. The raw user_id is never logged.

That is **not** the same as anonymous.

An attacker who compromises the salt (stored in `/etc/mempalace/sidecar.env`, mode `0640`, root:mempalace) can reverse the pseudonymization for any known user by computing `sha256(user_id + salt)[:12]` and matching. The salt is the load-bearing secret — pseudonymity depends on the salt's confidentiality.

## What this implies

- **Logs can be retained longer than anonymous logs could be**, but they're not "anonymized for retention purposes" in the GDPR sense. They're pseudonymized, which is a distinct classification under GDPR Article 4(5). Retention policies should treat mempalace logs as "personal data with reduced identifiability," not "non-personal data."
- **Salt rotation invalidates historical log correlation.** If the salt in sidecar.env is rotated, the hash of the same user_id changes, and log entries from before and after the rotation can't be correlated. This is the correct trade-off — rotating the salt is a response to salt-compromise or a scheduled refresh. Do not rotate casually.
- **Salt compromise is a privacy incident.** If sidecar.env is exposed (leaked to a public repo, left on a compromised host backup, etc.), the logs should be treated as de-pseudonymized from that point backwards. Incident response: rotate the salt, invalidate the leaked backup, audit who had read access.
- **Logs never contain drawer text, ingest bodies, or wake-up results.** These are always-personal-data payloads; pseudonymization of the user_id does nothing to protect them. The spec explicitly forbids logging these fields (§8.4).
- **Redaction logs record rule name + count only** — not the matched text. `xelasphere_mempalace_redaction_hits_total{rule="openai_key"}` records that a rule fired; the actual matched text (which might be a partial secret or PII) is never logged.

## The schema

Every log line follows a fixed schema (spec §8.4):

```
ts, service, level, event, user_id, turn_id, route, latency_ms, err, request_id
```

- `user_id` is the pseudonymized hash.
- `turn_id` is a UUIDv7 generated per chat turn; correlates the two record_turn calls (user + assistant halves) via the shared `pair_id` stored on drawers.
- `request_id` is generated per incoming chat request; propagates through the outbox (as a column), through the flusher, to the sidecar via the `X-Request-ID` header. End-to-end correlation survives async.

## Storage

- **Primary:** journald on the sidecar and Flask hosts. journald retention follows host policy.
- **File sink:** `/var/log/mempalace/` for 14 days, logrotate with `copytruncate` (spec §7.1). `copytruncate` is used specifically to survive wheel rollbacks that would otherwise break open file pointers.

## GDPR reconciliation

A nightly cron (spec §8.6) compares `mempalace_delete_total` (count of palace deletions) against `audit.sqlite` row count (record of admin deletion actions). Drift between the two is a `warn` — indicates a delete either (a) fired without audit, or (b) had an audit entry without corresponding palace deletion. Either is a bug worth investigating.

## On-call and escalation

On-call is a single rotation (Xela, v1). Pages are for `page`-severity alerts. Warns are reviewed next business day. Salt-compromise is always page-severity regardless of how it's detected (including via casual discovery of sidecar.env in an unexpected location).
