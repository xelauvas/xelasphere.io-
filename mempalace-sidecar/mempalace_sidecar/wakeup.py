"""Wake-up context composition — M0.5.2 stub (RED state).

Implementation lands in the GREEN commit. This stub exists so the
M0.5 test suite can import ``build_wakeup`` and ``_to_drawer`` and
fail at the assertion layer rather than at the import layer.
"""

from __future__ import annotations

from mempalace_sidecar.schemas import Drawer, WakeupContext


def _to_drawer(raw) -> Drawer:  # pragma: no cover - RED stub
    raise NotImplementedError("M0.5.2 GREEN implements _to_drawer")


async def build_wakeup(  # pragma: no cover - RED stub
    *,
    user_id: str,
    query: str,
    handle,
    session_id,
    hot: bool = True,
    k: int = 5,
) -> WakeupContext:
    raise NotImplementedError("M0.5.2 GREEN implements build_wakeup")


__all__ = ["build_wakeup", "_to_drawer"]
