"""The version of the command — the release's, stamped at build time (ADR-008 §3.1, ADR-009 §3.3).

``cli/build.py`` writes ``_stamp.py`` for the duration of a build: a release's version, or the one
a build of no release prepares, ``X.Y.Z-dev.N``. A source checkout has none: it reports the
version it prepares, ``X.Y.Z-dev``, which the checkout's ``CHANGELOG.md`` gives (``devversion``,
ADR-009 §9) — unless its tests name the version it stands for, with ``CORTEX_SOURCE_VERSION``. A
build always has a stamp, and reads neither. A checkout whose changelog cannot be read, an
installed copy of the package, says ``0.0.0-dev``.

The stamp also names the build's target, ``linux-x86_64`` say: the release asset ``cortex
self-update`` downloads. A source checkout has none.
"""

import os
from pathlib import Path


def _prepared() -> str:
    try:
        text = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "0.0.0-dev"
    from .devversion import development

    return development(text)


try:
    from ._stamp import TARGET, VERSION
except ImportError:
    TARGET = None
    VERSION = os.environ.get("CORTEX_SOURCE_VERSION") or _prepared()
