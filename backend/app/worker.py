"""Worker process: claims jobs, runs handlers, ticks the scheduler.

    python -m app.worker            # run forever
    python -m app.worker --once     # drain the queue once (tests / cron)
"""
from __future__ import annotations

import signal
import sys
import threading
import time
import traceback

from .services import audit_engine, handlers, handlers_monitor  # noqa: F401  (register handlers)
from .services import handlers_ai, handlers_backlinks, handlers_growth, handlers_local, handlers_social  # noqa: F401
from .services.jobs import claim, execute, scheduler_tick, worker_id

_stop = threading.Event()


def run_once(max_jobs: int = 100) -> int:
    wid, n = worker_id(), 0
    scheduler_tick()
    while n < max_jobs:
        j = claim(wid)
        if not j:
            break
        execute(j)
        n += 1
    return n


def run_forever(poll: float = 1.0):
    wid, last_tick = worker_id(), 0.0
    print(f"[worker] {wid} started", flush=True)
    while not _stop.is_set():
        try:
            if time.monotonic() - last_tick > 15:
                scheduler_tick()
                last_tick = time.monotonic()
            j = claim(wid)
            if j:
                print(f"[worker] running {j.kind} {j.id} attempt {j.attempts}", flush=True)
                execute(j)
                continue
        except Exception:
            traceback.print_exc()
        _stop.wait(poll)


def start_background_thread():
    t = threading.Thread(target=run_forever, daemon=True, name="genranq-worker")
    t.start()
    return t


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    if "--once" in sys.argv:
        print(run_once())
    else:
        run_forever()
