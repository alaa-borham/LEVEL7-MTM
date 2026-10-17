"""What the program is working on right now, per order line (for the progress bars in the order browser).

The automatic chain runs in worker threads of the same program: writing the GRAFIS model, the robot in GRAFIS,
nesting and the cutting files. Each step marks its order lines while it runs; the window reads the marks."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()
_busy: dict[int, dict] = {}


@contextmanager
def working(revision_ids, what: str, expected_s: float | None = None):
    """Mark these order lines as being worked on (``what``: model / robot / nesting) while the block runs;
    ``expected_s``: how long it should take (a filling bar), None = no estimate (a moving bar)."""
    rids = [r for r in revision_ids if r is not None]
    mark = {"what": what, "since": time.monotonic(), "expected": expected_s}
    with _lock:
        for r in rids:
            _busy[r] = mark
    try:
        yield
    finally:
        with _lock:
            for r in rids:
                if _busy.get(r) is mark:
                    del _busy[r]


def snapshot() -> dict[int, dict]:
    """{revision id: {what, since, expected, progress (0..1 or None)}} of what runs now."""
    now = time.monotonic()
    with _lock:
        out = {}
        for r, m in _busy.items():
            p = None
            if m["expected"]:
                p = min(0.97, (now - m["since"]) / m["expected"])
            out[r] = dict(m, progress=p)
        return out
