"""The CLI's suite, run by several workers at once (#229).

    python tests/parallel.py [--workers N]     # from cli/, where `python -m unittest discover -s tests` runs

finds the suite as that command does and runs every test of it in one of N worker processes, twice
the machine's CPUs by default: a test that launches the binary mostly waits for it. A worker takes
the next test as soon as it is free, the heaviest first (``HEAVY``), so the run ends near the total
work divided by the workers, instead of waiting for one shard that drew the long tests.

It exits 0 when every test found ran and passed or was skipped, 1 on a failure, an error, a worker
that died or a test that never ran, and 2 when ``HEAVY`` names no test. A test runs in whichever
worker is free, after whichever tests that worker ran before: none of this suite leans on another.

The answers come back through multiprocessing's own pipes, never the standard output: the probe of
#229 lost tests in silence when a ``git`` that a test started wrote to the stdout its worker answered
on. What a test, or a process it starts, writes there goes to its worker's log, printed at the end.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# The tests that take longest, by the start of their id: they start first. Each must name a test, or
# the run stops: a rename would quietly put them last, and the run would end waiting for them alone.
HEAVY = ("test_cli_validate.GoldenTests.",)
RUNNING = "parallel.py running "

_tests: Dict[str, unittest.TestCase] = {}


def discover(start: str) -> List[unittest.TestCase]:
    """The suite as ``python -m unittest discover -s START`` finds it, one test per item."""
    def flatten(suite: unittest.TestSuite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item
    return list(flatten(unittest.TestLoader().discover(start)))


def order(ids: Sequence[str], heavy: Sequence[str]) -> List[str]:
    """``ids``, the heavy ones first, each group in the order of its ids."""
    return sorted(ids, key=lambda test_id: (not test_id.startswith(tuple(heavy)), test_id))


def _start_worker(start: str, logs: str) -> None:
    # The descriptors themselves, so that a process a test starts writes to the log too.
    log = open(Path(logs) / f"worker-{os.getpid()}.log", "w", encoding="utf-8", errors="replace")
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    sys.stdout = sys.stderr = log
    _tests.update((test.id(), test) for test in discover(start))


def _run(test_id: str) -> Tuple[str, float, str]:
    """One test, in this worker: ``ok``, ``skipped`` or ``failed``, its seconds, and its report."""
    print(RUNNING + test_id, flush=True)   # names the test, should the worker die under it
    report = io.StringIO()
    start = time.monotonic()
    result = unittest.TextTestRunner(stream=report, verbosity=2).run(unittest.TestSuite([_tests[test_id]]))
    outcome = "failed" if not result.wasSuccessful() else "skipped" if result.skipped else "ok"
    return outcome, time.monotonic() - start, report.getvalue()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workers", type=int, default=2 * (os.cpu_count() or 1))
    parser.add_argument("--start-dir", default="tests", help="where discovery starts, as unittest's -s (default: tests)")
    parser.add_argument("--heavy", action="append", metavar="ID_PREFIX", help="a test to start first, in place of HEAVY")
    args = parser.parse_args(argv)
    heavy = HEAVY if args.heavy is None else tuple(args.heavy)
    ids = [test.id() for test in discover(args.start_dir)]
    unmatched = [prefix for prefix in heavy if not any(test_id.startswith(prefix) for test_id in ids)]
    if unmatched:
        print(f"parallel.py: no test starts with {', '.join(unmatched)}: HEAVY must name the heaviest tests",
              file=sys.stderr)
        return 2

    start = time.monotonic()
    outcomes: Dict[str, str] = {}
    reports: Dict[str, str] = {}
    broken = False
    with tempfile.TemporaryDirectory(prefix="cortex-parallel-") as logs:
        with ProcessPoolExecutor(args.workers, initializer=_start_worker, initargs=(args.start_dir, logs)) as pool:
            # Submitted in order, taken in order: a worker that is free takes the next one.
            futures: Dict[Future, str] = {pool.submit(_run, test_id): test_id for test_id in order(ids, heavy)}
            for future in as_completed(futures):
                test_id = futures[future]
                try:
                    outcome, seconds, report = future.result()
                except BrokenProcessPool:
                    broken = True
                    continue
                except Exception as error:   # the worker could not run it at all
                    outcome, seconds, report = "failed", 0.0, f"{test_id}: {error!r}\n"
                outcomes[test_id] = outcome
                if outcome == "failed":
                    reports[test_id] = report
                print(f"{test_id} ... {outcome} ({seconds:.1f} s)", flush=True)
        logged = {path.name: path.read_text(encoding="utf-8", errors="replace")
                  for path in sorted(Path(logs).glob("worker-*.log"))}

    for test_id, report in sorted(reports.items()):
        print(f"\n{'=' * 70}\n{test_id}\n{report}", end="")
    for name, text in logged.items():
        output = [line for line in text.splitlines() if not line.startswith(RUNNING)]
        if output:
            print(f"\n--- what the tests of {name} and the processes they started wrote")
            print("\n".join(output))
    if broken:
        # The test a worker died under is the last one it started, which has no outcome.
        under = []
        for text in logged.values():
            started = [line[len(RUNNING):] for line in text.splitlines() if line.startswith(RUNNING)]
            if started and started[-1] not in outcomes:
                under.append(started[-1])
        print(f"\nA worker died, so the others were stopped. It was running one of: {', '.join(under) or 'none known'}.")

    failed = sorted(test_id for test_id, outcome in outcomes.items() if outcome == "failed")
    skipped = sum(outcome == "skipped" for outcome in outcomes.values())
    not_run = len(ids) - len(outcomes)
    print(f"\nRan {len(outcomes)} of {len(ids)} tests in {time.monotonic() - start:.1f} s, {args.workers} workers at once")
    if failed or not_run:
        print(f"FAILED ({len(failed)} failed, {not_run} not run)")
        for test_id in failed:
            print(f"  {test_id}")
        return 1
    print(f"OK (skipped={skipped})" if skipped else "OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
