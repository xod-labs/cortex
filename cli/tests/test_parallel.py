"""tests/parallel.py, the runner that replays the suite through the binary in CI (#229), on small
suites of its own: what passes, what fails, what a worker that dies leaves, and the heavy tests."""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parallel  # noqa: E402
from cli_harness import BINARY  # noqa: E402

TESTS = Path(__file__).resolve().parent


@unittest.skipIf(BINARY, "the runner's own tests: no binary in them")
class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-parallel-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "suite").mkdir()
        (self.tmp / "suite" / "__init__.py").write_text("", encoding="utf-8")

    def suite(self, name, body):
        (self.tmp / "suite" / name).write_text("import os, subprocess, sys, unittest\n\n" + textwrap.dedent(body),
                                               encoding="utf-8")

    def run_it(self, *args):
        done = subprocess.run([sys.executable, str(TESTS / "parallel.py"), "--start-dir", "suite", "--heavy", "test_a",
                               *args], cwd=self.tmp, capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def test_every_test_runs_and_passes(self):
        self.suite("test_a.py", """
            class A(unittest.TestCase):
                def test_one(self): pass
                def test_two(self): pass
                @unittest.skip("not here")
                def test_skipped(self): pass
            class B(unittest.TestCase):
                def test_three(self): pass
        """)
        code, out = self.run_it("--workers", "3")
        self.assertEqual(code, 0, out)
        self.assertIn("Ran 4 of 4 tests", out)
        self.assertIn("OK (skipped=1)", out)

    def test_a_failure_fails_the_run_and_says_why(self):
        self.suite("test_a.py", """
            class A(unittest.TestCase):
                def test_passes(self): pass
                def test_fails(self): self.fail("the reason it fails")
                def test_errs(self): raise RuntimeError("the reason it errs")
        """)
        code, out = self.run_it("--workers", "2")
        self.assertEqual(code, 1, out)
        self.assertIn("FAILED (2 failed, 0 not run)", out)
        self.assertIn("  failed: test_a.A.test_errs\n  failed: test_a.A.test_fails\n", out)
        self.assertIn("the reason it fails", out)
        self.assertIn("the reason it errs", out)

    def test_what_a_test_and_its_processes_write_loses_no_test(self):
        # The probe of #229 lost tests when a process a test started wrote to its worker's stdout.
        self.suite("test_a.py", """
            class A(unittest.TestCase):
                def test_prints(self): print("printed by a test")
                def test_starts_a_process(self):
                    subprocess.run([sys.executable, "-c", "import sys; print('written by a child'); sys.stderr.write('and to stderr')"])
                def test_then_another(self): print("after")
                def test_then_nothing(self): pass
        """)
        code, out = self.run_it("--workers", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("Ran 4 of 4 tests", out)
        # Each under the test it came from, the next test's too though the child wrote no newline.
        self.assertIn("--- written while test_a.A.test_prints ran\nprinted by a test\n", out)
        self.assertRegex(out, r"--- written while test_a\.A\.test_starts_a_process ran\n(?:[^\n]*\n)*?written by a child")
        self.assertIn("--- written while test_a.A.test_then_another ran\nafter\n", out)
        self.assertNotIn("test_a.A.test_then_nothing ran", out)

    def test_a_worker_that_dies_fails_the_run_and_names_its_test(self):
        self.suite("test_a.py", """
            class A(unittest.TestCase):
                def test_dies(self):
                    print("last words")
                    os._exit(3)
        """)
        self.suite("test_b.py", """
            class B(unittest.TestCase):
                def test_never_reached(self): pass
        """)
        code, out = self.run_it("--workers", "1")
        self.assertEqual(code, 1, out)
        self.assertIn("It was running one of: test_a.A.test_dies.", out)
        self.assertIn("--- written while test_a.A.test_dies ran\nlast words\n", out)
        self.assertIn("  not run: test_a.A.test_dies\n", out)
        self.assertIn("  not run: test_b.B.test_never_reached\n", out)

    def test_a_worker_that_dies_while_the_tests_are_submitted_fails_the_run_and_says_what_did_not_run(self):
        # The pool breaks before every test is submitted: the run says so, with no traceback.
        class Probe(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass

        tests = [Probe("test_one"), Probe("test_two")]

        class Pool:
            submitted = 0

            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def submit(self, function, test_id):
                Pool.submitted += 1
                if Pool.submitted > 1:
                    raise BrokenProcessPool("a worker died")
                future = Future()
                future.set_exception(BrokenProcessPool("a worker died"))
                return future

        out = io.StringIO()
        with mock.patch.object(parallel, "discover", return_value=tests), \
                mock.patch.object(parallel, "ProcessPoolExecutor", Pool), contextlib.redirect_stdout(out):
            code = parallel.main(["--heavy", tests[0].id(), "--workers", "1"])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("A worker died, so the others were stopped.", out.getvalue())
        self.assertIn(f"  not run: {tests[1].id()}\n", out.getvalue())

    def test_a_test_found_twice_runs_once(self):
        # A TestCase imported into another module is found there too, under the same id.
        self.suite("test_a.py", """
            class A(unittest.TestCase):
                def test_one(self): pass
        """)
        self.suite("test_b.py", """
            from test_a import A
        """)
        code, out = self.run_it("--workers", "2")
        self.assertEqual(code, 0, out)
        self.assertIn("Ran 1 of 1 tests", out)

    def test_the_heavy_tests_start_first(self):
        self.suite("test_a.py", """
            class A(unittest.TestCase):
                def test_heavy(self): pass
        """)
        self.suite("test_b.py", """
            class B(unittest.TestCase):
                def test_light(self): pass
        """)
        done = subprocess.run([sys.executable, str(TESTS / "parallel.py"), "--start-dir", "suite", "--heavy",
                               "test_b.B.", "--workers", "1"], cwd=self.tmp, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("test_b.B.test_light ... ok"), done.stdout)

    def test_a_heavy_name_that_matches_no_test_stops_the_run(self):
        self.suite("test_b.py", """
            class B(unittest.TestCase):
                def test_light(self): pass
        """)
        code, out = self.run_it()
        self.assertEqual(code, 2, out)
        self.assertIn("no test starts with test_a", out)

    def test_the_workers_are_counted_from_1_to_61(self):
        # ProcessPoolExecutor takes no more than 61 on Windows.
        self.suite("test_a.py", """
            class A(unittest.TestCase):
                def test_one(self): pass
        """)
        for workers in ("0", "62"):
            with self.subTest(workers=workers):
                code, out = self.run_it("--workers", workers)
                self.assertEqual(code, 2, out)
                self.assertIn("from 1 to 61", out)

    def test_heavy_names_tests_of_this_suite(self):
        # The run stops on a name that matches nothing; this says so on the pull request already.
        ids = [test.id() for test in parallel.discover(str(TESTS))]
        for prefix in parallel.HEAVY:
            with self.subTest(prefix=prefix):
                self.assertTrue(any(test_id.startswith(prefix) for test_id in ids))

    def test_the_heavy_tests_go_first_whatever_their_name(self):
        self.assertEqual(parallel.order(["a.x", "z.y", "m.z"], ["z."]), ["z.y", "a.x", "m.z"])


if __name__ == "__main__":
    unittest.main()
