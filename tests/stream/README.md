# Stream module tests

Pure-logic tests (state machine, block AST, pre-warm) run under Node's built-in test runner:

    node --test tests/stream/

DOM / renderer tests live in `app/static/test/stream-harness.html` — open in a browser.

The 30-item corpus lives in `tests/stream/corpus/`. Each fixture is a pair:
- `NNN-short-name.md` — raw LLM output
- `NNN-short-name.expected.json` — the block AST the state machine must produce
