// app/static/js/artifactDiff.js
// Self-hosted, dependency-free diff utilities for the artifact preview.
(function (root) {
  'use strict';

  // Line-level diff via LCS backtrack. Returns ordered rows:
  //   {type:'context'|'add'|'del', text, oldNo, newNo} and {type:'gap', count}
  // Long unchanged runs are collapsed to `context` lines around each change.
  function computeUnifiedDiff(oldText, newText, context) {
    context = context == null ? 3 : context;
    var a = oldText ? oldText.split('\n') : [];
    var b = newText ? newText.split('\n') : [];
    var n = a.length, m = b.length;

    // Guard: very large files -> cheap replace-all diff (avoid O(n*m) blowup).
    if (n * m > 4000000) {
      var rows0 = [];
      for (var x = 0; x < n; x++) rows0.push({ type: 'del', text: a[x], oldNo: x + 1, newNo: null });
      for (var y = 0; y < m; y++) rows0.push({ type: 'add', text: b[y], oldNo: null, newNo: y + 1 });
      return rows0;
    }

    var dp = [];
    for (var i = 0; i <= n; i++) dp.push(new Int32Array(m + 1));
    for (i = n - 1; i >= 0; i--)
      for (var j = m - 1; j >= 0; j--)
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);

    var ops = [];
    i = 0; j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { ops.push({ type: 'context', text: a[i], oldNo: i + 1, newNo: j + 1 }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ type: 'del', text: a[i], oldNo: i + 1, newNo: null }); i++; }
      else { ops.push({ type: 'add', text: b[j], oldNo: null, newNo: j + 1 }); j++; }
    }
    while (i < n) { ops.push({ type: 'del', text: a[i], oldNo: i + 1, newNo: null }); i++; }
    while (j < m) { ops.push({ type: 'add', text: b[j], oldNo: null, newNo: j + 1 }); j++; }

    // Keep context lines near changes; collapse the rest into 'gap' markers.
    var keep = new Array(ops.length).fill(false);
    ops.forEach(function (op, idx) {
      if (op.type !== 'context') {
        for (var k = Math.max(0, idx - context); k <= Math.min(ops.length - 1, idx + context); k++) keep[k] = true;
      }
    });
    var rows = [], skipped = 0;
    ops.forEach(function (op, idx) {
      if (op.type === 'context' && !keep[idx]) { skipped++; return; }
      if (skipped > 0) { rows.push({ type: 'gap', count: skipped }); skipped = 0; }
      rows.push(op);
    });
    if (skipped > 0) rows.push({ type: 'gap', count: skipped });
    return rows;
  }

  root.ArtifactDiff = root.ArtifactDiff || {};
  root.ArtifactDiff.computeUnifiedDiff = computeUnifiedDiff;
})(typeof globalThis !== 'undefined' ? globalThis : window);
