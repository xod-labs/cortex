"""bin/check-english.sh in a throwaway repository: every tracked file is scanned, whatever its name."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check-english.sh"
NAME = "caf\u00e9.md"
FRENCH = "Les r\u00e8gles sont dans le fichier.\n"  # english-check:ignore — the sentence the check must catch


class CheckEnglishTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        (self.repo / "bin").mkdir()
        shutil.copy(SCRIPT, self.repo / "bin" / "check-english.sh")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def track(self, name, text):
        (self.repo / name).write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "--", name], cwd=self.repo, check=True)

    def check(self, *args):
        out = subprocess.run(["bash", "bin/check-english.sh", *args], cwd=self.repo, capture_output=True,
                             text=True, encoding="utf-8", env=dict(os.environ, LC_ALL="C.UTF-8"))
        return out.returncode, out.stdout

    def test_a_path_out_of_ascii_is_scanned(self):
        # git quotes such a path, as "caf\303\251.md", unless it lists NUL-separated (#227).
        self.track(NAME, FRENCH)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn(f"{NAME}:1:", out)
        self.assertEqual(self.check("--list"), (0, f"{NAME}\n"))

    def test_english_passes_under_any_name(self):
        self.track("na\u00efve notes.md", "The rules are in the file.\n")
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("1 tracked files scanned, all English.", out)


if __name__ == "__main__":
    unittest.main()
