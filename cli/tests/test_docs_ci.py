"""The CI lines the docs give — the migration guide's and extending-layers' — run as written.

They read the version ``cortex.toml`` pins, to install that binary: read wrong, the version is
empty, and ``install.sh`` installs the latest release instead, in silence (ADR-008 §9). Then they
sync with ``--locked``, but for a version before 1.4.0, whose binary refuses the option.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
GUIDES = (DOCS / "migrating-to-the-binary.md", DOCS / "extending-layers.md")
PINS = {'version = "1.2.3"\ntheme = "h2g2"\n': "1.2.3",
        "version = '1.2.3'\ntheme = 'h2g2'\n": "1.2.3",
        '# version = "0.1.0"\n  version="1.2.3"   # the team pins it\n': "1.2.3",
        '\ufeffversion = "1.2.3"\ntheme = "h2g2"\n': "1.2.3",          # a byte order mark, as Notepad writes one
        'theme = "h2g2"\n': None}
# The version pinned, and whether its binary has `cortex sync --locked` (ADR-010 §3.3): 1.4.0 on.
LOCKED = {"1.0.0": False, "1.3.2": False, "0.9.0": False, "1.4.0": True, "1.4.0-rc.1": True, "1.10.0": True,
          "2.0.0": True}


def gate(path, language):
    """The line of ``path``'s CI block that leaves ``--locked`` out before 1.4.0."""
    return next(line for line in block(path, language) if "--locked from 1.4.0" in line)


def block(path, language):
    """The first ``language`` code block of ``path``'s CI section."""
    text = path.read_text(encoding="utf-8")
    section = text[text.index("CI"):]
    return re.search(rf"```{language}\n(.*?)```", section, re.S).group(1).splitlines()


class CiLinesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-docs-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_both_guides_give_the_same_lines(self):
        self.assertEqual(block(GUIDES[0], "bash")[:2], block(GUIDES[1], "bash")[:2])
        self.assertEqual(block(GUIDES[0], "powershell")[:3], block(GUIDES[1], "powershell")[:3])
        for language in ("bash", "powershell"):
            self.assertEqual(gate(GUIDES[0], language), gate(GUIDES[1], language))

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "a POSIX shell's lines")
    def test_the_shell_lines_sync_with_locked_from_1_4_0(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "cortex").write_text('#!/bin/sh\necho "$@"\n', encoding="utf-8")
        (bin_dir / "cortex").chmod(0o755)
        for guide in GUIDES:
            for version, locked in LOCKED.items():
                with self.subTest(guide=guide.name, version=version):
                    lines = f'version="{version}"\n{gate(guide, "bash")}\ncortex sync $locked\n'
                    proc = subprocess.run(["sh", "-c", lines], capture_output=True, text=True,
                                          env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout.strip(), "sync --locked" if locked else "sync")

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell's lines")
    def test_the_powershell_lines_sync_with_locked_from_1_4_0(self):
        for guide in GUIDES:
            for version, locked in LOCKED.items():
                with self.subTest(guide=guide.name, version=version):
                    lines = f"$version = '{version}'\n{gate(guide, 'powershell')}\nWrite-Output \"[$($locked -join ' ')]\"\n"
                    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", lines],
                                          capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout.strip(), "[--locked]" if locked else "[]")

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "a POSIX shell's lines")
    def test_the_shell_lines_read_the_pinned_version_or_stop(self):
        lines = "\n".join(block(GUIDES[0], "bash")[:2]) + '\necho "$version"\n'
        for toml, expected in PINS.items():
            with self.subTest(toml=toml):
                (self.tmp / "cortex.toml").write_text(toml, encoding="utf-8")
                proc = subprocess.run(["sh", "-c", lines], cwd=self.tmp, capture_output=True, text=True)
                if expected is None:
                    self.assertEqual(proc.returncode, 1)
                    self.assertIn("cortex.toml pins no version", proc.stderr)
                else:
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout.strip(), expected)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell's lines")
    def test_the_powershell_lines_read_the_pinned_version_or_stop(self):
        lines = "\n".join(block(GUIDES[0], "powershell")[:3]) + "\nWrite-Output $version\n"
        for toml, expected in PINS.items():
            with self.subTest(toml=toml):
                (self.tmp / "cortex.toml").write_text(toml, encoding="utf-8")
                proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", lines],
                                      cwd=self.tmp, capture_output=True, text=True)
                if expected is None:
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn("cortex.toml pins no version", proc.stderr)
                else:
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout.strip(), expected)


if __name__ == "__main__":
    unittest.main()
