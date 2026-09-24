"""The decision loop as its own process.

    python -m app.worker

Separated from the API so that restarting, upgrading or reloading the web side
never interrupts trading, and so a supervisor (Docker, launchd, systemd) can
restart it on its own if it dies. The API reads the loop's status from the
database (monitor.scheduler.status) and never needs to share memory with it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))

from app.core.db import init_db  # noqa: E402
from app.monitor import scheduler  # noqa: E402


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    await scheduler.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logging.getLogger("worker").info("decision loop running")
    await stop.wait()
    await scheduler.stop()


if __name__ == "__main__":
    asyncio.run(main())
