"""An injectable clock. Real code asks the clock for time, so tests can move time by hand."""

from __future__ import annotations

import threading
import time


class RealClock:
    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """Time moves only when a test calls advance() or code calls sleep()."""

    def __init__(self, start: float = 1_000_000.0):
        self._t = float(start)
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._t

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._t += seconds

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.advance(seconds)
