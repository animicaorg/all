from __future__ import annotations

from typing import Dict, Set


class QuotaTracker:
    def __init__(self, default_concurrent: int = 1) -> None:
        self.default_concurrent = int(default_concurrent)

    def release(self, provider_id: str, *_ignored) -> None:
        st: Dict[str, Set[str]] = getattr(self, "_aicf_active", {})
        s = st.get(provider_id)
        if not s:
            return
        try:
            jid = next(iter(s))
            s.discard(jid)
        except StopIteration:
            pass
