"""Optional watched folder for ERP XML files.

* a file is read only after its size and modification time stayed unchanged for ``settle_seconds``
  (ERP may still be copying it); files with the extensions .tmp/.part/.partial or starting with '~' are ignored;
* source files are never deleted or moved;
* every file is processed once per content (SHA-256 in the database); a changed file with the same name is new
  content and is processed again;
* failures are kept in ``failed`` with their message until the user retries.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

IGNORED_SUFFIXES = (".tmp", ".part", ".partial", ".crdownload")


@dataclass
class WatchState:
    seen: dict[str, tuple[int, float, float]] = field(default_factory=dict)   # path -> (size, mtime, first_seen_stable)
    done: dict[str, tuple[int, float]] = field(default_factory=dict)          # path -> (size, mtime) processed
    failed: dict[str, str] = field(default_factory=dict)


class FolderWatcher:
    def __init__(self, workspace, folder: str, settle_seconds: float = 3.0, clock=time.monotonic,
                 extensions: tuple[str, ...] = (".xml",), handler=None):
        self.ws = workspace
        self.folder = folder
        self.extensions = extensions
        self.handler = handler or workspace.import_file
        self.settle = settle_seconds
        self.clock = clock
        self.state = WatchState()

    def _candidates(self):
        try:
            names = os.listdir(self.folder)
        except OSError:
            return []
        out = []
        for n in names:
            low = n.lower()
            if n.startswith("~") or low.endswith(IGNORED_SUFFIXES) or not low.endswith(self.extensions):
                continue
            p = os.path.join(self.folder, n)
            if os.path.isfile(p):
                out.append(p)
        return out

    def poll(self) -> list:
        """Check the folder once; returns the FileOutcome list of files processed in this poll."""
        results = []
        t = self.clock()
        for p in self._candidates():
            try:
                st = os.stat(p)
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime)
            if self.state.done.get(p) == sig:
                continue
            prev = self.state.seen.get(p)
            if prev is None or (prev[0], prev[1]) != sig:
                self.state.seen[p] = (sig[0], sig[1], t)        # new or still changing: wait
                continue
            if t - prev[2] < self.settle or st.st_size == 0:
                continue
            out = self.handler(p)
            results.append(out)
            self.state.done[p] = sig
            self.state.seen.pop(p, None)
            if _failed(out):
                self.state.failed[p] = _message(out)
            else:
                self.state.failed.pop(p, None)
        return results

    def retry(self, path: str):
        self.state.done.pop(path, None)
        out = self.handler(path)
        st = os.stat(path)
        self.state.done[path] = (st.st_size, st.st_mtime)
        if _failed(out):
            self.state.failed[path] = _message(out)
        else:
            self.state.failed.pop(path, None)
        return out


def _failed(out) -> bool:
    st = out.get("status") if isinstance(out, dict) else out.status
    return st in ("failed", "unmatched", "needs_review") or (st.startswith("cutting_") and st != "cutting_done")


def _message(out) -> str:
    if isinstance(out, dict):
        cut = out.get("cutting") or {}
        return "; ".join([m for s, m in out["check"].issues if s != "info"] + [cut.get("message", "")]).strip("; ") \
            or out.get("status", "")
    return out.message
