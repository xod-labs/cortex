# cortex-cli

The `cortex` command — [ADR-008](../docs/adr/ADR-008-cortex-binary.md).

It ships as a native binary, built with PyInstaller from this package and
[cortex-core](../core/README.md): the host needs no Python. This package is never
published on its own.

- **Standard library only**, and the core. The binary embeds nothing else — a test fails if a
  module imports anything more.
- **The core never imports it.** The command depends on the core, not the reverse — a test in
  the core's suite fails if it does.
- **Python 3.11 or later** for a development install, for `tomllib`. The binary embeds its own
  interpreter: the floor binds nobody else.

```bash
cd cli && python3 -m unittest discover -s tests   # nothing to install
pip install -e core -e cli                         # a `cortex` on PATH, from this checkout
```

The tests run the command from source, with nothing but `core/` and `cli/` on `sys.path`. With
`CORTEX_TEST_BINARY` naming a built binary, they run through it instead — which is how CI
checks each binary it builds. CI runs that replay with `tests/parallel.py`, which runs the same
tests in several workers at once and exits as `unittest` would:

```bash
cd cli && CORTEX_TEST_BINARY=build/dist/cortex python3 tests/parallel.py   # --workers N; twice the CPUs by default
```

## Version

`cortex --version` prints the version of the release the binary was built from, stamped by the
build with the version the release workflow names (ADR-009 §3.3). A build of no release prints the
version its branch prepares, as a pre-release (ADR-009 §9): `X.Y.Z-dev.N` from the CI, `N` its run's
number, which `build.py --development-version N` gives; `X.Y.Z-dev` from a source checkout, read
from its `CHANGELOG.md` — the highest release there, bumped by the `[Unreleased]` entries. The
tests name another with `CORTEX_SOURCE_VERSION`.
