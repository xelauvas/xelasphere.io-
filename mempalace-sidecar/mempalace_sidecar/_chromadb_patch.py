"""Cache the ONNXMiniLM_L6_V2 singleton used by chromadb's default EF,
with single-threaded intra-op execution for concurrent safety.

UPSTREAM BUG (chromadb 1.5.8). ``chromadb/api/types.py`` line 965::

    def __call__(self, input: Documents) -> Embeddings:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
            ONNXMiniLM_L6_V2,
        )
        return ONNXMiniLM_L6_V2()(input)

Constructing a fresh ``ONNXMiniLM_L6_V2`` on every call triggers cold
lazy-init of the tokenizer (~5ms) and the ONNX ``InferenceSession``
(~180ms). On a 300-drawer palace this pushes wake-up p95 from
~106ms (post-patch, intra_op=1) to ~770ms (pre-patch) — from
comfortably under the 150ms hot target to over the 500ms hard cap.
See deltas entry B (2026-04-20) for the full benchmark trail.

THE FIX — TWO PARTS.

Part 1: cache one ``ONNXMiniLM_L6_V2`` instance process-globally and
route every ``DefaultEmbeddingFunction.__call__`` through it.

Part 2: force the cached session to ``intra_op_num_threads=1`` (and
``inter_op_num_threads=1``). ChromaDB's default is ``intra_op=0``
(= "use all cores") which causes severe contention when multiple
concurrent wake-ups hit the same session: each call tries to fan out
across all CPUs, producing context-switch thrashing. Empirically
(AMD EPYC-Rome, 16 cores) the default configuration scales 4-concurrent
embeddings to ~4× single-call p95 (linearity worse than serial-in-
python). With ``intra_op=1`` each embedding runs on one core, so N
concurrent embeddings parallelize across N cores with near-flat p95.
Single-call p50 goes from ~25ms to ~80ms — slower in isolation but
predictable under the multi-user workloads the sidecar actually
faces. See deltas entry B for the concurrency-scaling numbers.

Safety:

* ``InferenceSession.run()`` is thread-safe per ONNX runtime docs.
* The Rust-backed ``tokenizers.Tokenizer`` is thread-safe for
  ``encode``.
* Same class, same default padding, deterministic: byte-identical
  vectors for identical input pre- and post-patch regardless of
  thread-count settings — no palace rebuild required.

IMPORT-ORDER CONTRACT. This module's import has the side effect of
patching ``DefaultEmbeddingFunction.__call__``. It MUST be imported
before any ``chromadb.PersistentClient`` or ``chromadb.Collection`` is
constructed anywhere in the process — that's why it's wired from the
top of ``mempalace_sidecar/__init__.py``. Test
``test_patch_applied_on_bare_import`` proves this works.

WHEN TO REMOVE THIS MODULE. If chromadb upstream ships a fix (track
the issue), the canary test ``test_default_ef_singleton_invariant``
still passes (one constructor across N calls), and removing this
module produces the same singleton-instance behaviour, then this patch
is redundant. Bump ``_PINNED_CHROMADB`` to the fixed version, run the
canary test against the un-patched build, and delete this module.
"""

from __future__ import annotations

import os
from functools import cached_property

import chromadb
from chromadb.api.types import DefaultEmbeddingFunction


# Pinned to the chromadb version whose source we audited for the
# patch site. A version drift could rewrite the delegation pattern
# this patch targets; failing loud at startup beats silent breakage.
_PINNED_CHROMADB = "1.5.8"
assert chromadb.__version__ == _PINNED_CHROMADB, (
    f"mempalace_sidecar chromadb singleton patch is pinned to "
    f"chromadb=={_PINNED_CHROMADB}; got {chromadb.__version__}. Re-verify "
    f"the patch site at chromadb/api/types.py DefaultEmbeddingFunction."
    f"__call__ and update _PINNED_CHROMADB once confirmed."
)


_cached_onnx = None


def _build_cached_onnx():
    """Construct an ``ONNXMiniLM_L6_V2`` subclass with intra-op and
    inter-op thread counts fixed at 1 — see module docstring for the
    concurrency rationale.
    """
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
        ONNXMiniLM_L6_V2,
    )

    class _SingleThreadONNX(ONNXMiniLM_L6_V2):  # type: ignore[misc, valid-type]
        @cached_property
        def model(self):  # type: ignore[no-untyped-def]
            so = self.ort.SessionOptions()
            so.log_severity_level = 3
            so.graph_optimization_level = (
                self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            so.intra_op_num_threads = 1
            so.inter_op_num_threads = 1
            return self.ort.InferenceSession(
                os.path.join(
                    self.DOWNLOAD_PATH,
                    self.EXTRACTED_FOLDER_NAME,
                    "model.onnx",
                ),
                providers=["CPUExecutionProvider"],
                sess_options=so,
            )

    return _SingleThreadONNX()


def _cached_default_call(self, input):
    """Replacement for ``DefaultEmbeddingFunction.__call__``.

    Lazy-initialises a single process-global single-threaded
    ``ONNXMiniLM_L6_V2`` on first call; every subsequent call
    delegates to the same instance.
    """
    global _cached_onnx
    if _cached_onnx is None:
        _cached_onnx = _build_cached_onnx()
    return _cached_onnx(input)


_cached_default_call._xela_patched = True


# Idempotency guard. Re-importing this module (test reloads, fork-then-
# import edge cases) must not double-patch — wrapping an already-
# patched function is harmless today but could compound badly if the
# patch shape ever changes.
if not getattr(DefaultEmbeddingFunction.__call__, "_xela_patched", False):
    DefaultEmbeddingFunction.__call__ = _cached_default_call


__all__: list[str] = []
