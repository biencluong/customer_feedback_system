"""Step-level trace of a pipeline run: printed to stderr and written to <run_dir>/trace.jsonl."""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List, Optional

_MAX_PRINT = 220


class Tracer:
    def __init__(self, path: Optional[Path] = None, verbose: bool = True):
        self.path = path
        self.verbose = verbose
        self.events: List[dict] = []
        self._t0 = time.monotonic()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def event(self, kind: str, **data: Any) -> None:
        record = {"t": round(time.monotonic() - self._t0, 3), "kind": kind, **data}
        self.events.append(record)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        if self.verbose:
            body = json.dumps(data, default=str, ensure_ascii=False)
            if len(body) > _MAX_PRINT:
                body = body[:_MAX_PRINT] + "…"
            print(f"[{record['t']:7.2f}s] {kind:<24} {body}", file=sys.stderr)

    @contextmanager
    def span(self, kind: str, **data: Any) -> Iterator[None]:
        start = time.monotonic()
        self.event(f"{kind}.start", **data)
        try:
            yield
        except Exception as e:
            self.event(f"{kind}.error", error=f"{type(e).__name__}: {e}", ms=int((time.monotonic() - start) * 1000))
            raise
        self.event(f"{kind}.end", ms=int((time.monotonic() - start) * 1000))
