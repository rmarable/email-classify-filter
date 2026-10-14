"""What is left of the global model queue (SPEC §5.2; V1.3 step 2a): the hold an `ecf eval run`
takes on Ollama (V1.3 step 8).

The queue that ran the local model on mail, its rounds, heat and battery rules, attempts and
`model_failed`, went in v3.0.0 (ADR 0031): no mail is classified locally any more. The hold stays
only for `ecf eval run`'s local backends, and goes with them.
"""

from __future__ import annotations

import threading


class Exclusive:
    """Held by an eval run (V1.3 step 8): one run at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.holder: str | None = None
        self.since: str | None = None  # when it was taken (timestamp), for the Slack line

    def acquire(self, holder: str, since: str | None = None) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.holder, self.since = holder, since
        return True

    def release(self) -> None:
        self.holder = self.since = None
        self._lock.release()

    def held(self) -> bool:
        return self._lock.locked()


EXCLUSIVE = Exclusive()
ROUND_LOCK = threading.Lock()  # an eval run takes it as it did while model rounds ran
