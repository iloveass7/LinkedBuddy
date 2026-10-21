"""`python -m linkedbuddy.worker [--kinds a,b] [--concurrency N]` (ARCHITECTURE.md 2)."""

import argparse
import asyncio
import logging
import signal

from linkedbuddy.jobs.context import RENDER_VIDEO
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.worker import run_worker

# Video renders get their own process (`--kinds render_video --concurrency 1`).
ISOLATED_KINDS = frozenset({RENDER_VIDEO})


def _parse(argv: list[str] | None) -> tuple[list[str], int]:
    parser = argparse.ArgumentParser(prog="python -m linkedbuddy.worker")
    parser.add_argument("--kinds", help="comma-separated job kinds (default: all but video)")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    if args.kinds:
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    else:
        kinds = sorted(set(HANDLERS) - ISOLATED_KINDS)
    unknown = sorted(set(kinds) - set(HANDLERS))
    if unknown:
        parser.error(f"no handler registered for: {', '.join(unknown)}")
    if not kinds:
        parser.error("no job kinds registered")
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    return kinds, args.concurrency


async def _main(kinds: list[str], concurrency: int) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)  # stop claiming, drain in-flight jobs
    await run_worker(kinds, concurrency, stop)


def main(argv: list[str] | None = None) -> None:
    kinds, concurrency = _parse(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # SECURITY.md 7.3
    logging.getLogger(__name__).info("worker kinds=%s concurrency=%d", kinds, concurrency)
    asyncio.run(_main(kinds, concurrency))


if __name__ == "__main__":
    main()
