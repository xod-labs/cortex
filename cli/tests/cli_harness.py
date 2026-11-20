"""How the CLI's tests run the ``cortex`` command.

From source by default: the interpreter running the tests, under ``-I -S``, with nothing but
``core/`` and ``cli/`` on ``sys.path`` — no working directory, no ``PYTHONPATH``, no
site-packages. With ``CORTEX_TEST_BINARY`` naming a built binary, the same tests run through it
instead (ADR-008 phase 1), and ``CORTEX_TEST_VERSION``, when set, is the version it must print.

Either way the command runs with a ``PATH`` that holds one empty directory: the binary must need
no Python on the host, and nothing else from ``PATH`` either. And with a ``CORTEX_HOME`` of the
suite's own, an empty directory, unless the test names one: no test reads or writes the store, or
the registry, of the machine it runs on — nor does a test that calls the modules: the suite's
process has that home too.
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parents[1]
REPO = CLI.parent
CORE = REPO / "core"
BINARY = os.environ.get("CORTEX_TEST_BINARY") or None
EXPECTED_VERSION = os.environ.get("CORTEX_TEST_VERSION") or None
SOURCE_CALL = ("import sys; sys.path[:0] = [sys.argv.pop(1), sys.argv.pop(1)]; "
               "from cortex_cli.main import main; sys.exit(main())")

# What the command may see of the environment: what an operating system needs to start a
# process, and where a user's files and temporary files are.
_KEPT = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA")
_EMPTY_PATH = tempfile.mkdtemp(prefix="cortex-empty-path-")
atexit.register(shutil.rmtree, _EMPTY_PATH, ignore_errors=True)
_HOME = tempfile.mkdtemp(prefix="cortex-home-")
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)
os.environ["CORTEX_HOME"] = _HOME


def _repository_above(directory):
    """A ``.git`` at ``directory`` or above it, as written or resolved — the repository Cortex takes a project made
    there to be in: the folder that holds it, or ``None``."""
    for start in {directory, os.path.realpath(directory)}:
        for folder in (start, *map(str, Path(start).parents)):
            if os.path.lexists(os.path.join(folder, ".git")):
                return folder
    return None


REPOSITORY_ABOVE_TEMP = _repository_above(tempfile.gettempdir())
# A test of a project in no repository makes it in the temporary directory: where that directory is in one — a TMPDIR
# inside a clone — the project is in it too, and the test is skipped, saying why.
outside_a_repository = unittest.skipIf(
    REPOSITORY_ABOVE_TEMP is not None,
    f"needs a temporary directory in no git repository: {REPOSITORY_ABOVE_TEMP} is one — set TMPDIR outside it")


def command(*args):
    if BINARY:
        return [BINARY, *args]
    return [sys.executable, "-I", "-S", "-c", SOURCE_CALL, str(CORE), str(CLI), *args]


def environment(**extra):
    env = {name: os.environ[name] for name in _KEPT if name in os.environ}
    env["PATH"] = _EMPTY_PATH
    env["CORTEX_HOME"] = _HOME
    env.update(extra)
    return env


def run(*args, cwd=None, env=None, stdin=subprocess.DEVNULL):
    """Run ``cortex ARGS`` in ``cwd`` and return the completed process, its output as bytes.

    ``PWD`` names ``cwd``, as a shell that changed into it would set it.
    """
    env = environment() if env is None else dict(env)
    if cwd is not None:
        env.setdefault("PWD", str(cwd))
    return subprocess.run(command(*args), cwd=cwd, env=env, stdin=stdin, capture_output=True)


# The templates of format 3, as 1.8.x ships them: what a project kept on 1.8.x reads from its own spec (ADR-014 §3.4).
FORMAT_3 = CLI / "tests" / "fixtures" / "format-3"


def text(template, version, theme="h2g2", folder="agents", spec=REPO, root=None):
    """``template`` as Cortex writes its text for a project reading ``version`` — rendered from format 4 with the theme,
    the team's folder and the Prompt Manager's character ``spec`` maps (ADR-014 §3.1), its personality block kept
    unless the theme is ``none`` — before the digest. ``root``, the project, where an overlay of the card may be."""
    from cortex_cli import init
    given = init.values_for(Path(root) if root else Path(_EMPTY_PATH), Path(spec), version, theme, folder)
    return init.instructions(init.rendered(template, given), theme != "none")


def written(template, version, theme="h2g2", folder="agents", spec=REPO, root=None):
    """``text``, with the digest in its marker: a bootstrap as Cortex writes it."""
    from cortex_cli import init
    return init.stamped(text(template, version, theme, folder, spec, root))
