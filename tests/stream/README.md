# Stream renderer tests

Assistant messages are rendered by `app/static/js/streamRenderer.js` on top of
`app/static/js/stream/markdownEngine.js` (markdown-it + math, task lists, code chrome).

    node --test tests/stream/markdownEngine.test.mjs

The text helpers (what is shown while an answer is still arriving) run with no
dependencies. The parser tests need markdown-it, which the page loads from a CDN;
point `MARKDOWN_IT_JS` at a copy of `markdown-it.min.js` to run them too:

    curl -sO https://cdn.jsdelivr.net/npm/markdown-it@14.0.0/dist/markdown-it.min.js
    MARKDOWN_IT_JS=$PWD/markdown-it.min.js node --test tests/stream/markdownEngine.test.mjs
