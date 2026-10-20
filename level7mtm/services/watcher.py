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
                 extensions: tuple[str, ...] = (".xml",), handler=None, known=None, skip=None):
        self.ws = workspace
        self.folder = folder
        self.extensions = extensions
        self.handler = handler or workspace.import_file
        self.settle = settle_seconds
        self.clock = clock
        self.state = WatchState()
        self.more = False
        self.known = known              # path -> True: read before (the program was closed and opened again)
        self.skip = skip                # path -> True: not now (e.g. GRAFIS is writing it); looked at next time

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

    def poll(self, limit: int | None = None) -> list:
        """Check the folder once; returns the FileOutcome list of files processed in this poll. ``limit``: at most
        that many files are read now (``more`` says that others are ready and waiting)."""
        results, handled = [], 0
        self.more = False
        t = self.clock()
        here = self._candidates()
        if self.known is not None:                     # first look after a start: files read before stay read
            for p in here:
                try:
                    if self.known(p):
                        st = os.stat(p)
                        self.state.done[p] = (st.st_size, st.st_mtime)
                except OSError:
                    pass
            self.known = None
        for gone in [p for p in self.state.done if p not in here]:   # moved away (e.g. its orders deleted): put
            self.state.done.pop(gone, None)                         # back, it is read again
        for p in here:
            if self.skip is not None and self.skip(p):
                self.state.seen.pop(p, None)            # settles again once it is free
                continue
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
            if limit is not None and handled >= limit:
                self.more = True                                # ready, read in the next batch
                continue
            self.state.done[p] = sig
            self.state.seen.pop(p, None)
            handled += 1
            try:
                out = self.handler(p)
            except Exception as e:  # noqa: BLE001 - one bad file never stops the files after it
                self.state.failed[p] = f"{type(e).__name__}: {e}"
                continue
            results.append(out)
            if _failed(out):
                self.state.failed[p] = _message(out)
            else:
                self.state.failed.pop(p, None)
        return results

    def retry(self, path: str):
        self.state.done.pop(path, None)
        self.state.failed.pop(path, None)
        if not os.path.isfile(path):                   # a failed ERP file was moved to the ER folder
            d, name = os.path.dirname(path), os.path.basename(path)
            moved = next((m for m in (os.path.join(d, "ER", name),                  # done / ER inside the folder
                                      os.path.join(os.path.dirname(d), "error", name))   # process / error
                          if os.path.isfile(m)), None)
            if moved is None:
                return None                            # gone (deleted or moved by hand): nothing to retry
            path = moved
        out = self.handler(path)
        try:
            st = os.stat(path)
        except OSError:                                # moved again by the handler (to done / ER)
            return out
        self.state.done[path] = (st.st_size, st.st_mtime)
        if _failed(out):
            self.state.failed[path] = _message(out)
        else:
            self.state.failed.pop(path, None)
        return out


def _failed(out) -> bool:
    st = out.get("status") if isinstance(out, dict) else out.status
    if not isinstance(out, dict) and any(g["status"] in ("blocked", "not_configured") for g in out.grafis):
        return True
    return st in ("failed", "unmatched", "needs_review") or (st.startswith("cutting_") and st != "cutting_done")


def _message(out) -> str:
    if isinstance(out, dict):
        cut = out.get("cutting") or {}
        return "; ".join([m for s, m in out["check"].issues if s != "info"] + [cut.get("message", "")]).strip("; ") \
            or out.get("status", "")
    return "; ".join([out.message] + [f"{g['line_number']}: " + "; ".join(g["problems"]) for g in out.grafis
                                      if g["status"] in ("blocked", "not_configured")]).strip("; ")
